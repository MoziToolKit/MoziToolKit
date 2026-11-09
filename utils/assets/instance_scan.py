"""
Background discovery state and scheduling for local Minecraft launcher instances.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any, Callable, List, Optional

import bpy

from .pack_probe import _timers_available
from ...ui.preferences_utils import _safe_get_prefs, refresh_ui_and_menus

logger = logging.getLogger("MoziToolKit.Utils.InstanceScan")

# Discovered Minecraft instances cache and user-supplied directories
_CACHED_DISCOVERED_INSTANCES: List[Any] = []
_CUSTOM_DIRECTORIES: List[Path] = []
_INSTANCE_SCAN_STATE: str = "IDLE"  # "IDLE", "SCANNING", "DONE", "ERROR"
_INSTANCE_SCAN_LOCK = threading.Lock()
_ACTIVE_SCAN_THREAD: Optional[threading.Thread] = None

# Flag to guard against feedback loops when updating instance from preferences
_IS_UPDATING_INSTANCE: bool = False


def is_updating_instance() -> bool:
    return _IS_UPDATING_INSTANCE


def set_is_updating_instance(val: bool) -> None:
    global _IS_UPDATING_INSTANCE
    _IS_UPDATING_INSTANCE = bool(val)


def wait_for_background_scan(timeout: float = 5.0) -> None:
    """Wait for any in-flight background instance scanning thread to finish."""
    global _ACTIVE_SCAN_THREAD
    thread = _ACTIVE_SCAN_THREAD
    if thread is not None and thread.is_alive():
        thread.join(timeout=timeout)


def schedule_background_instance_scan(
    force: bool = False,
    custom_dirs: Optional[List[Path]] = None,
    on_complete: Optional[Callable[[List[Any]], None]] = None,
) -> bool:
    """
    Asynchronously scans system directories for Minecraft instances on a daemon background thread.
    Zero main thread blocking. Automatically selects the latest vanilla instance if none is selected,
    and requests an asynchronous UI redraw upon completion.
    """
    global _INSTANCE_SCAN_STATE, _CACHED_DISCOVERED_INSTANCES
    with _INSTANCE_SCAN_LOCK:
        if _INSTANCE_SCAN_STATE == "SCANNING" and not force:
            return False
        if _INSTANCE_SCAN_STATE == "DONE" and _CACHED_DISCOVERED_INSTANCES and not force:
            return False
        _INSTANCE_SCAN_STATE = "SCANNING"

    dirs_to_scan = list(custom_dirs if custom_dirs is not None else _CUSTOM_DIRECTORIES)

    if not _timers_available():
        # Headless testing mode / synchronous fallback
        try:
            from ..launcher_scan import scan_all_instances
            instances = scan_all_instances(dirs_to_scan)
            with _INSTANCE_SCAN_LOCK:
                _CACHED_DISCOVERED_INSTANCES.clear()
                _CACHED_DISCOVERED_INSTANCES.extend(instances)
                _INSTANCE_SCAN_STATE = "DONE"
            if on_complete:
                on_complete(instances)
            return True
        except Exception as exc:
            logger.warning("Synchronous instance scan failed: %s", exc)
            with _INSTANCE_SCAN_LOCK:
                _INSTANCE_SCAN_STATE = "ERROR"
            return False

    def _worker():
        global _CACHED_DISCOVERED_INSTANCES, _INSTANCE_SCAN_STATE
        try:
            from ..launcher_scan import scan_all_instances
            instances = scan_all_instances(dirs_to_scan)
            with _INSTANCE_SCAN_LOCK:
                _CACHED_DISCOVERED_INSTANCES.clear()
                _CACHED_DISCOVERED_INSTANCES.extend(instances)
                _INSTANCE_SCAN_STATE = "DONE"
        except Exception as exc:
            logger.warning("Background instance scan failed: %s", exc)
            with _INSTANCE_SCAN_LOCK:
                _INSTANCE_SCAN_STATE = "ERROR"
            return

        def _on_main_thread():
            try:
                context = getattr(bpy, "context", None)
                prefs = _safe_get_prefs(context)
                if prefs is not None and _CACHED_DISCOVERED_INSTANCES:
                    from ..launcher_scan import find_latest_vanilla_instance
                    best = find_latest_vanilla_instance(_CACHED_DISCOVERED_INSTANCES)
                    if best is None and _CACHED_DISCOVERED_INSTANCES:
                        best = _CACHED_DISCOVERED_INSTANCES[0]

                    # Auto-populate resource_packs only if in zero-configuration state
                    existing_packs = getattr(prefs, "resource_packs", None) or []
                    has_any_valid = any(
                        bool(getattr(p, "path", "")) and Path(getattr(p, "path", "")).exists()
                        for p in existing_packs
                    )
                    if not has_any_valid and best is not None:
                        from ..launcher_scan import apply_instance_to_preferences
                        apply_instance_to_preferences(best, prefs)
                        try:
                            from .guard import invalidate_guard_cache
                            invalidate_guard_cache()
                        except Exception:
                            pass
                    else:
                        target_id = getattr(prefs, "detected_instance_id", "NONE")
                        if (target_id in ("NONE", "SCANNING") or not any(i.instance_id == target_id for i in _CACHED_DISCOVERED_INSTANCES)) and best is not None:
                            set_is_updating_instance(True)
                            try:
                                prefs.detected_instance_id = best.instance_id
                            except Exception:
                                pass
                            finally:
                                set_is_updating_instance(False)
                refresh_ui_and_menus(context)
                if on_complete:
                    on_complete(_CACHED_DISCOVERED_INSTANCES)
            except Exception as e:
                logger.debug("Failed updating preferences after instance scan: %s", e)
            return None

        try:
            bpy.app.timers.register(_on_main_thread, first_interval=0.0)
        except Exception:
            pass

    global _ACTIVE_SCAN_THREAD
    t = threading.Thread(target=_worker, name="MTK-InstanceScan", daemon=True)
    _ACTIVE_SCAN_THREAD = t
    t.start()
    return True
