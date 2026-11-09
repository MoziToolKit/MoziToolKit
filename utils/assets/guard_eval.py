"""
MoziToolKit Asset Guard Evaluation Submodule.
Pure Python snapshots, configuration signatures, and vanilla pack validity probing.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from ...bridge.engine import get_libmtk
from ...i18n import tr
from ...utils.system import get_prefs
from .cache_utils import get_cache_dir

logger = logging.getLogger("MoziToolKit.Utils.Assets.GuardEval")

#: Host-level asset roots / archives honoured when no preferences are registered (CLI, CI).
ENV_ASSET_ROOTS = ("MC_DIR", "MC_ASSETS_DIR", "MTK_TEST_ASSETS")
ENV_ASSET_ARCHIVES = ("MTK_TEST_JAR", "MC_JAR")

#: Block identifiers probed by Rust core for canonical base foundation.
BASE_PROBE_BLOCKS = ("stone", "dirt", "oak_planks")

_VANILLA_PROBE_CACHE: Dict[Tuple[str, int, int], bool] = {}
_VANILLA_CHECK_CACHE: Dict[Any, bool] = {}


class PurePackEntry:
    """Pure Python representation of a pack entry, free of Blender RNA pointers."""
    def __init__(self, path: str = "", enabled: bool = True, pack_type: str = "RESOURCE_PACK", name: str = "", detected_kind: str = ""):
        self.path = str(path)
        self.enabled = bool(enabled)
        self.pack_type = str(pack_type)
        self.name = str(name)
        self.detected_kind = str(detected_kind)


class PackConfigSnapshot:
    """Pure Python snapshot of preferences configuration for thread-safe evaluation."""
    def __init__(self, resource_packs, resource_pack_path: str = "", cache_dir: str = "", thread_count: int = 0):
        self.resource_packs = list(resource_packs)
        self.resource_pack_path = str(resource_pack_path)
        self.cache_dir = str(cache_dir)
        self.thread_count = int(thread_count)


def snapshot_pack_config(prefs=None) -> PackConfigSnapshot:
    """Snapshot Blender AddonPreferences into a thread-safe pure Python DTO."""
    if prefs is None:
        try:
            prefs = get_prefs()
        except Exception:
            prefs = None

    packs = []
    if prefs is not None and hasattr(prefs, "resource_packs"):
        for e in prefs.resource_packs:
            packs.append(PurePackEntry(
                path=getattr(e, "path", "") or "",
                enabled=getattr(e, "enabled", True),
                pack_type=getattr(e, "pack_type", "RESOURCE_PACK"),
                name=getattr(e, "name", "") or "",
                detected_kind=getattr(e, "detected_kind", "") or "",
            ))

    single = str(getattr(prefs, "resource_pack_path", "") or "")
    try:
        resolved_cache_dir = str(get_cache_dir(prefs).resolve())
    except Exception:
        resolved_cache_dir = ""
    raw_threads = getattr(prefs, "thread_count", 0) if prefs is not None else 0
    thread_count = raw_threads if isinstance(raw_threads, int) and not isinstance(raw_threads, bool) else 0

    return PackConfigSnapshot(
        resource_packs=packs,
        resource_pack_path=single,
        cache_dir=resolved_cache_dir,
        thread_count=thread_count,
    )


def _get_pack_signature(prefs=None) -> tuple:
    if prefs is None:
        try:
            prefs = get_prefs()
        except Exception:
            prefs = None
    packs = getattr(prefs, "resource_packs", None) or []
    entries = []
    for e in packs:
        raw = getattr(e, "path", "") or ""
        enabled = getattr(e, "enabled", True)
        pt = getattr(e, "pack_type", "")
        mtime = 0.0
        if raw:
            try:
                mtime = os.path.getmtime(raw)
            except OSError:
                mtime = 0.0
        entries.append((raw, enabled, pt, mtime))
    single = getattr(prefs, "resource_pack_path", "") or ""
    single_mtime = 0.0
    if single:
        try:
            single_mtime = os.path.getmtime(single)
        except OSError:
            single_mtime = 0.0
    env_sig = tuple((k, os.environ.get(k, "")) for k in ENV_ASSET_ROOTS + ENV_ASSET_ARCHIVES)
    return (tuple(entries), single, single_mtime, env_sig)


def _path_provides_vanilla_client(path: Path) -> bool:
    """Single source of truth for 'can this path serve as the vanilla client foundation'.
    Delegates directly to libmtk native core (GEMINI.md Rule 1 & Rule 2).
    """
    mtk = get_libmtk()
    if mtk is None:
        raise RuntimeError("libmtk native core is required for vanilla client validation")
    try:
        if not path.exists():
            return False
        stat = path.stat()
        key = (str(path.resolve()), stat.st_mtime_ns, stat.st_size)
    except OSError:
        return False

    if key in _VANILLA_PROBE_CACHE:
        return _VANILLA_PROBE_CACHE[key]

    res = mtk.path_provides_vanilla_client(str(path.resolve()))
    _VANILLA_PROBE_CACHE[key] = res
    return res


def _is_entry_valid_vanilla_client(entry: Any) -> bool:
    """Whether a resource-pack entry is enabled and provides vanilla client assets."""
    if not getattr(entry, "enabled", True):
        return False
    raw_path = getattr(entry, "path", "")
    if not raw_path or not isinstance(raw_path, str):
        return False
    return _path_provides_vanilla_client(Path(raw_path.strip()))


def _has_configured_packs(prefs: Any) -> bool:
    """True when the preferences actually configure at least one pack entry."""
    for entry in getattr(prefs, "resource_packs", None) or []:
        raw_path = getattr(entry, "path", "")
        if isinstance(raw_path, str) and raw_path.strip():
            return True
    single_path = getattr(prefs, "resource_pack_path", None)
    return isinstance(single_path, str) and bool(single_path.strip())


def has_valid_vanilla_pack(prefs=None) -> bool:
    """
    Checks whether the active configuration can provide Minecraft client assets.
    """
    sig = _get_pack_signature(prefs)
    if sig in _VANILLA_CHECK_CACHE:
        return _VANILLA_CHECK_CACHE[sig]

    if prefs is None:
        try:
            prefs = get_prefs()
        except Exception:
            prefs = None

    result = False
    if prefs is not None:
        for entry in getattr(prefs, "resource_packs", None) or []:
            if _is_entry_valid_vanilla_client(entry):
                result = True
                break

        if not result:
            single_path = getattr(prefs, "resource_pack_path", None)
            if isinstance(single_path, str) and single_path.strip():
                if _path_provides_vanilla_client(Path(single_path.strip())):
                    result = True

        # A stack is configured but none of it can back the pipeline: fail closed.
        if not result and _has_configured_packs(prefs):
            _VANILLA_CHECK_CACHE[sig] = False
            return False

    if not result:
        # No packs configured (CLI / CI / first run): the host environment may point at
        # unpacked client assets or a client JAR, still subject to the content check.
        for env_var in ENV_ASSET_ROOTS + ENV_ASSET_ARCHIVES:
            val = os.environ.get(env_var)
            if val and _path_provides_vanilla_client(Path(val)):
                result = True
                break

    _VANILLA_CHECK_CACHE[sig] = result
    return result


def can_execute_asset_pipeline(prefs=None, check_status_fn=None) -> Tuple[bool, str]:
    """
    Evaluates whether the asset pipeline (meshing, material replacement, precompilation) can be executed.
    Returns (can_execute, reason_message).
    When False, reason_message provides a clear, user-facing explanation suitable for UI tooltips or reports.
    """
    if not has_valid_vanilla_pack(prefs):
        status = check_status_fn(prefs) if check_status_fn else "NO_VANILLA"
        if status == "NO_PACKS":
            return False, tr("No Minecraft resource packs configured. Please configure a Vanilla Client JAR in Preferences.")
        return False, tr("A Minecraft Vanilla Client JAR is required. Resource packs and mods cannot be used without a vanilla base pack.")
    return True, ""
