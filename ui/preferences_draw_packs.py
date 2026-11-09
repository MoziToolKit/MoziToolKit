"""
Resource packs and Base JARs preferences tab UI layout.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
import bpy

from ..i18n import tr
from ..utils.assets import has_valid_vanilla_pack
from .preferences_packs import detected_kind_warning


def draw_resource_packs_tab(prefs: Any, layout: Any, context: Any) -> None:
    """Renders the Resource Packs & Base JARs tab inside addon preferences."""
    # -------------------------------------------------------------------
    # 1. Primary Streamlined Instance Selector
    # -------------------------------------------------------------------
    inst_box = layout.box()
    h_row = inst_box.row(align=True)
    h_row.label(text=tr("Game Instance & Auto-Ingestion:"), icon="CONSOLE")
    h_row.operator("mozi.scan_instances", text=tr("Scan / Refresh"), icon="FILE_REFRESH")
    h_row.operator("mozi.pick_custom_instance_dir", text=tr("Add Folder..."), icon="FILE_FOLDER")

    sel_col = inst_box.column(align=False)
    r_filter = sel_col.row(align=True)
    r_filter.prop(prefs, "detected_instances_launcher_filter", text=tr("Launcher"))
    r_filter.prop(prefs, "detected_instance_id", text=tr("Instance"))

    curr_inst = None
    scan_state = "IDLE"
    try:
        from .preferences_packs import _CACHED_DISCOVERED_INSTANCES, _INSTANCE_SCAN_STATE
        scan_state = _INSTANCE_SCAN_STATE
        target_id = getattr(prefs, "detected_instance_id", "NONE")
        if target_id not in ("NONE", "SCANNING") and _CACHED_DISCOVERED_INSTANCES:
            curr_inst = next((i for i in _CACHED_DISCOVERED_INSTANCES if i.instance_id == target_id), None)
            if curr_inst is None:
                try:
                    idx = int(target_id)
                    if 0 <= idx < len(_CACHED_DISCOVERED_INSTANCES):
                        curr_inst = _CACHED_DISCOVERED_INSTANCES[idx]
                except Exception:
                    pass
    except Exception:
        curr_inst = None

    if curr_inst:
        info_sub = inst_box.box()
        i_row = info_sub.row(align=True)
        iso_label = tr("Isolated Instance") if curr_inst.is_isolated else tr("Global Shared")
        en_mods = len(curr_inst.enabled_mods) if hasattr(curr_inst, "enabled_mods") else len(curr_inst.mods)
        mods_info = f"{tr('Mods')}: {en_mods}/{len(curr_inst.mods)}" if en_mods != len(curr_inst.mods) else f"{tr('Mods')}: {len(curr_inst.mods)}"
        if getattr(curr_inst, "skipped_functional_mods", None):
            mods_info += f" ({tr('Filtered')}: {len(curr_inst.skipped_functional_mods)})"

        en_packs = len(curr_inst.enabled_resource_packs) if hasattr(curr_inst, "enabled_resource_packs") else len(curr_inst.resource_packs)
        packs_info = f"{tr('Packs')}: {en_packs}/{len(curr_inst.resource_packs)}" if en_packs != len(curr_inst.resource_packs) else f"{tr('Packs')}: {len(curr_inst.resource_packs)}"

        i_row.label(
            text=f"{tr('Version')}: {curr_inst.minecraft_version} ({curr_inst.loader}) | {iso_label} | {mods_info} | {packs_info}",
            icon="CHECKMARK" if curr_inst.has_client_jar else "INFO",
        )
        if not curr_inst.has_client_jar:
            w_row = info_sub.row(align=True)
            w_row.alert = True
            w_row.label(text=tr("Client JAR not found in instance. Run the game once or add manual JAR."), icon="ERROR")
    elif scan_state == "SCANNING":
        info_sub = inst_box.box()
        i_row = info_sub.row(align=True)
        i_row.label(text=tr("Scanning local Minecraft launchers in background..."), icon="TIME")
    else:
        act_row = inst_box.row(align=True)
        act_row.scale_y = 1.2
        act_row.operator("mozi.scan_instances", text=tr("Scan System for Minecraft Instances"), icon="VIEWZOOM")

    layout.separator()

    # -------------------------------------------------------------------
    # 2. Stack Precompilation & Cache
    # -------------------------------------------------------------------
    cache_box = layout.box()
    c_head = cache_box.row(align=True)
    c_head.label(text=tr("Asset Prebake & Cache:"), icon="FILE_CACHE")

    try:
        from ..utils.assets import get_ui_asset_cache_status
        status = get_ui_asset_cache_status(prefs)
    except Exception:
        status = "UNKNOWN"

    st_row = cache_box.row(align=True)
    st_row.scale_y = 0.9
    if status == "CHECKING":
        st_row.label(text=tr("Cache Status: Checking freshness..."), icon="TIME")
    elif status == "READY":
        st_row.label(text=tr("Cache Status: Ready and up-to-date"), icon="CHECKMARK")
    elif status == "STALE":
        st_row.alert = True
        st_row.label(text=tr("Cache Status: Outdated (Stack changed, recompile recommended)"), icon="TIME")
    elif status == "MISSING":
        st_row.alert = True
        st_row.label(text=tr("Cache Status: Missing (Not precompiled yet)"), icon="INFO")
    elif status == "NO_VANILLA":
        st_row.alert = True
        st_row.label(text=tr("Cache Status: Disabled (Minecraft Vanilla Client JAR required)"), icon="ERROR")
    else:
        st_row.label(text=tr("Cache Status: No resource packs configured"), icon="INFO")

    cache_row = cache_box.row(align=True)
    cache_row.operator("mozi.precompile_cache", text=tr("Precompile / Rebuild Stack Caches"), icon="FILE_REFRESH")
    cache_row.operator("mozi.open_cache_folder", text=tr("Open Cache Folder"), icon="FOLDER_REDIRECT")
    cache_row.operator("mozi.clear_cache", text=tr("Clear Cache"), icon="TRASH")

    layout.separator()

    # -------------------------------------------------------------------
    # 3. Advanced Collapsible Manual Stack
    # -------------------------------------------------------------------
    adv_box = layout.box()
    adv_row = adv_box.row(align=True)
    is_advanced_open = bool(getattr(prefs, "show_advanced_packs", False))
    icon_tria = "TRIA_DOWN" if is_advanced_open else "TRIA_RIGHT"
    adv_row.prop(prefs, "show_advanced_packs", icon=icon_tria, text=tr("Advanced: Manual Resource Pack & JAR Stack"))

    if is_advanced_open:
        has_vanilla = status not in ("NO_VANILLA", "NO_PACKS") if status != "CHECKING" else has_valid_vanilla_pack(prefs)
        if not has_vanilla:
            warn_box = adv_box.box()
            warn_box.alert = True
            w_row = warn_box.row(align=True)
            w_row.label(text=tr("Minecraft Vanilla Client JAR Required!"), icon="ERROR")
            w_col = warn_box.column(align=True)
            w_col.scale_y = 0.85
            w_col.label(text=tr("Please add and enable a Minecraft Vanilla Client JAR (.jar) at the bottom tier."))

        list_row = adv_box.row(align=False)
        list_b = list_row.box()
        list_b.template_list(
            "MOZI_UL_resource_packs_list",
            "",
            prefs,
            "resource_packs",
            prefs,
            "resource_packs_index",
            rows=5,
        )

        idx = prefs.resource_packs_index
        can_move_up = False
        can_move_down = False
        if 0 <= idx < len(prefs.resource_packs):
            curr_tier = prefs.resource_packs[idx].pack_type
            if idx > 0 and prefs.resource_packs[idx - 1].pack_type == curr_tier:
                can_move_up = True
            if idx < len(prefs.resource_packs) - 1 and prefs.resource_packs[idx + 1].pack_type == curr_tier:
                can_move_down = True

        btn_col = list_row.column(align=True)
        btn_col.operator("mozi.pack_add", text="", icon="ADD")
        btn_col.operator("mozi.pack_remove", text="", icon="REMOVE")
        btn_col.separator(factor=2)

        up_col = btn_col.column(align=True)
        up_col.enabled = can_move_up
        op_up = up_col.operator("mozi.pack_move", text="", icon="TRIA_UP")
        op_up.direction = "UP"

        down_col = btn_col.column(align=True)
        down_col.enabled = can_move_down
        op_down = down_col.operator("mozi.pack_move", text="", icon="TRIA_DOWN")
        op_down.direction = "DOWN"

        if 0 <= idx < len(prefs.resource_packs):
            item = prefs.resource_packs[idx]
            detail_box = adv_box.box()
            d_header = detail_box.row(align=True)
            d_header.label(text=f"{tr('Pack Details')}: {item.name}", icon="PREFERENCES")
            d_header.prop(item, "enabled", text=tr("Enabled"))

            d_col = detail_box.column(align=False)
            row1 = d_col.row(align=True)
            row1.prop(item, "name", text=tr("Name"))
            row1.prop(item, "pack_type", text=tr("Type"))

            row2 = d_col.row(align=True)
            row2.prop(item, "path", text=tr("Path"))

            p_val = item.path.strip()
            if not p_val:
                st_row = d_col.row(align=True)
                st_row.alert = True
                st_row.label(text=tr("Path is empty. Please select a .zip, .jar, or assets folder."), icon="INFO")
            elif not Path(p_val).exists():
                st_row = d_col.row(align=True)
                st_row.alert = True
                st_row.label(text=f"{tr('File not found')}: '{p_val}'", icon="ERROR")

            kind_warning = detected_kind_warning(item)
            if kind_warning:
                warn_row = d_col.row(align=True)
                warn_row.alert = True
                warn_row.label(text=kind_warning, icon="ERROR")
