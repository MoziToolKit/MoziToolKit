"""
Material, Context Menu, and Performance/Misc preferences tabs UI layout.
"""

from __future__ import annotations

import os
from typing import Any
import bpy

from ..i18n import tr
from ..utils.config import get_config_manager
from .preferences_menus import (
    MOZI_OT_menu_add_item,
    MOZI_OT_menu_remove_item,
    MOZI_OT_menu_move_item,
    MOZI_OT_menu_reset_config,
    MOZI_OT_menu_export_config,
    MOZI_OT_menu_import_config,
)


def draw_material_tab(prefs: Any, layout: Any, context: Any) -> None:
    """Renders the Material & Shading tab inside addon preferences."""
    curr_engine = getattr(context.scene.render, "engine", "UNKNOWN") if context and context.scene else "UNKNOWN"
    is_eevee = curr_engine in {"BLENDER_EEVEE", "BLENDER_EEVEE_NEXT"}

    info_box = layout.box()
    b_row = info_box.row(align=True)
    b_row.label(text=tr("Material & PBR Shading Configuration:"), icon="SHADING_RENDERED")

    st_row = info_box.row(align=True)
    st_row.scale_y = 0.9
    engine_mode_text = tr("EEVEE Mode") if is_eevee else tr("Cycles Mode")
    st_row.label(text=f"{tr('Active Scene Engine')}: {curr_engine} ({engine_mode_text})", icon="RESTRICT_RENDER_OFF")

    layout.separator()

    # Group 1: Default Material Replacement Settings
    box_repl = layout.box()
    b_repl_head = box_repl.row(align=True)
    b_repl_head.label(text=tr("Default Material Replacement Settings:"), icon="MATERIAL")
    col_repl = box_repl.column(align=False)
    col_repl.prop(prefs, "pack_textures")
    repl_row = col_repl.row(align=True)
    repl_row.prop(prefs, "enable_ctm", text=tr("Connected Textures (CTM)"))
    repl_row.prop(prefs, "enable_animated_textures", text=tr("Animated Textures"))
    col_repl.prop(prefs, "smooth_biome_blend", text=tr("Smooth Biome Transition"))
    col_repl.prop(prefs, "material_enable_vertex_ao", text=tr("Vertex Ambient Occlusion (AO)"))

    # Group 2: Render Engine Adaptation & Presets
    layout.separator()
    box_engine = layout.box()
    b1_head = box_engine.row(align=True)
    b1_head.label(text=tr("Render Engine Adaptation & Presets:"), icon="SCENE")
    col_eng = box_engine.column(align=False)
    col_eng.prop(prefs, "material_render_engine", text=tr("Adaptation Mode"))
    hint_eng = col_eng.row(align=True)
    hint_eng.scale_y = 0.85
    if prefs.material_render_engine == "AUTO":
        hint_eng.label(text=tr("• Auto Mode: Optimizes alpha and subsurface settings for the active engine."))
    elif prefs.material_render_engine == "EEVEE":
        hint_eng.label(text=tr("• EEVEE Mode: Realtime-friendly alpha blending and optional subsurface override."))
    else:
        hint_eng.label(text=tr("• Cycles Mode: Full physical subsurface scattering where the material provides it."))

    # Group 3: Subsurface Scattering
    layout.separator()
    box_sss = layout.box()
    b4_head = box_sss.row(align=True)
    b4_head.label(text=tr("Subsurface Scattering (Leaves / Plants / Crops):"), icon="OUTLINER_OB_CURVES")
    col_sss = box_sss.column(align=False)
    col_sss.prop(prefs, "material_subsurface_method", text=tr("Subsurface Method"))
    col_sss.prop(prefs, "material_disable_subsurface", text=tr("Disable Subsurface Scattering"))

    layout.separator()
    row_pre = layout.row(align=True)
    row_pre.operator("mozi.precompile_cache", text=tr("Rebuild / Precompile Stack Caches"), icon="FILE_REFRESH")
    row_pre.operator("mozi.open_cache_folder", text=tr("Open Cache Folder"), icon="FOLDER_REDIRECT")


def draw_context_menus_tab(prefs: Any, layout: Any, context: Any) -> None:
    """Renders the Context Menu Presets tab inside addon preferences."""
    sub_row = layout.row(align=True)
    sub_row.prop(prefs, "context_menu_tab", expand=True)

    layout.separator()

    top_box = layout.box()
    top_row = top_box.row(align=True)
    op_reset_cur = top_row.operator(MOZI_OT_menu_reset_config.bl_idname, text=tr("Reset Current View"), icon="FILE_REFRESH")
    op_reset_cur.reset_all = False
    op_reset_all = top_row.operator(MOZI_OT_menu_reset_config.bl_idname, text=tr("Reset All Views"), icon="RECOVER_LAST")
    op_reset_all.reset_all = True
    top_row.operator(MOZI_OT_menu_import_config.bl_idname, text=tr("Import Presets JSON..."), icon="IMPORT")
    top_row.operator(MOZI_OT_menu_export_config.bl_idname, text=tr("Export Presets JSON..."), icon="EXPORT")

    layout.separator()

    view = prefs.context_menu_tab
    added_coll_name = f"added_{view}"
    added_idx_name = f"added_{view}_index"
    unadded_coll_name = f"unadded_{view}"
    unadded_idx_name = f"unadded_{view}_index"

    added_coll = getattr(prefs, added_coll_name)
    added_idx = getattr(prefs, added_idx_name)
    unadded_coll = getattr(prefs, unadded_coll_name)
    unadded_idx = getattr(prefs, unadded_idx_name)

    main_row = layout.row(align=False)

    # Left Column: Added Right-Click Menu Items
    left_col = main_row.column(align=False)
    left_box = left_col.box()
    left_box.label(text=tr("Added Right-Click Menu Items:"), icon="CHECKBOX_HLT")
    left_box.template_list(
        "MOZI_UL_added_items_list",
        "",
        prefs,
        added_coll_name,
        prefs,
        added_idx_name,
        rows=6,
    )

    if 0 <= added_idx < len(added_coll):
        item = added_coll[added_idx]
        edit_box = left_col.box()
        e_row = edit_box.row(align=True)
        e_row.label(text=tr("Edit Menu Item:"), icon="EDITMODE_HLT")
        e_row.prop(item, "enabled", text=tr("Enabled"))
        edit_box.prop(item, "label", text=tr("Label"))

    # Middle Column: Action Buttons
    mid_col = main_row.column(align=True)
    mid_col.alignment = "CENTER"
    mid_col.separator(factor=3)

    sub_row1 = mid_col.row(align=True)
    sub_row1.enabled = len(unadded_coll) > 0 and 0 <= unadded_idx < len(unadded_coll)
    sub_row1.operator(MOZI_OT_menu_add_item.bl_idname, text="", icon="BACK")

    sub_row2 = mid_col.row(align=True)
    sub_row2.enabled = len(added_coll) > 0 and 0 <= added_idx < len(added_coll)
    sub_row2.operator(MOZI_OT_menu_remove_item.bl_idname, text="", icon="FORWARD")

    mid_col.separator(factor=3)

    sub_row3 = mid_col.row(align=True)
    sub_row3.enabled = len(added_coll) > 0 and added_idx > 0
    op_up = sub_row3.operator(MOZI_OT_menu_move_item.bl_idname, text="", icon="TRIA_UP")
    op_up.direction = "UP"

    sub_row4 = mid_col.row(align=True)
    sub_row4.enabled = len(added_coll) > 0 and added_idx < len(added_coll) - 1
    op_down = sub_row4.operator(MOZI_OT_menu_move_item.bl_idname, text="", icon="TRIA_DOWN")
    op_down.direction = "DOWN"

    # Right Column: Available / Unadded Options
    right_col = main_row.column(align=False)
    right_box = right_col.box()
    right_box.label(text=tr("Available Unadded Options:"), icon="ADD")
    right_box.template_list(
        "MOZI_UL_unadded_items_list",
        "",
        prefs,
        unadded_coll_name,
        prefs,
        unadded_idx_name,
        rows=6,
    )


def draw_misc_tab(prefs: Any, layout: Any, context: Any) -> None:
    """Renders the Performance & Storage tab inside addon preferences."""
    config_box = layout.box()
    c_header = config_box.row(align=True)
    c_header.label(text=tr("Configuration Storage:"), icon="PREFERENCES")

    c_desc = config_box.column(align=True)
    c_desc.scale_y = 0.85
    mgr = get_config_manager()
    backend = mgr.get_backend()
    from ..utils.config.backends.json_backend import JsonConfigBackend
    path_str = backend.get_config_path() if isinstance(backend, JsonConfigBackend) else "config.json"
    c_desc.label(text=f"{tr('Active File')}: {path_str}")
    c_desc.label(text=tr("Changes are atomically written to external JSON file with automatic backup."))

    layout.separator()

    # Performance & Multi-Threading
    perf_box = layout.box()
    perf_header = perf_box.row(align=True)
    perf_header.label(text=tr("Performance & Multi-Threading:"), icon="MOD_DATA_TRANSFER")
    cpu_cnt = os.cpu_count() or 1
    perf_header.label(text=f"{tr('Detected')}: {cpu_cnt} {tr('cores')}", icon="INFO")

    perf_col = perf_box.column(align=True)
    perf_col.prop(prefs, "thread_count", text=tr("Worker Threads"))
    perf_col.scale_y = 0.85
    if prefs.thread_count == 0:
        perf_col.label(text=f"{tr('Active mode')}: {tr('Auto')} ({cpu_cnt} {tr('threads')})")
    else:
        perf_col.label(text=f"{tr('Active mode')}: {tr('Fixed')} ({prefs.thread_count} {tr('threads')})")
    perf_col.label(text=tr("Controls Rayon thread pool concurrency for asset precompilation, atlas encoding, and mesh construction."))

    perf_box.separator()

    # DNA Direct Memory Acceleration toggle
    dna_row = perf_box.row(align=True)
    dna_row.prop(prefs, "enable_dna_acceleration", text=tr("DNA Direct Memory Acceleration"))

    dna_desc = perf_box.column(align=True)
    dna_desc.scale_y = 0.85
    if not prefs.enable_dna_acceleration:
        dna_desc.label(text=f"{tr('Status')}: {tr('Disabled (Safe Batch API Mode)')}", icon="CHECKMARK")
    else:
        try:
            from ..bridge.dna import is_dna_available
            dna_avail = is_dna_available()
        except Exception:
            dna_avail = False
        if dna_avail:
            dna_desc.label(text=f"{tr('Status')}: {tr('Active (Hardware Speed)')}", icon="CHECKMARK")
        else:
            dna_desc.label(text=f"{tr('Status')}: {tr('Unavailable (Batch API Active)')}", icon="INFO")
    dna_desc.label(text=tr("Hardware-speed direct memory injection into Blender mesh buffers. Disable to enforce 100% stable standard BPy batch API."))

    layout.separator()

    # Cache & Storage Management
    try:
        from ..utils.assets import get_cache_stats
        stats = get_cache_stats(prefs)
    except Exception:
        try:
            from ..bridge import get_cache_stats
            stats = get_cache_stats(prefs)
        except Exception:
            stats = {
                "size_formatted": "0 B",
                "files_count": 0,
                "path": str(getattr(prefs, "cache_dir", "") or "Default"),
            }

    cache_box = layout.box()
    cache_header = cache_box.row(align=True)
    cache_header.label(text=tr("Persistent Cache & Storage:"), icon="DISK_DRIVE")
    cache_header.label(text=f"{tr('Total')}: {stats['size_formatted']} ({stats['files_count']} {tr('files')})", icon="INFO")
    cache_header.operator("mozi.refresh_cache_stats", text=tr("Refresh"), icon="FILE_REFRESH")

    cache_col = cache_box.column(align=True)
    cache_col.prop(prefs, "cache_dir", text=tr("Cache Location"))
    cache_col.scale_y = 0.85
    cache_col.label(text=f"{tr('Resolved Path')}: {stats['path']}")
    cache_col.label(text=tr("Extracted packs, compiled multi-layer atlases, and JSON indices persist here across restarts."))

    cache_row = cache_box.row(align=True)
    cache_row.operator("mozi.open_cache_folder", text=tr("Open Cache Folder"), icon="FILE_FOLDER")
    cache_row.operator("mozi.clear_cache", text=tr("Clear Resource Pack Cache"), icon="TRASH")
