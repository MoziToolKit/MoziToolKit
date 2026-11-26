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
from ...bridge.diagnostics import format_timing
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
    snapshot_pack_config,
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

    run_async = bool(getattr(operator, "run_async", True))
    needs_precompile = status in ("MISSING", "STALE")
    asset_context = (
        prepare_asset_context(prefs)
        if needs_precompile
        else {"status": status, "stack": None, "cache_dir": None, "num_threads": None}
    )
    prefs_snapshot = snapshot_pack_config(prefs) if run_async else prefs

    def _reload_session_if_needed(target_prefs, force_reload: bool) -> bool:
        if not hasattr(session, "check_and_reload_dirty_cache"):
            return False
        try:
            return bool(session.check_and_reload_dirty_cache(prefs=target_prefs, force=force_reload))
        except Exception as exc:
            logger.warning("Failed dirty cache check during live rebuild: %s", exc)
            return False

    def do_live_rebuild(progress_callback=None):
        need_force = needs_precompile or (getattr(session, "atlas", None) is None)
        did_reload = _reload_session_if_needed(prefs_snapshot, force_reload=need_force)
        mesh_data = session.get_world_mesh()
        return mesh_data, did_reload

    def on_live_success(result_payload):
        did_precompile = False
        did_reload = False
        mesh_data = result_payload
        if isinstance(result_payload, tuple) and len(result_payload) == 2 and isinstance(result_payload[1], bool):
            inner, did_precompile = result_payload
            if isinstance(inner, tuple) and len(inner) == 2 and isinstance(inner[1], bool):
                mesh_data, did_reload = inner
            else:
                mesh_data = inner

        if did_precompile or did_reload:
            try:
                reload_atlas_images(prefs)
            except Exception:
                pass

        if not mesh_data or mesh_data.vertex_count == 0:
            operator.report({"WARNING"}, "Voxel storage is empty or no geometry generated.")
            return

        if getattr(mesh_data, "profile", None) is not None:
            try:
                from ...bridge.diagnostics import record_operation_profile
                record_operation_profile("Rebuild World Mesh", mesh_data.profile)
            except Exception:
                pass

        target_root = root_container
        world_obj = world_mesh_obj or get_or_create_world_mesh_object(context, root_container=target_root)
        storage = session.get_storage()
        v_count, f_count = update_world_mesh(
            world_obj,
            mesh_data,
            storage=storage,
            sync_point_cloud=True,
            atlas=getattr(session, "atlas", None),
            session=session,
            prefs=prefs,
            clean_unused_slots=True,
        )
        root_obj = target_root or world_obj
        props = getattr(root_obj, "mozi_sync", None) or (getattr(context.scene, "mozi_sync", None) if hasattr(context, "scene") else None)
        info_suffix = " (Assets Hot-Reloaded)" if (did_precompile or did_reload) else ""
        if props:
            props.point_count = v_count
            props.faces_count = f_count
            props.last_update_info = f"Rebuilt World Mesh: {v_count:,} vertices, {f_count:,} faces{info_suffix}"
        operator.report({"INFO"}, f"Live Sync mesh rebuilt{info_suffix}: {v_count:,} vertices, {f_count:,} faces")

    if not run_async:
        try:
            did_precomp = False
            if needs_precompile:
                ensure_pipeline_assets_ready_sync(prefs)
                did_precomp = True
            inner = do_live_rebuild()
            on_live_success((inner, did_precomp))
            return {"FINISHED"}
        except Exception as e:
            operator.report({"ERROR"}, f"Failed rebuilding live sync mesh: {e}")
            return {"CANCELLED"}

    task = AsyncTask(
        target=run_task_with_asset_guard,
        kwargs={
            "target_func": do_live_rebuild,
            "target_kwargs": {},
            "prefs": prefs_snapshot,
            "task_title": "Rebuilding World Mesh",
            "asset_context": asset_context,
        },
    )
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
            material_indices_are_slots=getattr(mesh_data, "materials_are_slots", False),
            expected_fingerprint=ctx_data.get("cache_fingerprint"),
        )
    mat_ms = (time.perf_counter() - t0_mat) * 1000.0

    anim_tex = flags["anim_tex_enabled"]
    weld_mode = flags["weld_mode"]
    _safe_set_custom_prop(world_mesh_obj, "mtk_enable_animated_textures", bool(anim_tex))
    _safe_set_custom_prop(world_mesh_obj, "mtk_weld_mode", str(weld_mode))
    if root_container is not None:
        _safe_set_custom_prop(root_container, "mtk_enable_animated_textures", bool(anim_tex))
        _safe_set_custom_prop(root_container, "mtk_weld_mode", str(weld_mode))
        props = getattr(root_container, "mozi_save", None)
        if props is not None and hasattr(props, "weld_mode"):
            try:
                props.suppress_dirty = True
                props.weld_mode = str(weld_mode)
            except Exception:
                pass
            finally:
                props.suppress_dirty = False

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
        did_deferred_precompile = False
        if ctx_data.get("deferred_precompile"):
            fresh_mesh_ctx = prepare_world_mesh_context(
                ctx_data.get("prefs_snapshot") or prefs,
                enable_animated_textures=flags["anim_tex_enabled"],
            )
            ctx_data["model_db"] = fresh_mesh_ctx["model_db"]
            ctx_data["atlas"] = fresh_mesh_ctx["atlas"]
            ctx_data["biome_resolver"] = fresh_mesh_ctx["biome_resolver"]
            ctx_data["colormaps"] = fresh_mesh_ctx.get("colormaps")
            ctx_data["ctm_solver"] = fresh_mesh_ctx["ctm_solver"]
            ctx_data["mesh_context"] = fresh_mesh_ctx
            ctx_data["cache_fingerprint"] = fresh_mesh_ctx["cache_fingerprint"]
            ctx_data["cache_timestamp"] = fresh_mesh_ctx["cache_timestamp"]
            did_deferred_precompile = True

        storage = cloud_data.to_storage()
        worker_prefs = ctx_data.get("prefs_snapshot") or prefs
        unknown_strat = getattr(worker_prefs, "unknown_block_strategy", "FALLBACK_CUBE") if worker_prefs is not None else "FALLBACK_CUBE"
        mesh_data, elapsed_ms = mesh_voxel_storage(
            storage=storage,
            model_db=ctx_data["model_db"],
            atlas=ctx_data["atlas"],
            biome_resolver=ctx_data["biome_resolver"],
            colormaps=ctx_data["colormaps"],
            ctm_solver=ctx_data["ctm_solver"],
            prefs=worker_prefs,
            enable_ao=flags["enable_ao"],
            mesh_fluids=flags["mesh_fluids"],
            weld_mode=flags["weld_mode"],
            origin_centered=flags["origin_centered"],
            num_threads=ctx_data["num_threads"],
            progress_callback=progress_callback,
            world_session_key=session_key,
            force_rebuild=did_precompile or did_deferred_precompile,
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
            "prefs": ctx_data.get("prefs_snapshot") or prefs,
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

    weld_mode: bpy.props.EnumProperty(
        name="Weld Mode",
        description="Vertex welding strategy for DCC manifold topology or Web/glTF per-face normals",
        items=[
            (
                "manifold",
                "Manifold (Blender)",
                "Weld co-located corners into watertight manifold topology for Blender SSS and modifiers",
            ),
            (
                "per_vertex",
                "Per-Vertex (Web / glTF)",
                "Preserve per-face normals while welding shared coplanar corners for WebGPU, Three.js, and glTF export",
            ),
            (
                "none",
                "None",
                "Keep every polygon corner unshared without vertex welding",
            ),
        ],
        default="manifold",
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

    target_container: bpy.props.StringProperty(
        name="Target Container",
        description="Name of the root container to rebuild",
        default="",
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
        target_name = getattr(self, "target_container", "")
        if target_name and hasattr(bpy, "data") and hasattr(bpy.data, "objects") and target_name in bpy.data.objects:
            root_container, world_mesh_obj, cloud_obj, source_type = _resolve_mesh_rebuild_targets_from_root(
                bpy.data.objects[target_name]
            )
        else:
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
            allow_defer_precompile=bool(getattr(self, "run_async", True)),
        )
        if prep is None:
            return {"CANCELLED"}
        ctx_data, did_precompile = prep

        if not ctx_data.get("deferred_precompile"):
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
