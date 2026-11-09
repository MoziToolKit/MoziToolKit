"""
Resource pack preferences PropertyGroup, UIList, and UI presentation callbacks.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, List, Optional, Tuple

import bpy
from bpy.props import BoolProperty, EnumProperty, StringProperty

from ..i18n import tr
from ..utils.config import get_config_manager
from ..utils.pack_detect import (
    KIND_DATA_PACK,
    KIND_LABELS,
    KIND_MOD,
    KIND_MODPACK,
    KIND_RESOURCE_PACK,
    KIND_UNKNOWN,
    KIND_VANILLA_CLIENT,
    KIND_VANILLA_SERVER,
    TYPE_SOURCE_AUTO,
    TYPE_SOURCE_MANUAL,
    invalidate_pack_probe,
    peek_cached_probe,
)
from .preferences_utils import _safe_get_prefs, refresh_ui_and_menus

# Extracted utils & operators re-exports for clean modularity & backward compatibility
from ..utils.assets.instance_scan import (
    _CACHED_DISCOVERED_INSTANCES,
    _CUSTOM_DIRECTORIES,
    _INSTANCE_SCAN_LOCK,
    _INSTANCE_SCAN_STATE,
    is_updating_instance,
    schedule_background_instance_scan,
    set_is_updating_instance,
    wait_for_background_scan,
)
from ..utils.assets.pack_probe import (
    _auto_name_for,
    _callbacks_suppressed,
    _get_is_reordering,
    _guard_host,
    _is_probing,
    _looks_auto_named,
    _pack_probe_tick,
    _probe_inflight,
    _probe_lock,
    _probe_results,
    _probe_scheduled,
    _set_is_probing,
    _set_is_reordering,
    _start_probe,
    _stem_title,
    _timers_available,
    apply_probe_to_entry,
    probe_entry_now,
    schedule_pack_probe,
    schedule_pending_pack_detection,
)
from ..utils.assets.pack_order import reorder_resource_packs_by_tier
from ..operators.preferences.op_packs import (
    MOZI_OT_pack_add,
    MOZI_OT_pack_remove,
    MOZI_OT_pack_move,
    MOZI_OT_pack_detect,
    MOZI_OT_scan_instances,
    MOZI_OT_pick_custom_instance_dir,
)

logger = logging.getLogger("MoziToolKit.UI.PreferencesPacks")

_KIND_ICONS = {
    KIND_VANILLA_CLIENT: "WORLD_DATA",
    KIND_VANILLA_SERVER: "WORLD_DATA",
    KIND_MOD: "MODIFIER",
    KIND_RESOURCE_PACK: "PACKAGE",
    KIND_DATA_PACK: "FILE",
    KIND_MODPACK: "FILE_ARCHIVE",
    KIND_UNKNOWN: "QUESTION",
}


def get_detected_instances_enum_items(self, context) -> List[Tuple[str, str, str, str, int]]:
    """Dynamic callback providing the list of discovered Minecraft instances (reads memory cache only, zero disk I/O)."""
    global _CACHED_DISCOVERED_INSTANCES, _INSTANCE_SCAN_STATE

    if not _CACHED_DISCOVERED_INSTANCES:
        if _INSTANCE_SCAN_STATE == "IDLE":
            schedule_background_instance_scan()
        if _INSTANCE_SCAN_STATE == "SCANNING":
            return [("SCANNING", "Scanning Local Instances...", "Scanning background directories for installations", "TIME", 0)]
        return [("NONE", "No Instances Detected", "Click 'Scan Local Instances' to search for game directories", "QUESTION", 0)]

    items: List[Tuple[str, str, str, str, int]] = []
    _tr = tr

    def launcher_sort_key(launcher_kind: str) -> int:
        order = {"PRISM": 0, "MODRINTH": 1, "OFFICIAL": 2, "CURSEFORGE": 3, "CUSTOM": 4}
        return order.get(launcher_kind, 5)

    sorted_instances = sorted(
        _CACHED_DISCOVERED_INSTANCES,
        key=lambda inst: (launcher_sort_key(inst.launcher_kind), inst.name.lower())
    )

    for idx, inst in enumerate(sorted_instances):
        icon = "WINDOW"
        if inst.launcher_kind == "OFFICIAL":
            icon = "WORLD_DATA"
        elif inst.launcher_kind == "PRISM":
            icon = "COLOR"
        elif inst.launcher_kind == "MODRINTH":
            icon = "FILE_VOLUME"
        elif inst.launcher_kind == "CURSEFORGE":
            icon = "OUTLINER_OB_LIGHT"
        elif inst.launcher_kind == "CUSTOM":
            icon = "FILE_FOLDER"

        label = f"[{inst.launcher_kind}] {inst.name}"
        version = getattr(inst, "minecraft_version", "") or getattr(inst, "mc_version", "")
        if version:
            label += f" ({version})"

        num_packs = len(inst.resource_packs)
        num_mods = len(inst.mods)
        desc = f"{inst.game_dir} • {_tr('Pack Count')}: {num_packs} • {_tr('Mod Count')}: {num_mods}"
        items.append((inst.instance_id, label, desc, icon, idx + 1))

    return items


def on_detected_instance_changed(self, context) -> None:
    """When the user explicitly changes the detected instance dropdown in preferences."""
    if is_updating_instance() or _callbacks_suppressed():
        return

    instance_id = getattr(self, "detected_instance_id", "NONE")
    if instance_id in ("NONE", "SCANNING"):
        return

    target = next((inst for inst in _CACHED_DISCOVERED_INSTANCES if inst.instance_id == instance_id), None)
    if target is None:
        return

    set_is_updating_instance(True)
    try:
        from ..utils.launcher_scan import apply_instance_to_preferences
        apply_instance_to_preferences(target, self)
        try:
            from ..utils.assets.guard import invalidate_guard_cache
            invalidate_guard_cache()
        except Exception:
            pass
        refresh_ui_and_menus(context)
    finally:
        set_is_updating_instance(False)


def on_pack_entry_changed(self, context):
    """Callback when a resource pack entry's attributes change."""
    if _callbacks_suppressed():
        return
    prefs = _safe_get_prefs(self)
    if prefs:
        get_config_manager().sync_from_preferences(prefs)
        refresh_ui_and_menus(context)
        try:
            from ..bridge.assets import invalidate_asset_cache_memory
            from ..bridge.world import clear_world_sessions

            invalidate_asset_cache_memory()
            clear_world_sessions()
        except Exception:
            pass


def on_pack_type_changed(self, context):
    """Callback when a resource pack entry's pack_type tier changes."""
    if _callbacks_suppressed():
        return
    _set_is_probing(True)
    try:
        if getattr(self, "pack_type_source", TYPE_SOURCE_AUTO) != TYPE_SOURCE_MANUAL:
            self.pack_type_source = TYPE_SOURCE_MANUAL
    finally:
        _set_is_probing(False)

    prefs = _safe_get_prefs(self)
    if prefs:
        reorder_resource_packs_by_tier(prefs)
        get_config_manager().sync_from_preferences(prefs)
        refresh_ui_and_menus(context)
        try:
            from ..utils.assets.guard import invalidate_guard_cache
            invalidate_guard_cache()
        except Exception:
            pass


def on_pack_type_source_changed(self, context):
    """Callback when the user switches between AUTO and MANUAL type resolution."""
    if _callbacks_suppressed():
        return
    if getattr(self, "pack_type_source", TYPE_SOURCE_AUTO) == TYPE_SOURCE_AUTO:
        raw = (getattr(self, "path", "") or "").strip()
        if raw:
            invalidate_pack_probe(raw)
            if Path(raw).exists():
                schedule_pack_probe(raw)
    prefs = _safe_get_prefs(self)
    if prefs:
        get_config_manager().sync_from_preferences(prefs)
        refresh_ui_and_menus(context)


def on_pack_path_changed(self, context):
    """Auto-fill the display name and schedule content-based pack type detection."""
    if _callbacks_suppressed():
        return
    try:
        raw = (self.path or "").strip()
        if raw and Path(raw).exists():
            _set_is_probing(True)
            try:
                self.pack_type_source = TYPE_SOURCE_AUTO
                self.detected_kind = ""
            finally:
                _set_is_probing(False)

            if _looks_auto_named(self.name, raw):
                self.name = _stem_title(raw)
            schedule_pack_probe(raw)
    except Exception:
        pass
    prefs = _safe_get_prefs(self)
    if prefs:
        reorder_resource_packs_by_tier(prefs)
        get_config_manager().sync_from_preferences(prefs)
        refresh_ui_and_menus(context)
        try:
            from ..utils.assets.guard import invalidate_guard_cache
            invalidate_guard_cache()
        except Exception:
            pass


def describe_detected_kind(item: Any) -> Tuple[str, str]:
    """Return ``(label, icon)`` describing the last content-detection verdict."""
    kind = (getattr(item, "detected_kind", "") or "").strip()
    if not kind:
        return tr("Not detected yet"), "INFO"
    label = tr(KIND_LABELS.get(kind, kind))
    probe = peek_cached_probe((getattr(item, "path", "") or "").strip())
    if probe is not None and probe.kind == kind and probe.kind != KIND_UNKNOWN:
        label = f"{label}  ({probe.confidence * 100:.0f}%)"
    return label, _KIND_ICONS.get(kind, "INFO")


def detected_kind_warning(item: Any) -> Optional[str]:
    """Return a user-facing warning when the detected archive cannot feed the bake pipeline."""
    kind = (getattr(item, "detected_kind", "") or "").strip()
    if kind in {KIND_DATA_PACK, KIND_MODPACK}:
        return tr("This archive provides no textures for baking (data pack / modpack).")
    probe = peek_cached_probe((getattr(item, "path", "") or "").strip())
    if probe is not None and probe.details.get("archive_root"):
        return tr("Pack is nested inside a folder; Minecraft will not load it as-is.")
    return None


class MOZI_PG_resource_pack_entry(bpy.types.PropertyGroup):
    __slots__ = ()

    name: StringProperty(
        name="Name",
        description="Display name for this pack/JAR",
        default="New Resource Pack",
        update=on_pack_entry_changed,
    )
    path: StringProperty(
        name="Path",
        description="File path to .zip/.jar archive or extracted directory",
        subtype="FILE_PATH",
        default="",
        update=on_pack_path_changed,
    )
    enabled: BoolProperty(
        name="Enabled",
        description="Enable this pack in the fallback resolution stack",
        default=True,
        update=on_pack_entry_changed,
    )
    pack_type: EnumProperty(
        name="Pack Type",
        description="Classification of this asset source",
        items=[
            ("RESOURCE_PACK", "Resource Pack", "Standard ZIP or folder resource pack (Overrides base assets)"),
            ("MOD_JAR", "Mod JAR", "Mod JAR archive (Fabric/Forge/NeoForge) containing assets"),
            ("VANILLA", "Vanilla Client JAR", "Minecraft vanilla client JAR archive containing textures and models (Base foundation)"),
        ],
        default="RESOURCE_PACK",
        update=on_pack_type_changed,
    )
    pack_type_source: EnumProperty(
        name="Type Source",
        description="AUTO classifies the archive content and may update the type; MANUAL keeps your choice",
        items=[
            ("AUTO", "Auto", "Detect the pack type from archive content and keep it in sync"),
            ("MANUAL", "Manual", "Keep the pack type exactly as selected, ignoring auto-detection"),
        ],
        default=TYPE_SOURCE_AUTO,
        update=on_pack_type_source_changed,
    )
    detected_kind: StringProperty(
        name="Detected Kind",
        description="Last content-detection verdict (Vanilla Client, Mod, Resource Pack, ...)",
        default="",
    )


class MOZI_UL_resource_packs_list(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        if self.layout_type in {"DEFAULT", "COMPACT"}:
            row = layout.row(align=True)
            row.prop(item, "enabled", text="", emboss=False)
            _tr = tr

            if item.pack_type == "VANILLA":
                icon_type = "WORLD_DATA"
                tier_badge = _tr("Vanilla Base")
            elif item.pack_type == "MOD_JAR":
                icon_type = "MODIFIER"
                mod_idx = sum(1 for i, elem in enumerate(data.resource_packs) if elem.pack_type == "MOD_JAR" and i <= index)
                tier_badge = f"{_tr('Mod')} #{mod_idx}"
            else:
                icon_type = "PACKAGE"
                rp_idx = sum(1 for i, elem in enumerate(data.resource_packs) if elem.pack_type == "RESOURCE_PACK" and i <= index)
                tier_badge = f"{_tr('RP')} #{rp_idx}"

            is_valid = bool(item.path and Path(item.path).exists())
            name_text = item.name or (Path(item.path).stem if item.path else "Unnamed Pack")

            if not is_valid and item.path:
                row.label(text=f"{name_text} {_tr('(Missing File)')}", icon="ERROR")
            elif not item.path:
                row.label(text=f"{name_text} {_tr('(No Path)')}", icon="QUESTION")
            else:
                row.label(text=name_text, icon=icon_type)

            p_badge = row.row(align=True)
            p_badge.alignment = "RIGHT"
            p_badge.enabled = False
            p_badge.label(text=f"{tier_badge}")
        elif self.layout_type == "GRID":
            layout.alignment = "CENTER"
            layout.label(text=item.name, icon="PACKAGE")


def populate_resource_packs(prefs, entries):
    """Safely populate prefs.resource_packs from a list of dicts without reorder race conditions."""
    if prefs is None:
        return
    _set_is_reordering(True)
    try:
        setattr(prefs, "_mozi_is_syncing", True)
        prefs.resource_packs.clear()
        for p_item in entries:
            if isinstance(p_item, dict):
                p_elem = prefs.resource_packs.add()
                p_elem.name = p_item.get("name", "Resource Pack")
                p_elem.path = p_item.get("path", "")
                p_elem.enabled = p_item.get("enabled", True)
                p_elem.pack_type = p_item.get("pack_type", "RESOURCE_PACK")
                if hasattr(p_elem, "pack_type_source"):
                    p_elem.pack_type_source = p_item.get("pack_type_source", TYPE_SOURCE_AUTO)
                if hasattr(p_elem, "detected_kind"):
                    p_elem.detected_kind = p_item.get("detected_kind", "")
    finally:
        setattr(prefs, "_mozi_is_syncing", False)
        _set_is_reordering(False)

    reorder_resource_packs_by_tier(prefs)


PACKS_CLASSES = [
    MOZI_PG_resource_pack_entry,
    MOZI_UL_resource_packs_list,
    MOZI_OT_pack_add,
    MOZI_OT_pack_remove,
    MOZI_OT_pack_move,
    MOZI_OT_pack_detect,
    MOZI_OT_scan_instances,
    MOZI_OT_pick_custom_instance_dir,
]
