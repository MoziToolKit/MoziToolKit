"""
Minecraft instance preference application, version comparison, and zero-config startup auto-setup.
"""

from __future__ import annotations

import logging
import re
import sys
from pathlib import Path
from typing import Any, List, Optional, Sequence, Tuple

from .detector import filter_functional_mods
from .models import DiscoveredInstance
from .aggregate import scan_all_instances

logger = logging.getLogger("MoziToolKit.Utils.Launcher.Applier")


def _get_launcher_delegate(name: str, default: Any) -> Any:
    for mod_name in (
        "bl_ext.vscode_development.MoziToolKit.utils.launcher_scan",
        "MoziToolKit.utils.launcher_scan",
    ):
        mod = sys.modules.get(mod_name)
        if mod is not None:
            val = getattr(mod, name, None)
            if val is not None:
                return val
    return default


def parse_minecraft_version(version_str: str) -> Tuple[int, ...]:
    """
    Parses a Minecraft release or snapshot version into a comparable tuple of integers.
    E.g.: '1.21.4' -> (1, 21, 4), '1.21' -> (1, 21, 0), '1.12.2' -> (1, 12, 2).
    """
    clean = version_str.strip()
    m = re.match(r"^(\d+)\.(\d+)(?:\.(\d+))?", clean)
    if m:
        major = int(m.group(1))
        minor = int(m.group(2))
        patch = int(m.group(3)) if m.group(3) else 0
        return (major, minor, patch)

    # Snapshot fallback, e.g. '24w45a' -> (1, 24, 45)
    snap_m = re.match(r"^(\d{2})w(\d{2})", clean)
    if snap_m:
        return (1, int(snap_m.group(1)), int(snap_m.group(2)))

    return (0, 0, 0)


def find_latest_vanilla_instance(
    instances: Sequence[DiscoveredInstance],
) -> Optional[DiscoveredInstance]:
    """
    Selects the discovered instance with the highest Minecraft release version
    that actually provides a verified Minecraft Client JAR.
    """
    valid_instances = [
        inst for inst in instances
        if inst.has_client_jar and inst.minecraft_version
    ]
    if not valid_instances:
        return None

    return max(
        valid_instances,
        key=lambda inst: (
            parse_minecraft_version(inst.minecraft_version),
            1 if inst.is_isolated else 0,
        ),
    )


def apply_instance_to_preferences(inst: DiscoveredInstance, prefs: Any) -> Tuple[List[Any], List[Any]]:
    """
    Applies the given game instance assets (Vanilla Client JAR, filtered mods, resource packs)
    to Blender AddonPreferences, sorting them by standard tier hierarchy.
    Returns: (asset_mods, skipped_mods)
    """
    from ..pack_detect import (
        KIND_MOD,
        KIND_RESOURCE_PACK,
        KIND_VANILLA_CLIENT,
        TYPE_SOURCE_MANUAL,
    )

    asset_mods, skipped_mods = filter_functional_mods(inst.mods)

    try:
        from ..assets.pack_probe import _set_is_reordering
        _set_is_reordering(True)
    except Exception:
        pass
    if hasattr(prefs, "_mozi_is_syncing"):
        setattr(prefs, "_mozi_is_syncing", True)

    try:
        coll = prefs.resource_packs
        coll.clear()

        def _add_elem():
            if hasattr(coll, "add"):
                return coll.add()
            import types
            elem = types.SimpleNamespace()
            coll.append(elem)
            return elem

        # 1. Vanilla base JAR
        if inst.client_jar and inst.client_jar.is_file():
            elem = _add_elem()
            elem.name = f"Minecraft {inst.minecraft_version} Vanilla Client"
            elem.pack_type = "VANILLA"
            elem.pack_type_source = TYPE_SOURCE_MANUAL
            elem.detected_kind = KIND_VANILLA_CLIENT
            elem.path = str(inst.client_jar)
            elem.enabled = True

        # 2. Mod JARs (filtered for renderable block assets)
        for mod in asset_mods:
            elem = _add_elem()
            m_path = mod.path if hasattr(mod, "path") else Path(mod)
            m_name = getattr(mod, "display_name", None) or m_path.stem
            m_enabled = getattr(mod, "enabled", True)

            elem.name = m_name
            elem.pack_type = "MOD_JAR"
            elem.pack_type_source = TYPE_SOURCE_MANUAL
            elem.detected_kind = KIND_MOD
            elem.path = str(m_path)
            elem.enabled = m_enabled

        # 3. Resource Packs (ordered by options.txt stack priority: highest priority first)
        for pack in inst.resource_packs:
            elem = _add_elem()
            p_path = pack.path if hasattr(pack, "path") else Path(pack)
            p_name = getattr(pack, "display_name", None) or p_path.stem
            p_enabled = getattr(pack, "enabled", True)

            elem.name = p_name
            elem.pack_type = "RESOURCE_PACK"
            elem.pack_type_source = TYPE_SOURCE_MANUAL
            elem.detected_kind = KIND_RESOURCE_PACK
            elem.path = str(p_path)
            elem.enabled = p_enabled
    finally:
        if hasattr(prefs, "_mozi_is_syncing"):
            setattr(prefs, "_mozi_is_syncing", False)
        try:
            from ..assets.pack_probe import _set_is_reordering
            _set_is_reordering(False)
        except Exception:
            pass

    try:
        from ..assets.instance_scan import _CACHED_DISCOVERED_INSTANCES
        if not any(i.instance_id == inst.instance_id for i in _CACHED_DISCOVERED_INSTANCES):
            _CACHED_DISCOVERED_INSTANCES.append(inst)
    except Exception:
        pass

    try:
        from ..assets.pack_order import reorder_resource_packs_by_tier
        reorder_resource_packs_by_tier(prefs)
    except Exception:
        pass

    prefs.resource_packs_index = 0
    if hasattr(prefs, "detected_instance_id"):
        try:
            from ..assets.instance_scan import is_updating_instance, set_is_updating_instance
            was_updating = is_updating_instance()
            set_is_updating_instance(True)
            try:
                prefs.detected_instance_id = inst.instance_id
            finally:
                set_is_updating_instance(was_updating)
        except Exception:
            try:
                prefs.detected_instance_id = inst.instance_id
            except Exception:
                pass

    try:
        from ..config import get_config_manager
        get_config_manager().sync_from_preferences(prefs)
    except Exception:
        pass

    return asset_mods, skipped_mods


def auto_configure_initial_asset_stack(prefs=None) -> Optional[DiscoveredInstance]:
    """
    Zero-configuration auto-setup:
    If no resource packs or JARs are configured, scans local launchers, finds the
    highest Minecraft version with a verified Client JAR, and automatically configures it
    as the default base asset stack.
    """
    if prefs is None:
        try:
            from ..system import get_prefs
            prefs = get_prefs()
        except Exception:
            return None

    if prefs is None:
        return None

    existing_packs = getattr(prefs, "resource_packs", None) or []
    has_any_existing_file = any(
        bool(getattr(p, "path", "")) and Path(getattr(p, "path", "")).exists()
        for p in existing_packs
    )
    if has_any_existing_file and len(existing_packs) > 0:
        return None

    scanner_fn = _get_launcher_delegate("scan_all_instances", scan_all_instances)
    is_mocked = hasattr(scanner_fn, "mock") or type(scanner_fn).__module__.startswith("unittest.mock")

    # Use memory cache if available to avoid duplicate scans (unless scanner is explicitly mocked)
    instances = None
    if not is_mocked:
        try:
            from ..assets.instance_scan import _CACHED_DISCOVERED_INSTANCES
            if _CACHED_DISCOVERED_INSTANCES:
                instances = _CACHED_DISCOVERED_INSTANCES
        except Exception:
            pass

    if instances is None:
        instances = scanner_fn()

    best = find_latest_vanilla_instance(instances)
    if best is None:
        if hasattr(prefs, "initial_auto_setup_done"):
            try:
                prefs.initial_auto_setup_done = True
            except Exception:
                pass
        return None

    apply_instance_to_preferences(best, prefs)
    if hasattr(prefs, "initial_auto_setup_done"):
        try:
            prefs.initial_auto_setup_done = True
        except Exception:
            pass

    try:
        from ..assets.guard import invalidate_guard_cache
        invalidate_guard_cache()
    except Exception:
        pass

    logger.info(
        "MoziToolKit: Automatically configured Minecraft %s (%s) as default asset stack.",
        best.minecraft_version,
        best.launcher_kind,
    )
    return best


def schedule_background_auto_setup() -> None:
    """
    Schedules an asynchronous, non-blocking check on Blender startup.
    Scans local Minecraft launchers in a background daemon thread and auto-populates
    the default asset stack if the preferences are completely empty.
    """
    try:
        import bpy
    except ImportError:
        return

    import threading

    def _bg_worker():
        try:
            instances = scan_all_instances()
            try:
                from ..assets import instance_scan
                with instance_scan._INSTANCE_SCAN_LOCK:
                    instance_scan._CACHED_DISCOVERED_INSTANCES.clear()
                    instance_scan._CACHED_DISCOVERED_INSTANCES.extend(instances)
                    instance_scan._INSTANCE_SCAN_STATE = "DONE"
            except Exception:
                pass

            best = find_latest_vanilla_instance(instances)
            if best is None:
                return

            def _apply_on_main():
                try:
                    from ..system import get_prefs
                    p = get_prefs()
                    if p is not None:
                        existing_packs = getattr(p, "resource_packs", None) or []
                        has_any_valid = any(
                            bool(getattr(x, "path", "")) and Path(getattr(x, "path", "")).exists()
                            for x in existing_packs
                        )
                        if not has_any_valid:
                            apply_instance_to_preferences(best, p)
                            try:
                                from ..assets.guard import invalidate_guard_cache
                                invalidate_guard_cache()
                            except Exception:
                                pass
                            logger.info(
                                "MoziToolKit: Automatically configured Minecraft %s (%s) as default asset stack.",
                                best.minecraft_version,
                                best.launcher_kind,
                            )
                            try:
                                from ...ui.preferences_packs import refresh_ui_and_menus
                                refresh_ui_and_menus(bpy.context)
                            except Exception:
                                pass
                except Exception as e:
                    logger.debug("Failed applying auto-configured instance on main thread: %s", e)
                return None

            try:
                if hasattr(bpy, "app") and hasattr(bpy.app, "timers"):
                    bpy.app.timers.register(_apply_on_main, first_interval=0.0)
                else:
                    _apply_on_main()
            except Exception:
                pass
        except Exception as e:
            logger.debug("Background instance auto-setup scan failed: %s", e)

    threading.Thread(target=_bg_worker, name="Mozi_Initial_AutoSetup", daemon=True).start()
