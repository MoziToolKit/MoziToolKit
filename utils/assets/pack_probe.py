"""
Background resource pack and JAR content probing pipeline, worker threads, and debounced application.
"""

from __future__ import annotations

import logging
import os
import queue
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import bpy

from ..config import get_config_manager
from ..pack_detect import (
    KIND_MOD,
    KIND_VANILLA_CLIENT,
    KIND_VANILLA_SERVER,
    TYPE_SOURCE_AUTO,
    PackProbe,
    detect_pack_kind,
    invalidate_pack_probe,
    resolve_pack_type,
)
from ...ui.preferences_utils import _safe_get_prefs, refresh_ui_and_menus

logger = logging.getLogger("MoziToolKit.Utils.PackProbe")

#: Debounce before probing, so typing a path does not open the archive on every keystroke.
PROBE_DEBOUNCE_SECONDS = 0.15
#: Timer cadence while probes are scheduled or in flight.
PROBE_TICK_SECONDS = 0.1
#: Give up waiting on a wedged probe (slow network share) and allow a retry.
PROBE_TIMEOUT_SECONDS = 10.0

_probe_lock = threading.Lock()
_probe_scheduled: Dict[str, float] = {}
_probe_inflight: Dict[str, float] = {}
_probe_results: "queue.Queue[Tuple[str, PackProbe]]" = queue.Queue()

#: Fallback guard storage for hosts without ``bpy.types.WindowManager`` (headless tests).
_GUARD_FALLBACK = {"reordering": False, "probing": False}


def _guard_host():
    return getattr(getattr(bpy, "types", None), "WindowManager", None)


def _get_probe_delegate(name: str, default: Any) -> Any:
    for mod_name in (
        "bl_ext.vscode_development.MoziToolKit.ui.preferences_packs",
        "MoziToolKit.ui.preferences_packs",
    ):
        mod = sys.modules.get(mod_name)
        if mod is not None:
            val = getattr(mod, name, None)
            if val is not None:
                return val
    return default


def _get_is_reordering() -> bool:
    host = _guard_host()
    if host is None:
        return _GUARD_FALLBACK["reordering"]
    return bool(getattr(host, "_mozi_is_reordering_packs", False))


def _set_is_reordering(val: bool) -> None:
    host = _guard_host()
    if host is None:
        _GUARD_FALLBACK["reordering"] = bool(val)
    else:
        setattr(host, "_mozi_is_reordering_packs", bool(val))


def _is_probing() -> bool:
    host = _guard_host()
    if host is None:
        return _GUARD_FALLBACK["probing"]
    return bool(getattr(host, "_mozi_is_probing_pack", False))


def _set_is_probing(val: bool) -> None:
    host = _guard_host()
    if host is None:
        _GUARD_FALLBACK["probing"] = bool(val)
    else:
        setattr(host, "_mozi_is_probing_pack", bool(val))


def _callbacks_suppressed() -> bool:
    if _get_is_reordering() or _is_probing():
        return True
    try:
        return bool(get_config_manager().is_syncing())
    except Exception:
        return False


def _stem_title(raw_path: str) -> str:
    try:
        return Path(raw_path).stem.replace("_", " ").replace("-", " ").title()
    except Exception:
        return "Resource Pack"


def _looks_auto_named(current_name: str, raw_path: str) -> bool:
    """True when the display name was never customized by the user."""
    name = (current_name or "").strip()
    if not name or name.startswith("Resource Pack") or name == "New Resource Pack":
        return True
    return name == _stem_title(raw_path)


def _auto_name_for(raw_path: str, probe: Optional[PackProbe]) -> Optional[str]:
    """Derive a friendlier name from detected metadata (version string / mod display name)."""
    if probe is None:
        return None
    if probe.kind == KIND_VANILLA_CLIENT and probe.details.get("mc_version"):
        return f"Minecraft {probe.details['mc_version']}"
    if probe.kind == KIND_VANILLA_SERVER and probe.details.get("mc_version"):
        return f"Minecraft Server {probe.details['mc_version']}"
    if probe.kind == KIND_MOD and probe.details.get("mod_name"):
        return str(probe.details["mod_name"])
    return None


def apply_probe_to_entry(item: Any, probe: Optional[PackProbe]) -> bool:
    """
    Write a probe verdict onto a pack entry. Returns True when anything changed.

    Manual type choices are preserved; only the recorded ``detected_kind`` is refreshed.
    """
    if item is None or probe is None:
        return False

    was_probing = _is_probing()
    _set_is_probing(True)
    try:
        return _apply_probe_fields(item, probe)
    finally:
        _set_is_probing(was_probing)


def _apply_probe_fields(item: Any, probe: PackProbe) -> bool:
    changed = False
    source = getattr(item, "pack_type_source", TYPE_SOURCE_AUTO) or TYPE_SOURCE_AUTO
    current_type = getattr(item, "pack_type", "RESOURCE_PACK")

    if getattr(item, "detected_kind", "") != probe.kind:
        setattr(item, "detected_kind", probe.kind)
        changed = True

    resolved_type = resolve_pack_type(current_type, source, probe)
    if resolved_type != current_type:
        setattr(item, "pack_type", resolved_type)
        changed = True

    raw_path = (getattr(item, "path", "") or "").strip()
    if raw_path and _looks_auto_named(getattr(item, "name", ""), raw_path):
        better_name = _auto_name_for(raw_path, probe)
        if better_name and better_name != getattr(item, "name", ""):
            setattr(item, "name", better_name)
            changed = True
    return changed


def probe_entry_now(item: Any) -> Optional[PackProbe]:
    """Detect synchronously and apply to one entry (used by the explicit re-detect operator)."""
    raw_path = (getattr(item, "path", "") or "").strip()
    if not raw_path:
        return None
    invalidate_pack_probe(raw_path)
    probe = detect_pack_kind(raw_path)
    apply_probe_to_entry(item, probe)
    return probe


def _timers_available() -> bool:
    try:
        timers = bpy.app.timers
        if type(timers).__module__.startswith("unittest.mock"):
            return False
        return hasattr(timers, "register") and hasattr(timers, "is_registered")
    except Exception:
        return False


def _probe_worker(key: str) -> None:
    try:
        probe = detect_pack_kind(key)
    except Exception as exc:  # defensive: never kill the worker with an exception
        logger.warning("pack probe failed for %s: %s", key, exc)
        probe = PackProbe(error=f"{type(exc).__name__}: {exc}")
    _probe_results.put((key, probe))
    with _probe_lock:
        _probe_inflight.pop(key, None)


def _start_probe(key: str) -> None:
    with _probe_lock:
        if key in _probe_inflight:
            return
        _probe_inflight[key] = time.monotonic()
    threading.Thread(target=_probe_worker, args=(key,), name="MTK-PackProbe", daemon=True).start()


def schedule_pack_probe(raw_path: str) -> None:
    """Debounce + schedule a content probe for *raw_path* (never blocks the caller)."""
    key = (raw_path or "").strip()
    if not key:
        return
    timers_avail = _get_probe_delegate("_timers_available", _timers_available)
    if not timers_avail():
        # Non-Blender host (tests, mock runs): probe and apply synchronously.
        _apply_probe_result(key, detect_pack_kind(key))
        return
    with _probe_lock:
        _probe_scheduled[key] = time.monotonic() + PROBE_DEBOUNCE_SECONDS
    try:
        if not bpy.app.timers.is_registered(_pack_probe_tick):
            bpy.app.timers.register(_pack_probe_tick, first_interval=PROBE_TICK_SECONDS)
    except Exception:
        logger.debug("failed to register pack probe timer", exc_info=True)


def _apply_probe_result(key: str, probe: PackProbe) -> bool:
    """Apply one finished probe on the main thread. Returns True when anything changed."""
    context = getattr(bpy, "context", None)
    safe_get = _get_probe_delegate("_safe_get_prefs", _safe_get_prefs)
    prefs = safe_get(context)
    if prefs is None:
        return False
    changed = False
    for item in getattr(prefs, "resource_packs", []):
        if (getattr(item, "path", "") or "").strip() != key:
            continue
        if apply_probe_to_entry(item, probe):
            changed = True
    if changed:
        from .pack_order import reorder_resource_packs_by_tier
        reorder_resource_packs_by_tier(prefs)
        get_cfg = _get_probe_delegate("get_config_manager", get_config_manager)
        get_cfg().sync_from_preferences(prefs)
        refresh = _get_probe_delegate("refresh_ui_and_menus", refresh_ui_and_menus)
        refresh(context)
        try:
            from .guard import invalidate_guard_cache
            invalidate_guard_cache()
        except Exception:
            pass
    return changed


def schedule_pending_pack_detection(prefs) -> int:
    """
    Queue content probes for entries that never got a verdict (config migration / first run).

    Called once per session from the preferences panel; every probe is debounced and runs off the
    main thread, so opening the panel never stalls on a stack of large JARs.
    """
    if prefs is None:
        return 0
    scheduled = 0
    try:
        items = list(getattr(prefs, "resource_packs", []))
    except TypeError:
        return 0
    probe_fn = _get_probe_delegate("schedule_pack_probe", schedule_pack_probe)
    for item in items:
        if getattr(item, "pack_type_source", TYPE_SOURCE_AUTO) != TYPE_SOURCE_AUTO:
            continue
        if (getattr(item, "detected_kind", "") or "").strip():
            continue
        raw = (getattr(item, "path", "") or "").strip()
        if not raw or not Path(raw).exists():
            continue
        probe_fn(raw)
        scheduled += 1
    return scheduled


def _drain_probe_results() -> bool:
    """Drain the worker queue. Returns True when anything changed."""
    changed_any = False
    while True:
        try:
            key, probe = _probe_results.get_nowait()
        except queue.Empty:
            return changed_any
        if _apply_probe_result(key, probe):
            changed_any = True


def _pack_probe_tick() -> Optional[float]:
    """Main-thread timer: dispatch due probes and apply finished ones."""
    try:
        _drain_probe_results()
        now = time.monotonic()
        due: list = []
        with _probe_lock:
            for key, deadline in list(_probe_scheduled.items()):
                if deadline <= now:
                    _probe_scheduled.pop(key, None)
                    due.append(key)
            for key, started in list(_probe_inflight.items()):
                if now - started > PROBE_TIMEOUT_SECONDS:
                    _probe_inflight.pop(key, None)
                    logger.warning("pack probe timed out for %s", key)
        for key in due:
            if os.path.exists(key):
                _start_probe(key)
        with _probe_lock:
            busy = bool(_probe_scheduled or _probe_inflight)
    except Exception:
        logger.warning("pack probe timer error", exc_info=True)
        return None
    return PROBE_TICK_SECONDS if busy or not _probe_results.empty() else None
