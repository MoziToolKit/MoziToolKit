"""
Blender operators for managing resource packs, mod JARs, and Minecraft instances in Addon Preferences.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any
import bpy
from bpy.props import EnumProperty, StringProperty

from ...ui.preferences_utils import _safe_get_prefs, refresh_ui_and_menus
from ...utils.config import get_config_manager
from ...utils.pack_detect import TYPE_SOURCE_AUTO
from ...utils.assets.instance_scan import (
    _CACHED_DISCOVERED_INSTANCES,
    _CUSTOM_DIRECTORIES,
    schedule_background_instance_scan,
)
from ...utils.assets.pack_probe import (
    _set_is_probing,
    _timers_available,
    probe_entry_now,
)
from ...utils.assets.pack_order import reorder_resource_packs_by_tier


def _get_ui_delegate(name: str, default: Any) -> Any:
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


class MOZI_OT_pack_add(bpy.types.Operator):
    """Add a new resource pack or Minecraft/Mod JAR entry to the fallback stack"""

    bl_idname = "mozi.pack_add"
    bl_label = "Add Pack or JAR"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        prefs = _get_ui_delegate("_safe_get_prefs", _safe_get_prefs)(context)
        if prefs is None:
            return {"CANCELLED"}
        if not getattr(prefs, "is_initialized", False):
            _get_ui_delegate("get_config_manager", get_config_manager)().sync_to_preferences(prefs)

        _set_is_probing(True)
        try:
            coll = prefs.resource_packs
            elem = coll.add()
            elem.name = f"Resource Pack #{len(coll)}"
            elem.enabled = True
            elem.pack_type = "RESOURCE_PACK"
            if hasattr(elem, "pack_type_source"):
                elem.pack_type_source = TYPE_SOURCE_AUTO
            if hasattr(elem, "detected_kind"):
                elem.detected_kind = ""
            # New packs enter at the very top of the stack (highest resolution priority),
            # matching Minecraft's "newly added pack overrides everything below it" model.
            if len(coll) > 1:
                coll.move(len(coll) - 1, 0)
        finally:
            _set_is_probing(False)
        reorder_resource_packs_by_tier(prefs)
        prefs.resource_packs_index = 0

        _get_ui_delegate("get_config_manager", get_config_manager)().sync_from_preferences(prefs)
        _get_ui_delegate("refresh_ui_and_menus", refresh_ui_and_menus)(context)
        return {"FINISHED"}


class MOZI_OT_pack_remove(bpy.types.Operator):
    """Remove selected pack from the fallback stack"""

    bl_idname = "mozi.pack_remove"
    bl_label = "Remove Pack or JAR"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        prefs = _get_ui_delegate("_safe_get_prefs", _safe_get_prefs)(context)
        if prefs is None:
            return {"CANCELLED"}
        idx = prefs.resource_packs_index
        if 0 <= idx < len(prefs.resource_packs):
            prefs.resource_packs.remove(idx)
            reorder_resource_packs_by_tier(prefs)
            prefs.resource_packs_index = max(0, min(idx, len(prefs.resource_packs) - 1))
            _get_ui_delegate("get_config_manager", get_config_manager)().sync_from_preferences(prefs)
            _get_ui_delegate("refresh_ui_and_menus", refresh_ui_and_menus)(context)
        return {"FINISHED"}


class MOZI_OT_pack_move(bpy.types.Operator):
    """Move selected pack up or down to adjust resolution priority within its tier"""

    bl_idname = "mozi.pack_move"
    bl_label = "Move Pack Priority"
    bl_options = {"REGISTER", "UNDO"}

    direction: EnumProperty(
        items=[("UP", "Up", "Increase Priority"), ("DOWN", "Down", "Decrease Priority")],
        default="UP",
    )

    def execute(self, context):
        prefs = _get_ui_delegate("_safe_get_prefs", _safe_get_prefs)(context)
        if prefs is None:
            return {"CANCELLED"}
        idx = prefs.resource_packs_index
        if not (0 <= idx < len(prefs.resource_packs)):
            return {"CANCELLED"}

        curr_item = prefs.resource_packs[idx]
        curr_tier = curr_item.pack_type

        if self.direction == "UP" and idx > 0:
            target_item = prefs.resource_packs[idx - 1]
            if target_item.pack_type != curr_tier:
                self.report({'INFO'}, f"Cannot move {curr_tier.replace('_', ' ').title()} above {target_item.pack_type.replace('_', ' ').title()} tier.")
                return {"CANCELLED"}
            prefs.resource_packs.move(idx, idx - 1)
            prefs.resource_packs_index = idx - 1
            _get_ui_delegate("get_config_manager", get_config_manager)().sync_from_preferences(prefs)
            _get_ui_delegate("refresh_ui_and_menus", refresh_ui_and_menus)(context)
        elif self.direction == "DOWN" and idx < len(prefs.resource_packs) - 1:
            target_item = prefs.resource_packs[idx + 1]
            if target_item.pack_type != curr_tier:
                self.report({'INFO'}, f"Cannot move {curr_tier.replace('_', ' ').title()} below {target_item.pack_type.replace('_', ' ').title()} tier.")
                return {"CANCELLED"}
            prefs.resource_packs.move(idx, idx + 1)
            prefs.resource_packs_index = idx + 1
            _get_ui_delegate("get_config_manager", get_config_manager)().sync_from_preferences(prefs)
            _get_ui_delegate("refresh_ui_and_menus", refresh_ui_and_menus)(context)
        return {"FINISHED"}


class MOZI_OT_pack_detect(bpy.types.Operator):
    """Re-scan this archive and let MoziToolKit classify its pack type from its contents"""

    bl_idname = "mozi.pack_detect"
    bl_label = "Re-detect Pack Type"
    bl_description = "Inspect the selected .jar/.zip/folder content and choose its pack type automatically"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        prefs = _get_ui_delegate("_safe_get_prefs", _safe_get_prefs)(context)
        if prefs is None:
            return {"CANCELLED"}
        idx = prefs.resource_packs_index
        if not (0 <= idx < len(prefs.resource_packs)):
            return {"CANCELLED"}

        item = prefs.resource_packs[idx]
        raw = (item.path or "").strip()
        if not raw or not Path(raw).exists():
            self.report({'ERROR'}, "Select an existing .jar/.zip archive or pack folder first")
            return {"CANCELLED"}

        _set_is_probing(True)
        try:
            if hasattr(item, "pack_type_source"):
                item.pack_type_source = TYPE_SOURCE_AUTO
        finally:
            _set_is_probing(False)

        probe = probe_entry_now(item)

        reorder_resource_packs_by_tier(prefs)
        _get_ui_delegate("get_config_manager", get_config_manager)().sync_from_preferences(prefs)
        _get_ui_delegate("refresh_ui_and_menus", refresh_ui_and_menus)(context)

        if probe is None:
            self.report({'WARNING'}, "Detection did not run")
            return {"CANCELLED"}
        if not probe.is_known:
            self.report({'WARNING'}, f"Could not identify this archive ({probe.error or 'no signature matched'})")
            return {"FINISHED"}
        self.report({'INFO'}, f"Detected {probe.label()} ({probe.confidence * 100:.0f}% confidence)")
        return {"FINISHED"}


class MOZI_OT_scan_instances(bpy.types.Operator):
    """Scan local launchers for Minecraft game instances"""

    bl_idname = "mozi.scan_instances"
    bl_label = "Scan Local Instances"
    bl_description = "Scan system directories for Official, Prism, Modrinth, and CurseForge game instances"
    bl_options = {"REGISTER"}

    def execute(self, context):
        global _CACHED_DISCOVERED_INSTANCES
        if _timers_available():
            schedule_background_instance_scan(force=True)
            self.report({'INFO'}, "Scanning local Minecraft instances in background...")
            _get_ui_delegate("refresh_ui_and_menus", refresh_ui_and_menus)(context)
            return {'FINISHED'}

        from ...utils.launcher_scan import scan_all_instances
        try:
            _CACHED_DISCOVERED_INSTANCES = scan_all_instances(_CUSTOM_DIRECTORIES)
            self.report({'INFO'}, f"Discovered {len(_CACHED_DISCOVERED_INSTANCES)} game instances")
        except Exception as e:
            self.report({'ERROR'}, f"Failed scanning instances: {e}")
            return {'CANCELLED'}
        _get_ui_delegate("refresh_ui_and_menus", refresh_ui_and_menus)(context)
        return {'FINISHED'}


class MOZI_OT_pick_custom_instance_dir(bpy.types.Operator):
    """Pick a custom game directory (e.g. HMCL or portable folder)"""

    bl_idname = "mozi.pick_custom_instance_dir"
    bl_label = "Add Custom Game Folder"
    bl_description = "Pick an external or portable Minecraft game directory to discover its instances"
    bl_options = {"REGISTER"}

    directory: StringProperty(
        name="Game Directory",
        subtype="DIR_PATH",
        description="Select a folder containing .minecraft, versions/, or instance.cfg",
    )

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        if not self.directory or not Path(self.directory).is_dir():
            self.report({'WARNING'}, "Please select a valid directory")
            return {'CANCELLED'}

        target = Path(self.directory)
        if target not in _CUSTOM_DIRECTORIES:
            _CUSTOM_DIRECTORIES.append(target)

        from ...utils.launcher_scan import scan_custom_directory

        found = scan_custom_directory(target)
        if not found:
            self.report({'WARNING'}, f"No valid Minecraft instances found in '{target.name}'")
            return {'CANCELLED'}

        for inst in found:
            if not any(i.instance_id == inst.instance_id for i in _CACHED_DISCOVERED_INSTANCES):
                _CACHED_DISCOVERED_INSTANCES.append(inst)

        prefs = _get_ui_delegate("_safe_get_prefs", _safe_get_prefs)(context)
        if prefs is not None and found:
            try:
                if hasattr(prefs, "detected_instances_launcher_filter") and prefs.detected_instances_launcher_filter not in ("ALL", "CUSTOM"):
                    prefs.detected_instances_launcher_filter = "CUSTOM"
                prefs.detected_instance_id = found[0].instance_id
                from ...utils.launcher_scan import apply_instance_to_preferences
                apply_instance_to_preferences(found[0], prefs)
                try:
                    from ...utils.assets.guard import invalidate_guard_cache
                    invalidate_guard_cache()
                except Exception:
                    pass
            except Exception:
                pass

        self.report({'INFO'}, f"Added {len(found)} instance(s) from '{target.name}'")
        _get_ui_delegate("refresh_ui_and_menus", refresh_ui_and_menus)(context)
        return {'FINISHED'}


OPERATORS_PREFERENCES_PACKS = [
    MOZI_OT_pack_add,
    MOZI_OT_pack_remove,
    MOZI_OT_pack_move,
    MOZI_OT_pack_detect,
    MOZI_OT_scan_instances,
    MOZI_OT_pick_custom_instance_dir,
]
