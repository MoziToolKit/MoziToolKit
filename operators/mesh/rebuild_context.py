"""
Context preparation and UI helpers for unified voxel mesh reconstruction.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import bpy

from ...i18n import tr
from ...utils.assets import (
    can_execute_asset_pipeline,
    ensure_pipeline_assets_ready_sync,
    get_cache_dir,
    prepare_asset_context,
    snapshot_pack_config,
)
from ...bridge.config_validation import sanitize_bool_property, sanitize_weld_mode
from ...bridge.world import prepare_world_mesh_context, scan_unknown_blocks
from ...utils.materials.builder.atlas_refresh import reload_atlas_images

logger = logging.getLogger("MoziToolKit.Operators.Mesh.RebuildContext")


def resolve_rebuild_flags(
    operator: Any,
    root_container: Optional[bpy.types.Object],
    world_mesh_obj: Optional[bpy.types.Object],
    prefs: Any,
) -> Dict[str, Any]:
    """Resolves operational flags from operator properties, container custom props, or user preferences."""
    enable_ao = sanitize_bool_property("enable_ao", getattr(operator, "enable_ao", True), True, log=logger)
    mesh_fluids = sanitize_bool_property("mesh_fluids", getattr(operator, "mesh_fluids", True), True, log=logger)
    weld_mode = sanitize_weld_mode(getattr(operator, "weld_mode", "manifold"), name="weld_mode", log=logger)
    weld_explicitly_set = bool(
        hasattr(operator, "properties")
        and hasattr(operator.properties, "is_property_set")
        and operator.properties.is_property_set("weld_mode")
    )
    origin_centered = sanitize_bool_property(
        "origin_centered", getattr(operator, "origin_centered", True), True, log=logger
    )

    if root_container is not None and hasattr(root_container, "get"):
        if root_container.get("mtk_enable_ao") is not None:
            enable_ao = sanitize_bool_property(
                "mtk_enable_ao", root_container.get("mtk_enable_ao"), True, log=logger
            )
        if root_container.get("mtk_mesh_fluids") is not None:
            mesh_fluids = sanitize_bool_property(
                "mtk_mesh_fluids", root_container.get("mtk_mesh_fluids"), True, log=logger
            )
        if not weld_explicitly_set and root_container.get("mtk_weld_mode") is not None:
            weld_mode = sanitize_weld_mode(
                root_container.get("mtk_weld_mode"), name="mtk_weld_mode", log=logger
            )
        if root_container.get("mtk_origin_centered") is not None:
            origin_centered = sanitize_bool_property(
                "mtk_origin_centered", root_container.get("mtk_origin_centered"), True, log=logger
            )
        elif root_container.get("mtk:origin_centered") is not None:
            origin_centered = sanitize_bool_property(
                "mtk:origin_centered", root_container.get("mtk:origin_centered"), True, log=logger
            )

    anim_tex_enabled = None
    if root_container is not None:
        props = getattr(root_container, "mozi_save", None)
        if props and hasattr(props, "enable_animated_textures"):
            anim_tex_enabled = sanitize_bool_property(
                "enable_animated_textures", props.enable_animated_textures, True, log=logger
            )
        elif hasattr(root_container, "get") and root_container.get("mtk_enable_animated_textures") is not None:
            anim_tex_enabled = sanitize_bool_property(
                "mtk_enable_animated_textures",
                root_container.get("mtk_enable_animated_textures"),
                True,
                log=logger,
            )
    if anim_tex_enabled is None and world_mesh_obj is not None:
        props = getattr(world_mesh_obj, "mozi_save", None)
        if props and hasattr(props, "enable_animated_textures"):
            anim_tex_enabled = sanitize_bool_property(
                "enable_animated_textures", props.enable_animated_textures, True, log=logger
            )
        elif hasattr(world_mesh_obj, "get") and world_mesh_obj.get("mtk_enable_animated_textures") is not None:
            anim_tex_enabled = sanitize_bool_property(
                "mtk_enable_animated_textures",
                world_mesh_obj.get("mtk_enable_animated_textures"),
                True,
                log=logger,
            )

    if anim_tex_enabled is None:
        raw_anim = getattr(prefs, "enable_animated_textures", True) if prefs is not None else True
        anim_tex_enabled = sanitize_bool_property("enable_animated_textures", raw_anim, True, log=logger)

    return {
        "enable_ao": enable_ao,
        "mesh_fluids": mesh_fluids,
        "weld_mode": weld_mode,
        "origin_centered": origin_centered,
        "anim_tex_enabled": anim_tex_enabled,
    }


def prepare_rebuild_asset_context(
    root_container: Optional[bpy.types.Object],
    source_type: str,
    status: str,
    prefs: Any,
    anim_tex_enabled: bool,
    origin_centered: bool,
    report_fn: Any,
    allow_defer_precompile: bool = False,
) -> Optional[Tuple[Dict[str, Any], bool]]:
    """
    Prepares model database, atlas, biome resolver, and CTM solver for remeshing.
    When `allow_defer_precompile=True` and cache is MISSING/STALE, defers precompilation
    to the background `run_task_with_asset_guard` worker instead of blocking the main thread.
    Returns (context_dict, did_precompile) or None if preparation failed with report.
    """
    package_reader = None
    did_precompile = False

    if source_type == "SCENE":
        scene_pkg_path = None
        if root_container is not None and hasattr(root_container, "get"):
            scene_pkg_path = root_container.get("mtk:scene_package_path")

        scene_data = None
        if scene_pkg_path and Path(scene_pkg_path).is_file():
            try:
                from ...bridge.scene import read_scene_package
                scene_data = read_scene_package(scene_pkg_path, origin_centered=origin_centered, z_up=True)
            except Exception as exc:
                logger.warning("Failed to read original .mtkscene package at %s: %s", scene_pkg_path, exc)

        if scene_data is not None:
            raw_threads = getattr(prefs, "thread_count", 0) if prefs is not None else 0
            num_threads = raw_threads if isinstance(raw_threads, int) and not isinstance(raw_threads, bool) and raw_threads > 0 else None
            return {
                "model_db": scene_data.get("models"),
                "atlas": scene_data.get("atlas"),
                "biome_resolver": scene_data.get("biome_resolver"),
                "colormaps": scene_data.get("colormaps"),
                "ctm_solver": scene_data.get("ctm_solver"),
                "package_reader": scene_data.get("reader"),
                "asset_context": {"status": "READY"},
                "mesh_context": {
                    "skin_cache_dir": None,
                    "enable_animated_textures": anim_tex_enabled,
                },
                "num_threads": num_threads,
                "anim_tex_enabled": anim_tex_enabled,
                "cache_fingerprint": None,
                "cache_timestamp": None,
                "deferred_precompile": False,
            }, False

        can_run, reason = can_execute_asset_pipeline(prefs)
        if not can_run:
            report_fn({"ERROR"}, f"Original .mtkscene package is missing ({scene_pkg_path}) and host asset cache is not ready: {reason}")
            return None

    if status in ("MISSING", "STALE"):
        if allow_defer_precompile:
            asset_context = prepare_asset_context(prefs)
            prefs_snapshot = snapshot_pack_config(prefs)
            skin_cache_dir = None
            try:
                sc = get_cache_dir(prefs) / "skins"
                sc.mkdir(parents=True, exist_ok=True)
                skin_cache_dir = str(sc)
            except Exception:
                pass
            return {
                "model_db": None,
                "atlas": None,
                "biome_resolver": None,
                "colormaps": None,
                "ctm_solver": None,
                "package_reader": package_reader,
                "asset_context": asset_context,
                "mesh_context": {
                    "skin_cache_dir": skin_cache_dir,
                    "enable_animated_textures": anim_tex_enabled,
                },
                "num_threads": asset_context.get("num_threads"),
                "anim_tex_enabled": anim_tex_enabled,
                "cache_fingerprint": None,
                "cache_timestamp": None,
                "prefs_snapshot": prefs_snapshot,
                "deferred_precompile": True,
            }, False

        try:
            ensure_pipeline_assets_ready_sync(prefs)
        except Exception as e:
            report_fn({"ERROR"}, f"Failed to precompile assets: {e}")
            return None
        did_precompile = True

    asset_context = prepare_asset_context(prefs)
    mesh_context = prepare_world_mesh_context(prefs, enable_animated_textures=anim_tex_enabled)

    return {
        "model_db": mesh_context["model_db"],
        "atlas": mesh_context["atlas"],
        "biome_resolver": mesh_context["biome_resolver"],
        "colormaps": mesh_context.get("colormaps"),
        "ctm_solver": mesh_context["ctm_solver"],
        "package_reader": package_reader,
        "asset_context": asset_context,
        "mesh_context": mesh_context,
        "num_threads": mesh_context["num_threads"],
        "anim_tex_enabled": mesh_context["enable_animated_textures"],
        "cache_fingerprint": mesh_context["cache_fingerprint"],
        "cache_timestamp": mesh_context["cache_timestamp"],
        "deferred_precompile": False,
    }, did_precompile


def draw_unknown_blocks_dialog(
    layout: Any,
    unknown_blocks: List[str],
    prefs: Any,
) -> None:
    """Draws dialog content for detected unknown blocks."""
    if not unknown_blocks:
        return
    box = layout.box()
    box.label(
        text=tr("Detected {count} unknown block(s):").format(count=len(unknown_blocks)),
        icon="ERROR",
    )
    col = box.column(align=True)
    for block_id in unknown_blocks[:10]:
        col.label(text=f"  • {block_id}")
    if len(unknown_blocks) > 10:
        col.label(
            text=tr("  ... and {count} more").format(count=len(unknown_blocks) - 10)
        )

    strat = getattr(prefs, "unknown_block_strategy", "FALLBACK_CUBE") if prefs else "FALLBACK_CUBE"
    strat_desc = tr("Magenta/Black Cube") if strat == "FALLBACK_CUBE" else tr("Ignore (Air)")
    layout.separator()
    layout.label(text=f"{tr('Handling Strategy')}: {strat_desc}")
    layout.label(text=tr("Click OK to proceed with meshing."))


def check_unknown_blocks(
    operator: Any,
    context: Any,
    cloud_data: Any,
    ctx_data: Dict[str, Any],
    prefs: Any,
) -> Tuple[bool, Optional[Any]]:
    """Scans for unknown blocks. Returns (proceed, dialog_result_or_none)."""
    try:
        unknown_blocks = scan_unknown_blocks(
            cloud_data,
            model_db=ctx_data["model_db"],
            atlas=ctx_data["atlas"],
            prefs=prefs,
        )
    except Exception as exc:
        if not getattr(operator, "force", False):
            operator.report({"ERROR"}, f"Unknown block validation failed: {exc}")
            return False, {"CANCELLED"}
        unknown_blocks = []

    if unknown_blocks and not getattr(operator, "force", False):
        if getattr(operator, "_dialog_confirmed", False):
            operator._dialog_confirmed = False
        else:
            operator._unknown_blocks = unknown_blocks
            if not getattr(bpy.app, "background", False) and hasattr(context, "window_manager"):
                operator._dialog_confirmed = True
                return False, context.window_manager.invoke_props_dialog(operator, width=420)
            operator.report(
                {"WARNING"},
                f"Found {len(unknown_blocks)} unknown block(s): {', '.join(unknown_blocks)}. "
                "Pass force=True to proceed with fallback cubes or air.",
            )
            operator._dialog_confirmed = False
            return False, {"CANCELLED"}
    return True, None


def unpack_cloud_rebuild_result(result_tuple: Any, prefs: Any) -> Tuple[Any, float, Any]:
    """Unpacks (mesh_data, elapsed_ms, storage) from background worker result."""
    storage = None
    if isinstance(result_tuple, tuple) and len(result_tuple) == 2 and isinstance(result_tuple[1], bool):
        inner, did_precomp = result_tuple
        if len(inner) == 3:
            mesh_data, elapsed_ms, storage = inner
        else:
            mesh_data, elapsed_ms = inner
        if did_precomp:
            try:
                reload_atlas_images(prefs)
            except Exception:
                pass
    elif isinstance(result_tuple, tuple) and len(result_tuple) == 3:
        mesh_data, elapsed_ms, storage = result_tuple
    else:
        mesh_data, elapsed_ms = result_tuple
    return mesh_data, elapsed_ms, storage


def apply_rebuilt_mesh_biomes(
    world_mesh_obj: bpy.types.Object,
    root_container: Optional[bpy.types.Object],
    storage: Any,
) -> None:
    """Marks world biome preset and invalidates per-mesh cached biome lookup tables."""
    from ...utils.materials.biome.updater import invalidate_mesh_biome_cache, mark_world_biome

    invalidate_mesh_biome_cache(getattr(world_mesh_obj, "data", None))
    aliases = []
    if root_container is not None:
        aliases.append(getattr(root_container, "name", ""))
        container_id = root_container.get("mtk:container_id", "") if hasattr(root_container, "get") else ""
        if isinstance(container_id, str) and container_id:
            aliases.append(container_id)
        mark_world_biome(root_container, storage)
    mark_world_biome(world_mesh_obj, storage, aliases=aliases)
