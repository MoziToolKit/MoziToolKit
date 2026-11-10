"""
MoziToolKit Dev 3D Viewport Sidebar Panel.
Provides quick visual verification, scenario loading, and native engine diagnostics.
"""

import bpy
from ..loader import get_engine_status
from ... import bridge


class VIEW3D_PT_mtk_dev_panel(bpy.types.Panel):
    """Developer sidebar panel for MoziToolKit native core diagnostics and debugging"""

    bl_label = "MTK Dev Tools"
    bl_idname = "VIEW3D_PT_mtk_dev_panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "MTK Dev"

    def draw(self, context):
        from ...i18n import tr
        layout = self.layout

        # 1. Native Engine Status
        status = get_engine_status()
        box = layout.box()
        col = box.column(align=True)

        header = col.row(align=True)
        if status["available"]:
            header.label(text=tr("Engine: ONLINE"), icon="CHECKMARK")
            mode_text = tr("DEV (.so)") if status.get("is_dev") else tr("PROD (Wheel)")
            header.label(text=f"[{mode_text}]")
        else:
            header.label(text=tr("Engine: OFFLINE"), icon="CANCEL")

        col.separator(factor=0.5)
        col.label(text=f"{tr('Version')}: {status.get('version', 'N/A')}")
        path_str = status.get("path", "")
        if path_str:
            short_path = path_str if len(path_str) <= 38 else "..." + path_str[-35:]
            col.label(text=f"{tr('Path')}: {short_path}")
        if status.get("is_dev"):
            col.separator(factor=0.3)
            reload_row = col.row(align=True)
            reload_row.operator("mozi.dev_reload_binary", text=tr("Hot Reload Native Binary"), icon="FILE_REFRESH")

        # 2. Debug Scenarios
        scenarios_box = layout.box()
        scenarios_box.label(text=tr("Debug Scenarios"), icon="SCENE_DATA")

        row = scenarios_box.row(align=True)
        row.scale_y = 1.3
        op = row.operator("mozi.dev_load_debug_world", text=tr("Load Debug World"), icon="WORLD")
        op.enable_ao = True
        op.mesh_fluids = True

        row2 = scenarios_box.row(align=True)
        row2.operator("mozi.dev_mesh_benchmark", text=tr("Mesher Benchmark"), icon="TIME")

        # 3. Execution Profile Breakdown (Most Recent Operation)
        try:
            from ..profile_tracker import get_last_profile
            profile = get_last_profile()
        except Exception:
            profile = None

        prof_box = layout.box()
        prof_header = prof_box.row(align=True)
        prof_header.label(text=tr("Last Operation Profile"), icon="PREVIEW_RANGE")

        if profile:
            op_label = f"{profile['operation']}: {profile['total_ms']} ms"
            prof_header.label(text=f"[{profile['timestamp']}]")

            sub = prof_box.column(align=True)
            sub.label(text=op_label, icon="SORTTIME")
            core_ms = float(profile.get("core_ms", 0.0) or 0.0)
            host_ms = float(profile.get("host_ms", 0.0) or 0.0)
            if host_ms > 0.0 and core_ms > 0.0:
                sub.label(
                    text=f"{tr('Rust')} {core_ms:.1f} ms   |   {tr('Blender')} {host_ms:.1f} ms"
                )
            sub.separator(factor=0.5)

            stages = profile.get("stages", [])
            for idx, stage in enumerate(stages):
                s_row = sub.row(align=True)
                s_name = stage["name"]
                cat = stage.get("category")
                depth = int(stage.get("depth", 0))

                # Indentation and prefix by depth
                if depth == 0:
                    cat_prefix = f"[{cat}] " if cat else ""
                    display_name = f"{idx+1}. {cat_prefix}{s_name}"
                else:
                    indent = "    " * depth
                    display_name = f"{indent}└─ {s_name}"

                s_row.label(text=display_name)

                # Optional items count
                if stage.get("items_count") is not None:
                    s_row.label(text=f"[{stage['items_count']} items]")
                else:
                    s_row.label(text="")

                # Duration & percentage
                dur_text = f"{stage['duration_ms']:.2f} ms ({stage['percent']}%)"
                s_row.label(text=dur_text)

            unaccounted_ms = float(profile.get("unaccounted_ms", 0.0) or 0.0)
            total_ms = float(profile.get("total_ms", 0.0) or 0.0)
            if unaccounted_ms > 0.5 and total_ms > 0:
                unacc_pct = round((unaccounted_ms / total_ms) * 100.0, 1)
                u_row = sub.row(align=True)
                u_row.label(text=f"~ {tr('Unaccounted Overhead')}")
                u_row.label(text="")
                u_row.label(text=f"{unaccounted_ms:.2f} ms ({unacc_pct}%)")

            prof_box.separator(factor=0.3)
            clear_row = prof_box.row(align=True)
            clear_row.operator("mozi.dev_clear_profile", text=tr("Clear Profile"), icon="PANEL_CLOSE")
        else:
            prof_col = prof_box.column(align=True)
            prof_col.label(text=tr("No operations profiled yet"), icon="INFO")
            prof_col.label(text=tr("Run benchmark or load world to inspect steps"))

        # 4. Material & Asset Cache Status
        cache_box = layout.box()
        cache_header = cache_box.row(align=True)
        cache_header.label(text=tr("Material & Asset Cache"), icon="FILE_CACHE")
        cache_header.operator("mozi.refresh_cache_stats", text="", icon="FILE_REFRESH")

        prefs = None
        try:
            from ...utils.system.dependencies import get_prefs
            prefs = get_prefs(context)
        except Exception:
            pass

        try:
            from ...utils.assets.guard import check_asset_cache_status
            cache_status = check_asset_cache_status(prefs)
        except Exception:
            cache_status = "UNKNOWN"

        try:
            stats = bridge.get_cache_stats(prefs=prefs)
            c_col = cache_box.column(align=True)
            status_icons = {
                "READY": "CHECKMARK",
                "MISSING": "INFO",
                "STALE": "TIME",
                "NO_PACKS": "ERROR",
                "NO_VANILLA": "ERROR",
            }
            c_col.label(
                text=f"{tr('Status')}: {cache_status}",
                icon=status_icons.get(cache_status, "INFO"),
            )
            files = stats.get('files_count', stats.get('total_files', 0))
            size = stats.get('size_formatted', '0 B')
            c_col.label(text=f"{tr('Files')}: {files} ({size})")
            p = stats.get('path', '')
            if p:
                short_p = p if len(p) <= 38 else "..." + p[-35:]
                c_col.label(text=f"{tr('Path')}: {short_p}")
        except Exception:
            cache_box.label(text=tr("Cache unavailable"))

        row_c = cache_box.row(align=True)
        row_c.operator("mozi.precompile_cache", text=tr("Precompile Cache"), icon="FILE_REFRESH")
        row_c.operator("mozi.clear_cache", text=tr("Clear Cache"), icon="TRASH")

        row_f = cache_box.row(align=True)
        row_f.operator("mozi.open_cache_folder", text=tr("Open Cache Folder"), icon="FILE_FOLDER")
