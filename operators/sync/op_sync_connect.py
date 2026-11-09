"""
Operators for connecting, disconnecting, and synchronizing with Live Sync WebSocket server.
Supports multi-container isolation with single active session exclusivity and switching.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Optional
import bpy

from ...bridge.sync import (
    get_sync_bridge_session,
    is_sync_available,
    load_atlas_from_cache,
    load_model_database_from_cache,
    load_biome_resolver_from_cache,
)
from ...bridge.assets import invalidate_asset_cache_memory
from ...utils.materials.builder.atlas_refresh import reload_atlas_images
from ...utils.assets import (
    can_execute_asset_pipeline,
    check_asset_cache_status,
    ensure_pipeline_assets_ready_sync,
)
from ...utils.world.sync_container import (
    _safe_get_custom_prop,
    _safe_set_custom_prop,
    get_or_create_world_container,
    get_or_create_world_mesh_object,
    find_world_mesh_child,
    resolve_world_root_object,
    update_world_mesh,
)
from .properties import (
    get_active_sync_container,
    set_active_sync_container,
)

from .sync_pump import (
    _close_progress_reporter,
    _get_active_props,
    _get_progress_reporter,
    _sync_timer_tick,
    is_sync_timer_running,
    start_sync_timer,
    stop_sync_timer,
)

logger = logging.getLogger("MoziToolKit.Sync.Connect")

_pending_cloud_sync_time: Optional[float] = None


class MOZI_OT_sync_connect(bpy.types.Operator):
    """Connect to Minecraft Live Sync streaming server for the active or specified container."""
    bl_idname = "mozi.sync_connect"
    bl_label = "Connect"
    bl_description = "Connect to Minecraft Live Sync WebSocket Server"
    bl_options = {'REGISTER', 'UNDO'}

    target_container: bpy.props.StringProperty(
        name="Target Container",
        description="Name of the root Empty container to bind",
        default="",
    )

    @classmethod
    def poll(cls, context):
        from ...utils.system import get_prefs

        prefs = get_prefs(context)
        can_run, reason = can_execute_asset_pipeline(prefs)
        if not can_run:
            if hasattr(cls, "poll_message_set"):
                cls.poll_message_set(reason)
            return False
        return True

    def invoke(self, context, event):
        target_obj = None
        if self.target_container and self.target_container in bpy.data.objects:
            target_obj = bpy.data.objects[self.target_container]
        else:
            active_obj = getattr(context, "active_object", None)
            target_obj = resolve_world_root_object(active_obj) if active_obj else None
            if not target_obj:
                target_obj = get_or_create_world_container(context)
            self.target_container = target_obj.name

        session = get_sync_bridge_session()
        curr_active = get_active_sync_container(context.scene)

        # If another container is currently actively syncing, ask for confirmation to switch
        if session.is_active and curr_active and curr_active != target_obj:
            curr_props = getattr(curr_active, "mozi_sync", None)
            if curr_props and (curr_props.is_connected or curr_props.connection_status.startswith("CONNECTING")):
                return context.window_manager.invoke_confirm(
                    self,
                    event,
                    message=f"Container '{curr_active.name}' is actively syncing. Disconnect it and switch to '{target_obj.name}'?",
                )

        return self.execute(context)

    def execute(self, context):
        if not is_sync_available():
            self.report({'ERROR'}, "Native libmtk core is missing or not installed!")
            return {'CANCELLED'}

        target_obj = None
        if self.target_container and self.target_container in bpy.data.objects:
            target_obj = bpy.data.objects[self.target_container]
        else:
            active_obj = getattr(context, "active_object", None)
            target_obj = resolve_world_root_object(active_obj) if active_obj else None
            if not target_obj:
                target_obj = get_or_create_world_container(context)
            self.target_container = target_obj.name

        session = get_sync_bridge_session()
        curr_active = get_active_sync_container(context.scene)

        # If switching from an existing active container, gracefully stop old session and freeze it
        if session.is_active and curr_active and curr_active != target_obj:
            stop_sync_timer()
            session.stop()
            if hasattr(curr_active, "mozi_sync"):
                curr_active.mozi_sync.is_connected = False
                curr_active.mozi_sync.connection_status = "DISCONNECTED"
                curr_active.mozi_sync.validation_info = "Disconnected (switched to another container)"
            logger.info(f"Detached active sync from '{curr_active.name}' to switch to '{target_obj.name}'")

        set_active_sync_container(context.scene, target_obj)

        # A new native session restarts its mesh generation counter at 1. Clear any
        # generation persisted in the .blend from a previous session, otherwise
        # every WORLD_MESH_READY would be treated as stale and silently discarded
        # (the viewport then never receives voxel data until the file is recreated).
        _safe_set_custom_prop(target_obj, "mtk:client_generation", 0)

        props = getattr(target_obj, "mozi_sync", None) or _get_active_props(context, target_obj)
        url = props.url if props and props.url else "ws://127.0.0.1:8765"

        from ...utils.system import get_prefs
        prefs = get_prefs(context)

        # Asset readiness gate: verify valid vanilla client base pack before proceeding
        can_run, reason = can_execute_asset_pipeline(prefs)
        if not can_run:
            self.report({'WARNING'}, reason)
            return {'CANCELLED'}

        # Asset readiness gate
        status = check_asset_cache_status(prefs)
        if status in ("NO_PACKS", "NO_VANILLA"):
            self.report({'WARNING'}, reason)
            return {'CANCELLED'}
        if status in ("MISSING", "STALE"):
            self.report({'INFO'}, f"Asset cache is {status.lower()}; automatically precompiling before Live Sync connection...")
            success, _ = ensure_pipeline_assets_ready_sync(prefs)
            if not success:
                self.report({'ERROR'}, "Failed to precompile assets before connection.")
                return {'CANCELLED'}
            invalidate_asset_cache_memory()
            try:
                reload_atlas_images(prefs)
            except Exception:
                pass

        model_db = load_model_database_from_cache(prefs)
        atlas = load_atlas_from_cache(prefs)
        biome_resolver = load_biome_resolver_from_cache(prefs)

        try:
            from ...utils.assets.cache_utils import get_cache_dir
            skin_cache = get_cache_dir() / "skins"
            skin_cache.mkdir(parents=True, exist_ok=True)
            session.set_skin_cache_dir(str(skin_cache))
        except Exception as e:
            logger.debug("Failed configuring skin cache dir on connect: %s", e)

        # Check for 0ms instant reconnect capability
        can_fast_reconnect = False
        cached_uuid = _safe_get_custom_prop(target_obj, "mtk_sync_server_uuid", "")
        cached_epoch = _safe_get_custom_prop(target_obj, "mtk_sync_epoch", 0)
        cached_seq = _safe_get_custom_prop(target_obj, "mtk_sync_sequence", 0)
        cached_hash_val = _safe_get_custom_prop(target_obj, "mtk_sync_state_hash", 0)
        cached_dim = _safe_get_custom_prop(target_obj, "mtk_sync_dimension", "minecraft:overworld") or "minecraft:overworld"
        cached_bounds = _safe_get_custom_prop(target_obj, "mtk_sync_bounds", None)

        cached_hash = 0
        if cached_hash_val:
            try:
                if isinstance(cached_hash_val, str):
                    cached_hash = int(cached_hash_val, 16) if cached_hash_val.startswith("0x") else int(cached_hash_val)
                elif isinstance(cached_hash_val, int):
                    cached_hash = cached_hash_val
            except Exception:
                cached_hash = 0

        min_pos = (0, 0, 0)
        size = (0, 0, 0)
        if cached_bounds and len(cached_bounds) == 6:
            min_pos = (int(cached_bounds[0]), int(cached_bounds[1]), int(cached_bounds[2]))
            size = (int(cached_bounds[3]), int(cached_bounds[4]), int(cached_bounds[5]))

        from ...utils.point_cloud import get_associated_voxel_cloud
        from ...bridge.point_cloud.extract import extract_voxel_point_cloud

        cloud_obj = get_associated_voxel_cloud(target_obj)
        point_cloud = None
        if cloud_obj is not None and cached_uuid and cached_hash != 0 and size[0] > 0 and size[1] > 0 and size[2] > 0:
            try:
                point_cloud = extract_voxel_point_cloud(cloud_obj)
                if point_cloud is not None and hasattr(point_cloud, "compute_sparse_state_hash"):
                    computed_hash = point_cloud.compute_sparse_state_hash(min_pos, size, cached_dim)
                    if computed_hash == cached_hash:
                        can_fast_reconnect = True
                        logger.info("0ms instant reconnect: Point cloud state hash verified (0x%016x)", computed_hash)
                    else:
                        logger.info(
                            "Point cloud hash mismatch (0x%016x != 0x%016x), falling back to full baseline",
                            computed_hash,
                            cached_hash,
                        )
            except Exception as e:
                logger.debug("Failed computing point cloud sparse hash for fast reconnect: %s", e)
                can_fast_reconnect = False

        if can_fast_reconnect and point_cloud is not None:
            success = session.start_with_cache(
                url=url,
                auto_reconnect=True,
                max_reconnect_attempts=5,
                server_uuid=cached_uuid,
                epoch=int(cached_epoch),
                sequence=int(cached_seq),
                min_pos=min_pos,
                size=size,
                state_hash=cached_hash,
                dimension=cached_dim,
                point_cloud=point_cloud,
                model_db=model_db,
                atlas=atlas,
                biome_resolver=biome_resolver,
                auth_token=(getattr(props, "auth_token", "") or None) if props else None,
            )
        else:
            success = session.start(
                url=url,
                auto_reconnect=True,
                max_reconnect_attempts=5,
                model_db=model_db,
                atlas=atlas,
                biome_resolver=biome_resolver,
                auth_token=(getattr(props, "auth_token", "") or None) if props else None,
            )

        if not success:
            err = session.last_error or "Check system console for details"
            self.report({'ERROR'}, f"Failed to initiate connection to {url}: {err}")
            return {'CANCELLED'}

        if props:
            props.is_connected = False
            props.connection_status = "CONNECTING..."
            props.validation_info = f"Connecting to {url}..."
            props.is_streaming = False

        scene_props = getattr(context.scene, "mozi_sync", None)
        if scene_props:
            scene_props.is_connected = False
            scene_props.connection_status = "CONNECTING..."
            scene_props.validation_info = f"Connecting to {url}..."
            scene_props.is_streaming = False

        start_sync_timer()
        self.report({'INFO'}, f"Connecting Live Sync for '{target_obj.name}' at {url}")
        return {'FINISHED'}


class MOZI_OT_sync_disconnect(bpy.types.Operator):
    """Disconnect from Minecraft Live Sync streaming server."""
    bl_idname = "mozi.sync_disconnect"
    bl_label = "Disconnect"
    bl_description = "Disconnect Live Sync session and stop background listener"
    bl_options = {'REGISTER', 'UNDO'}

    target_container: bpy.props.StringProperty(
        name="Target Container",
        description="Name of the root Empty container to disconnect",
        default="",
    )

    def execute(self, context):
        stop_sync_timer()
        session = get_sync_bridge_session()

        target_obj = None
        if self.target_container and self.target_container in bpy.data.objects:
            target_obj = bpy.data.objects[self.target_container]
        else:
            target_obj = get_active_sync_container(context.scene) or resolve_world_root_object(getattr(context, "active_object", None))

        session.stop()

        if target_obj and hasattr(target_obj, "mozi_sync"):
            props = target_obj.mozi_sync
            props.is_connected = False
            props.connection_status = "DISCONNECTED"
            props.validation_info = "Disconnected"
            props.is_streaming = False

        scene_props = getattr(context.scene, "mozi_sync", None)
        if scene_props:
            scene_props.is_connected = False
            scene_props.connection_status = "DISCONNECTED"
            scene_props.validation_info = "Disconnected"
            scene_props.is_streaming = False

        set_active_sync_container(context.scene, None)
        self.report({'INFO'}, "Live Sync Disconnected.")
        return {'FINISHED'}


class MOZI_OT_sync_refresh(bpy.types.Operator):
    """Request server to resend active selection snapshot."""
    bl_idname = "mozi.sync_refresh"
    bl_label = "Refresh Data"
    bl_description = "Request a full snapshot resync from the server"
    bl_options = {'REGISTER', 'UNDO'}

    target_container: bpy.props.StringProperty(
        name="Target Container",
        description="Name of the root Empty container to refresh",
        default="",
    )

    def execute(self, context):
        session = get_sync_bridge_session()

        target_obj = None
        if self.target_container and self.target_container in bpy.data.objects:
            target_obj = bpy.data.objects[self.target_container]
        else:
            target_obj = get_active_sync_container(context.scene) or resolve_world_root_object(getattr(context, "active_object", None))

        props = getattr(target_obj, "mozi_sync", None) or getattr(context.scene, "mozi_sync", None)

        # Refresh the UI state from the native session before deciding, so a stale
        # panel (e.g. leftover CONNECTED text after a silent disconnect) cannot
        # produce a misleading "not connected" report.
        session.poll_events()
        is_active = bool(getattr(session, "is_active", False))
        is_connected = bool(getattr(session, "is_connected", False))
        status = str(getattr(session, "connection_status", "") or "DISCONNECTED")

        if not is_active:
            stop_sync_timer()
            if props:
                props.is_connected = False
                props.connection_status = "DISCONNECTED"
                props.is_streaming = False
                props.validation_info = "Disconnected"
            self.report({'WARNING'}, "Live Sync is not connected.")
            return {'CANCELLED'}

        if not is_connected:
            if props:
                props.connection_status = status
                props.is_connected = False
            self.report(
                {'WARNING'},
                f"Live Sync is not ready yet ({status}). Please wait for the connection to be restored.",
            )
            return {'CANCELLED'}

        if props:
            props.is_streaming = True
            props.stream_stage = "sync_request"
            props.stream_message = "Requesting snapshot from server..."
            props.stream_progress_current = 0
            props.stream_progress_total = 100

        reporter = _get_progress_reporter(title="Live Sync: Request")
        reporter.update(0, 100, message="Requesting snapshot from server...")

        if not session.request_resync():
            _close_progress_reporter()
            if props:
                props.is_streaming = False
                props.validation_info = "Failed to send snapshot request"
            self.report({'ERROR'}, "Failed to request a snapshot from the server.")
            return {'CANCELLED'}

        self.report({'INFO'}, "Full snapshot requested from server.")
        return {'FINISHED'}


OPERATOR_CLASSES = (
    MOZI_OT_sync_connect,
    MOZI_OT_sync_disconnect,
    MOZI_OT_sync_refresh,
)
