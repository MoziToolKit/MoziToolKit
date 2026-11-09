"""
Unified Voxel Mesh Reconstruction Operator.
Reconstructs voxel surface mesh with manifold topology, smooth AO, fluids, and atlas shaders.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional, Tuple
import bpy

from ...utils.system import get_prefs, register_menu_item
from ...utils.async_task import AsyncTask, ModalTaskRunner
from ...utils.progress import BlenderProgressReporter
from ...utils.diagnostics import format_timing
from ...bridge.point_cloud import extract_voxel_point_cloud
from ...bridge.world import (
    clear_world_sessions,
    get_world_pipeline_assets,
    mesh_voxel_storage,
    prepare_world_mesh_context,
    scan_unknown_blocks,
    get_world_session_dynamic_entity_atlases,
)
from ...utils.materials.builder.world_binding import ensure_world_materials
from ...bridge.mesh import inject_mesh_data
from ...bridge.sync import get_sync_bridge_session
from ...bridge.assets import check_cache_dirty, invalidate_asset_cache_memory
from ...utils.materials.builder.atlas_refresh import reload_atlas_images
from ...utils.assets import (
    can_execute_asset_pipeline,
    prepare_asset_context,
    check_asset_cache_status,
    ensure_pipeline_assets_ready_sync,
    run_task_with_asset_guard,
)
from ...utils.mesh.ownership import (
    ORIGIN_EXTERNAL,
    ORIGIN_VOXEL,
    classify_origin,
    stamp_geometry_origin,
)
from .rebuild_targets import (
    _safe_set_custom_prop,
    _resolve_mesh_rebuild_targets_from_root,
    resolve_mesh_rebuild_targets,
    _live_session_matches_container,
    get_or_create_container_mesh,
)
from .rebuild_context import (
    resolve_rebuild_flags,
    prepare_rebuild_asset_context,
    draw_unknown_blocks_dialog,
    check_unknown_blocks,
    unpack_cloud_rebuild_result,
    apply_rebuilt_mesh_biomes,
)

logger = logging.getLogger("MoziToolKit.Operators.Mesh.Rebuild")


def _execute_live_rebuild(operator, context, prefs, status, session, root_container, world_mesh_obj):
    from ...utils.world.sync_container import get_or_create_world_mesh_object, update_world_mesh

    if status in ("MISSING", "STALE"):
        ensure_pipeline_assets_ready_sync(prefs)
        if hasattr(session, "check_and_reload_dirty_cache"):
            session.check_and_reload_dirty_cache(prefs=prefs, force=True)
    elif hasattr(session, "check_and_reload_dirty_cache"):
        try:
            session.check_and_reload_dirty_cache(prefs=prefs)
        except Exception:
            pass

    def do_live_rebuild(progress_callback=None):
        return session.get_world_mesh()

    def on_live_success(mesh_data):
        if not mesh_data or mesh_data.vertex_count == 0:
            operator.report({"WARNING"}, "Voxel storage is empty or no geometry generated.")
            return
        target_root = root_container
        world_obj = world_mesh_obj or get_or_create_world_mesh_object(context, root_container=target_root)
        storage = session.get_storage()
        v_count, f_count = update_world_mesh(world_obj, mesh_data, storage=storage, sync_point_cloud=True)
        root_obj = target_root or world_obj
        props = getattr(root_obj, "mozi_sync", None) or (getattr(context.scene, "mozi_sync", None) if hasattr(context, "scene") else None)
        if props:
            props.point_count = v_count
            props.faces_count = f_count
            props.last_update_info = f"Rebuilt World Mesh: {v_count:,} vertices, {f_count:,} faces"
        operator.report({"INFO"}, f"Live Sync mesh rebuilt: {v_count:,} vertices, {f_count:,} faces")

    if not getattr(operator, "run_async", True):
        try:
            data = do_live_rebuild()
            on_live_success(data)
            return {"FINISHED"}
        except Exception as e:
            operator.report({"ERROR"}, f"Failed rebuilding live sync mesh: {e}")
            return {"CANCELLED"}

    task = AsyncTask(target=do_live_rebuild)
    reporter = BlenderProgressReporter(context=context, total=100, title="Rebuilding World Mesh")
    operator._runner = ModalTaskRunner(
        operator=operator,
        context=context,
        task=task,
        reporter=reporter,
        on_success=on_live_success,
        title="Rebuilding World Mesh",
    )
    return operator._runner.start()


def _handle_cloud_success(
    operator,
    result_tuple,
    context,
    prefs,
    root_container,
    world_mesh_obj,
    ctx_data,
    flags,
    session_key,
):
    mesh_data, elapsed_ms, storage = unpack_cloud_rebuild_result(result_tuple, prefs)

    if mesh_data is not None and getattr(mesh_data, "profile", None) is not None:
        try:
            from ...bridge.diagnostics import record_operation_profile
            record_operation_profile("Rebuild World Mesh", mesh_data.profile)
        except Exception:
            pass

    if world_mesh_obj is None and root_container is not None:
        world_mesh_obj = get_or_create_container_mesh(context, root_container)

    if not world_mesh_obj or not hasattr(world_mesh_obj, "data"):
        operator.report({"WARNING"}, "No mesh object available to receive geometry.")
        return

    t0_inj = time.perf_counter()
    inject_mesh_data(
        mesh_data,
        world_mesh_obj.data,
        update_topology=True,
        update_normals=True,
    )
    inj_ms = (time.perf_counter() - t0_inj) * 1000.0

    t0_mat = time.perf_counter()
    used_chunk_ids = mesh_data.used_materials()
    dyn_entities = get_world_session_dynamic_entity_atlases(session_key) if session_key else None
    pkg_reader = ctx_data.get("package_reader")

    if pkg_reader is not None:
        from ...utils.scene.materials import bind_scene_materials
        bind_scene_materials(
            world_mesh_obj,
            reader=pkg_reader,
            scene_name=root_container.name if root_container else "Scene",
            atlas=ctx_data["atlas"],
            prefs=prefs,
            used_chunk_ids=used_chunk_ids,
            dynamic_entity_atlases=dyn_entities,
        )
    else:
        ensure_world_materials(
            world_mesh_obj,
            prefs=prefs,
            atlas=ctx_data["atlas"],
            used_chunk_ids=used_chunk_ids,
            dynamic_entity_atlases=dyn_entities,
            clean_unused_slots=True,
        )
    mat_ms = (time.perf_counter() - t0_mat) * 1000.0

    anim_tex = flags["anim_tex_enabled"]
    _safe_set_custom_prop(world_mesh_obj, "mtk_enable_animated_textures", bool(anim_tex))
    if root_container is not None:
        _safe_set_custom_prop(root_container, "mtk_enable_animated_textures", bool(anim_tex))

    try:
        from ...bridge.diagnostics import append_stage_to_last_profile
        append_stage_to_last_profile("blender_mesh_injection", inj_ms, getattr(mesh_data, "vertex_count", None))
        append_stage_to_last_profile("blender_material_binding", mat_ms, len(used_chunk_ids) if used_chunk_ids else None)
    except Exception:
        pass

    apply_rebuilt_mesh_biomes(world_mesh_obj, root_container, storage)

    poly_cnt = len(world_mesh_obj.data.polygons) if hasattr(world_mesh_obj.data, "polygons") else 0
    operator.report(
        {"INFO"},
        f"Rebuilt voxel mesh: {poly_cnt:,} faces in "
        f"{format_timing(elapsed_ms, operation='Rebuild World Mesh')}",
    )


def _launch_cloud_rebuild(
    operator,
    context,
    prefs,
    cloud_data,
    root_container,
    world_mesh_obj,
    cloud_obj,
    flags,
    ctx_data,
    did_precompile,
):
    session_key = (
        getattr(root_container, "name", None)
        or (root_container.get("mozi_save_uuid") if root_container and hasattr(root_container, "get") else None)
        or (getattr(world_mesh_obj, "name", None))
        or (getattr(cloud_obj, "name", "global_cloud"))
    )

    def do_cloud_rebuild(progress_callback=None):
        storage = cloud_data.to_storage()
        unknown_strat = getattr(prefs, "unknown_block_strategy", "FALLBACK_CUBE") if prefs is not None else "FALLBACK_CUBE"
        mesh_data, elapsed_ms = mesh_voxel_storage(
            storage=storage,
            model_db=ctx_data["model_db"],
            atlas=ctx_data["atlas"],
            biome_resolver=ctx_data["biome_resolver"],
            colormaps=ctx_data["colormaps"],
            ctm_solver=ctx_data["ctm_solver"],
            prefs=prefs,
            enable_ao=flags["enable_ao"],
            mesh_fluids=flags["mesh_fluids"],
            weld_vertices=flags["weld_vertices"],
            origin_centered=flags["origin_centered"],
            num_threads=ctx_data["num_threads"],
            progress_callback=progress_callback,
            world_session_key=session_key,
            force_rebuild=did_precompile,
            enable_animated_textures=flags["anim_tex_enabled"],
            skin_cache_dir=ctx_data["mesh_context"].get("skin_cache_dir"),
            cache_fingerprint=ctx_data["cache_fingerprint"],
            cache_timestamp=ctx_data["cache_timestamp"],
            resolve_prefs=False,
            resolve_ctm=False,
            unknown_block_strategy=unknown_strat,
        )
        return mesh_data, elapsed_ms, storage

    def on_cloud_success(result_tuple):
        _handle_cloud_success(
            operator=operator,
            result_tuple=result_tuple,
            context=context,
            prefs=prefs,
            root_container=root_container,
            world_mesh_obj=world_mesh_obj,
            ctx_data=ctx_data,
            flags=flags,
            session_key=session_key,
        )

    if not getattr(operator, "run_async", True):
        try:
            res = do_cloud_rebuild()
            on_cloud_success(res)
            return {"FINISHED"}
        except Exception as e:
            operator.report({"ERROR"}, f"Remeshing failed: {e}")
            return {"CANCELLED"}

    task = AsyncTask(
        target=run_task_with_asset_guard,
        kwargs={
            "target_func": do_cloud_rebuild,
            "target_kwargs": {},
            "prefs": prefs,
            "task_title": "Rebuilding Voxel Mesh",
            "asset_context": ctx_data["asset_context"],
        },
    )
    reporter = BlenderProgressReporter(context=context, total=100, title="Rebuilding Voxel Mesh")
    operator._runner = ModalTaskRunner(
        operator=operator,
        context=context,
        task=task,
        reporter=reporter,
        on_success=on_cloud_success,
        title="Rebuilding Voxel Mesh",
    )
    return operator._runner.start()


@register_menu_item(views=["object", "mesh"], label="Rebuild Voxel Mesh")
class MOZI_OT_rebuild_mesh(bpy.types.Operator):
    """
    Reconstruct voxel surface mesh with manifold topology, smooth AO, fluids, and atlas shaders.
    Unified operator supporting Live Sync world sessions, Save containers, and Voxel Point Clouds.
    """

    bl_idname = "mozi.rebuild_mesh"
    bl_label = "Rebuild Voxel Mesh"
    bl_description = "Unified rebuild of voxel surface geometry from Live Sync, Save container, or Point Cloud"
    bl_options = {"REGISTER", "UNDO"}

    enable_ao: bpy.props.BoolProperty(
        name="Ambient Occlusion",
        description="Calculate smooth 4-corner ambient occlusion lighting",
        default=True,
    )

    mesh_fluids: bpy.props.BoolProperty(
        name="Mesh Fluids",
        description="Reconstruct physically-accurate fluid surfaces",
        default=True,
    )

    weld_vertices: bpy.props.BoolProperty(
        name="Weld Vertices",
        description="Weld adjacent coplanar vertices into manifold topology",
        default=True,
    )

    origin_centered: bpy.props.BoolProperty(
        name="Origin Centered",
        description="Center mesh coordinate bounds relative to selection bottom center",
        default=True,
    )

    force: bpy.props.BoolProperty(
        name="Force Rebuild",
        description="Proceed with rebuild even if unknown blocks without models/textures are detected",
        default=False,
    )

    run_async: bpy.props.BoolProperty(
        name="Run Asynchronously",
        description="Run calculation in non-blocking background thread with live status bar progress",
        default=True,
        options={"HIDDEN"},
    )

    _runner: Optional[Any] = None
    _unknown_blocks: List[str] = []
    _dialog_confirmed: bool = False

    def invoke(self, context, event):
        self._dialog_confirmed = False
        return self.execute(context)

    def cancel(self, context):
        self._dialog_confirmed = False
        self._unknown_blocks = []

    def draw(self, context):
        prefs = get_prefs(context)
        unknown = getattr(self, "_unknown_blocks", [])
        draw_unknown_blocks_dialog(self.layout, unknown, prefs)

    @classmethod
    def poll(cls, context):
        if not context:
            return False

        root_container, world_mesh, cloud_obj, source_type = resolve_mesh_rebuild_targets(context)
        if source_type == "NONE":
            return False

        if source_type in ("SAVE", "SYNC", "SCENE") and world_mesh is not None:
            if classify_origin(world_mesh) == ORIGIN_EXTERNAL:
                return False

        if source_type == "SCENE":
            return cloud_obj is not None

        prefs = get_prefs(context)
        can_run, reason = can_execute_asset_pipeline(prefs)
        if not can_run:
            if hasattr(cls, "poll_message_set"):
                cls.poll_message_set(reason)
            return False

        if source_type == "SYNC":
            if _live_session_matches_container(context, root_container, get_session_fn=get_sync_bridge_session):
                return True
            return cloud_obj is not None

        if source_type == "SAVE":
            return cloud_obj is not None

        if source_type == "CLOUD":
            return cloud_obj is not None and world_mesh is not None

        return False

    def execute(self, context):
        prefs = get_prefs(context)
        root_container, world_mesh_obj, cloud_obj, source_type = resolve_mesh_rebuild_targets(context)
        if source_type == "NONE" or (world_mesh_obj is None and cloud_obj is None and root_container is None):
            self.report({"WARNING"}, "No valid Live Sync, Save container, or Voxel Point Cloud found.")
            return {"CANCELLED"}

        if source_type != "SCENE":
            can_run, reason = can_execute_asset_pipeline(prefs)
            if not can_run:
                self.report({"WARNING"}, reason)
                return {"CANCELLED"}

        status = check_asset_cache_status(prefs)

        saved_mode = getattr(context, "mode", "OBJECT")
        if saved_mode != "OBJECT" and hasattr(bpy.ops, "object") and hasattr(bpy.ops.object, "mode_set"):
            try:
                bpy.ops.object.mode_set(mode="OBJECT")
            except Exception:
                pass

        # Branch A: Active Live Sync session
        session = get_sync_bridge_session() if callable(get_sync_bridge_session) else None
        use_live_session = False
        if source_type == "SYNC" and session and getattr(session, "is_active", False):
            active_c_name = getattr(context.scene, "mozi_active_sync_container_name", "") if hasattr(context, "scene") else ""
            if not root_container or not active_c_name or root_container.name == active_c_name:
                use_live_session = True

        if use_live_session:
            return _execute_live_rebuild(self, context, prefs, status, session, root_container, world_mesh_obj)

        # Branch B: Remesh from companion Voxel Point Cloud
        if source_type != "SCENE" and status in ("NO_PACKS", "NO_VANILLA"):
            self.report({"WARNING"}, reason)
            return {"CANCELLED"}
        if cloud_obj is None:
            self.report({"WARNING"}, "Associated Voxel Point Cloud is required for offline mesh reconstruction.")
            return {"CANCELLED"}

        cloud_data = extract_voxel_point_cloud(cloud_obj)
        if cloud_data is None:
            self.report({"ERROR"}, "Failed to extract voxel point cloud data from companion object.")
            return {"CANCELLED"}

        if len(cloud_data) == 0:
            if world_mesh_obj and hasattr(world_mesh_obj, "data") and hasattr(world_mesh_obj.data, "clear_geometry"):
                world_mesh_obj.data.clear_geometry()
            self.report({"INFO"}, "Voxel cloud is empty; cleared mesh geometry.")
            return {"FINISHED"}

        flags = resolve_rebuild_flags(self, root_container, world_mesh_obj, prefs)
        prep = prepare_rebuild_asset_context(
            root_container=root_container,
            source_type=source_type,
            status=status,
            prefs=prefs,
            anim_tex_enabled=flags["anim_tex_enabled"],
            origin_centered=flags["origin_centered"],
            report_fn=self.report,
        )
        if prep is None:
            return {"CANCELLED"}
        ctx_data, did_precompile = prep

        proceed, dialog_res = check_unknown_blocks(self, context, cloud_data, ctx_data, prefs)
        if not proceed:
            return dialog_res

        return _launch_cloud_rebuild(
            operator=self,
            context=context,
            prefs=prefs,
            cloud_data=cloud_data,
            root_container=root_container,
            world_mesh_obj=world_mesh_obj,
            cloud_obj=cloud_obj,
            flags=flags,
            ctx_data=ctx_data,
            did_precompile=did_precompile,
        )

    def modal(self, context, event):
        if self._runner is not None:
            return self._runner.modal(event)
        return {"FINISHED"}
