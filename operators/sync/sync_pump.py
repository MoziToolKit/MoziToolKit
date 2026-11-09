"""
Live Sync main-thread event pump, timer scheduler, and viewport mesh dispatch.
"""

from __future__ import annotations

import logging
import sys
import time
from typing import Any, Optional

import bpy

from ...bridge.sync import get_sync_bridge_session
from ...utils.world.sync_container import (
    _safe_get_custom_prop,
    _safe_set_custom_prop,
    get_or_create_world_container,
    get_or_create_world_mesh_object,
    resolve_world_root_object,
    update_world_mesh,
)
from .properties import (
    get_active_sync_container,
    set_active_sync_container,
)

logger = logging.getLogger("MoziToolKit.Sync.Pump")

_timer_registered = False
_progress_reporter = None
_delayed_close_callback = None
_pending_cloud_sync_time = None


def _get_pump_delegate(name: str, default: Any) -> Any:
    for mod_name in (
        "bl_ext.vscode_development.MoziToolKit.operators.sync.op_sync_connect",
        "MoziToolKit.operators.sync.op_sync_connect",
    ):
        mod = sys.modules.get(mod_name)
        if mod is not None:
            val = getattr(mod, name, None)
            if val is not None:
                return val
    return default


def _get_pending_cloud_sync_time() -> Optional[float]:
    for mod_name in (
        "bl_ext.vscode_development.MoziToolKit.operators.sync.op_sync_connect",
        "MoziToolKit.operators.sync.op_sync_connect",
    ):
        mod = sys.modules.get(mod_name)
        if mod is not None and hasattr(mod, "_pending_cloud_sync_time"):
            return getattr(mod, "_pending_cloud_sync_time")
    return _pending_cloud_sync_time


def _set_pending_cloud_sync_time(val: Optional[float]) -> None:
    global _pending_cloud_sync_time
    _pending_cloud_sync_time = val
    for mod_name in (
        "bl_ext.vscode_development.MoziToolKit.operators.sync.op_sync_connect",
        "MoziToolKit.operators.sync.op_sync_connect",
    ):
        mod = sys.modules.get(mod_name)
        if mod is not None:
            setattr(mod, "_pending_cloud_sync_time", val)


def is_sync_timer_running() -> bool:
    """Checks whether the Live Sync polling timer is currently active."""
    return bpy is not None and hasattr(bpy.app, "timers") and bpy.app.timers.is_registered(_sync_timer_tick)


def start_sync_timer() -> None:
    """Safely registers the main-thread sync polling timer if not already running."""
    global _timer_registered
    if bpy is not None and hasattr(bpy.app, "timers"):
        if not bpy.app.timers.is_registered(_sync_timer_tick):
            bpy.app.timers.register(_sync_timer_tick, first_interval=0.016)
    _timer_registered = True


def stop_sync_timer() -> None:
    """Safely unregisters and cancels the main-thread sync polling timer and pending progress reporters."""
    global _timer_registered, _delayed_close_callback, _pending_cloud_sync_time
    if bpy is not None and hasattr(bpy.app, "timers"):
        if bpy.app.timers.is_registered(_sync_timer_tick):
            try:
                bpy.app.timers.unregister(_sync_timer_tick)
            except Exception as e:
                logger.debug("Failed to unregister sync timer: %s", e)
        if _delayed_close_callback is not None and bpy.app.timers.is_registered(_delayed_close_callback):
            try:
                bpy.app.timers.unregister(_delayed_close_callback)
            except Exception:
                pass
            _delayed_close_callback = None
    _set_pending_cloud_sync_time(None)
    _timer_registered = False
    _close_progress_reporter()


def _get_progress_reporter(title: str = "Mozi Live Sync"):
    global _progress_reporter
    if _progress_reporter is None:
        from ...utils.progress import BlenderProgressReporter
        _progress_reporter = BlenderProgressReporter(bpy.context, title=title)
        _progress_reporter.start()
    else:
        _progress_reporter.title = title
    return _progress_reporter


def _close_progress_reporter(delay_sec: float = 0.0):
    global _progress_reporter, _delayed_close_callback
    if _delayed_close_callback is not None:
        if bpy is not None and hasattr(bpy.app, "timers") and bpy.app.timers.is_registered(_delayed_close_callback):
            try:
                bpy.app.timers.unregister(_delayed_close_callback)
            except Exception:
                pass
        _delayed_close_callback = None

    if _progress_reporter is not None:
        rep = _progress_reporter
        _progress_reporter = None
        if delay_sec > 0 and bpy is not None and hasattr(bpy.app, "timers"):
            def _delayed_close():
                global _delayed_close_callback
                _delayed_close_callback = None
                rep.close()
                return None
            _delayed_close_callback = _delayed_close
            bpy.app.timers.register(_delayed_close, first_interval=delay_sec)
        else:
            rep.close()


def _get_active_props(context: Optional[bpy.types.Context] = None, root_container: Optional[bpy.types.Object] = None) -> Optional[bpy.types.PropertyGroup]:
    """Resolves active live sync properties bound to container or scene."""
    if root_container is not None and hasattr(root_container, "mozi_sync"):
        return root_container.mozi_sync
    ctx = context or getattr(bpy, "context", None)
    if ctx:
        scene = getattr(ctx, "scene", None)
        active_container = get_active_sync_container(scene)
        if active_container and hasattr(active_container, "mozi_sync"):
            return active_container.mozi_sync
        active_obj = getattr(ctx, "active_object", None)
        if active_obj:
            resolved = resolve_world_root_object(active_obj)
            if resolved and hasattr(resolved, "mozi_sync"):
                return resolved.mozi_sync
        if scene and hasattr(scene, "mozi_sync"):
            return scene.mozi_sync
    return None


def _sync_timer_tick() -> Optional[float]:
    """
    Main-thread non-blocking timer polling events from native libmtk engine
    and updating the Blender viewport world mesh of the active bound container.
    """
    global _pending_cloud_sync_time
    get_session_fn = _get_pump_delegate("get_sync_bridge_session", get_sync_bridge_session)
    session = get_session_fn()

    # Drain pending events BEFORE checking is_active. The terminal DISCONNECTED
    # event is queued just before the worker exits; checking is_active first would
    # discard it and leave the panel stuck showing "CONNECTED" while the native
    # session is already dead (clicking Refresh then reports "not connected").
    events = session.poll_events() if session is not None else []
    session_active = bool(session is not None and session.is_active)

    scene = getattr(bpy.context, "scene", None) if bpy.context else None
    get_active_container_fn = _get_pump_delegate("get_active_sync_container", get_active_sync_container)
    active_container = get_active_container_fn(scene)
    if not active_container:
        get_or_create_container_fn = _get_pump_delegate("get_or_create_world_container", get_or_create_world_container)
        active_container = get_or_create_container_fn(bpy.context)
        set_active_container_fn = _get_pump_delegate("set_active_sync_container", set_active_sync_container)
        set_active_container_fn(scene, active_container)

    get_active_props_fn = _get_pump_delegate("_get_active_props", _get_active_props)
    props = getattr(active_container, "mozi_sync", None) or get_active_props_fn(bpy.context, active_container)
    scene_props = getattr(scene, "mozi_sync", None) if scene else None

    def _sync_prop(name: str, val: Any) -> None:
        if props and hasattr(props, name):
            setattr(props, name, val)
        if scene_props and hasattr(scene_props, name):
            setattr(scene_props, name, val)

    latest_world_mesh = None
    latest_generation = 0
    needs_voxel_sync = False
    terminal_status: Optional[str] = None

    for ev in events:
        ev_type = ev.get("type")
        if not ev_type:
            continue

        if ev_type == "WORLD_MESH_READY":
            # Coalesce: only keep newest mesh to prevent Blender event queue choke and OOM
            latest_world_mesh = ev.get("mesh")
            latest_generation = ev.get("generation", 0)
            continue

        if ev_type == "STATUS_CHANGE":
            status = ev.get("status", "DISCONNECTED")
            _sync_prop("connection_status", status)
            if status == "CONNECTED":
                _sync_prop("validation_info", "Connected to Live Sync")
            elif status.startswith("DISCONNECTED"):
                terminal_status = status
                _sync_prop("is_streaming", False)
                _sync_prop("validation_info", status)
                _close_progress_reporter()
            # Intermediate statuses (CONNECTING/RECONNECTING/transport errors) are
            # surfaced by connection_status but must not be treated as "disconnected".

        elif ev_type == "SELECTION_UPDATED":
            _sync_prop("has_selection", True)
            _sync_prop("min_x", ev.get("min_x", 0))
            _sync_prop("min_y", ev.get("min_y", 0))
            _sync_prop("min_z", ev.get("min_z", 0))
            sx = ev.get("size_x", 0)
            sy = ev.get("size_y", 0)
            sz = ev.get("size_z", 0)
            _sync_prop("size_x", sx)
            _sync_prop("size_y", sy)
            _sync_prop("size_z", sz)
            _sync_prop("total_blocks", sx * sy * sz)

        elif ev_type == "HANDSHAKE":
            epoch = ev.get("epoch", 0)
            seq = ev.get("sequence", 0)
            decision = ev.get("decision", 2)
            dimension = ev.get("dimension", "minecraft:overworld")
            _sync_prop("dimension", dimension)
            _sync_prop("validation_info", f"Sync Handshake: decision={decision}, epoch={epoch}, seq={seq}")
            if ev.get("decision_code") == 0 or decision == "VERIFIED_SKIP_REBUILD":
                _sync_prop("sync_verified", True)
                _sync_prop("validation_info", "0ms Instant Reconnect Verified: 100% in sync with scene")
                _sync_prop("is_streaming", False)
                _close_progress_reporter()

        elif ev_type == "DELTA_APPLIED":
            cnt = ev.get("change_count", 0)
            _sync_prop("last_update_info", f"Delta Applied: {cnt} block modification(s)")

            # Add to history
            if props:
                item = props.delta_history.add()
                item.pos_str = f"Delta #{len(props.delta_history)}"
                item.block_state = f"{cnt} blocks modified"
                item.time_str = time.strftime("%H:%M:%S")
                if len(props.delta_history) > 50:
                    props.delta_history.remove(0)

            # Schedule debounced voxel point cloud synchronization (coalesce rapid block changes)
            _set_pending_cloud_sync_time(time.time() + 0.35)

        elif ev_type == "STREAM_PROGRESS":
            stage = ev.get("stage", "sync")
            tot = ev.get("total", 0)
            msg = ev.get("message", "")
            _sync_prop("is_streaming", True)
            _sync_prop("stream_stage", stage)

            # Rust owns the two-phase phase weighting; the host only displays it.
            normalized_curr = int(min(100.0, max(0.0, float(ev.get("percent", 0.0)))))
            _sync_prop("stream_progress_current", normalized_curr)
            _sync_prop("stream_progress_total", 100)
            _sync_prop("stream_message", msg)

            if tot > 0:
                stage_title = (
                    "Live Sync: Download" if stage == "sync_download"
                    else ("Live Sync: Mesh" if stage == "sync_meshing"
                    else ("Live Sync: Request" if stage == "sync_request"
                    else "Live Sync"))
                )
                _get_progress_reporter(title=stage_title).update(normalized_curr, 100, message=msg)

        elif ev_type == "STREAM_FINISHED":
            needs_voxel_sync = True
            _set_pending_cloud_sync_time(None)
            built = ev.get("built_sections", 0)
            _sync_prop("last_update_info", f"Stream Complete ({built} active chunks)")
            _sync_prop("stream_progress_current", 100)
            _sync_prop("stream_progress_total", 100)
            _sync_prop("is_streaming", False)

        elif ev_type == "VERIFIED":
            is_ver = ev.get("is_verified", False)
            _sync_prop("sync_verified", is_ver)
            msg = ev.get("message", "")
            if is_ver:
                _sync_prop("validation_info", msg or "100% in sync with scene")
                _sync_prop("is_streaming", False)
                try:
                    storage = session.get_storage()
                    if storage is not None:
                        from ...utils.materials.biome.updater import (
                            mark_world_biome,
                            register_object_storage,
                        )
                        if mark_world_biome is not None:
                            container_name = getattr(active_container, "name", "")
                            if isinstance(container_name, str) and container_name:
                                register_object_storage(container_name, storage)
                                register_object_storage(f"{container_name}_Mesh", storage)
                            container_id = active_container.get("mtk:container_id") if hasattr(active_container, "get") else None
                            if isinstance(container_id, str) and container_id:
                                register_object_storage(container_id, storage)
                            mark_world_biome(active_container, storage)
                except Exception as e:
                    logger.debug("Failed registering storage on verified: %s", e)
            else:
                _sync_prop("validation_info", f"Out of sync: {msg}" if msg else "Detected out-of-sync sections")

        elif ev_type == "WARNING":
            _sync_prop("validation_info", f"Warning: {ev.get('message', '')}")

        elif ev_type == "ERROR":
            message = ev.get("message", "")
            # Make the #1 failure mode actionable: the token field is not persisted
            # into .blend, so a blank field after reopening looks "identical".
            if "Authentication failed" in message:
                message = f"{message} (fill 'Auth Token' with the value of /yefira config token, then reconnect)"
            _sync_prop("validation_info", f"Error: {message}")
            _sync_prop("is_streaming", False)
            _close_progress_reporter()

    # Authoritative connection state comes from the native session, never from a
    # string comparison against an intermediate status message.
    if session_active:
        native_status = getattr(session, "connection_status", "") or ""
        if isinstance(native_status, str) and native_status:
            _sync_prop("connection_status", native_status)
        _sync_prop("is_connected", bool(getattr(session, "is_connected", False)))

    if latest_world_mesh is not None:
        try:
            curr_gen_raw = _safe_get_custom_prop(active_container, "mtk:client_generation", 0)
            if isinstance(curr_gen_raw, (int, float)):
                curr_gen = int(curr_gen_raw)
            elif isinstance(curr_gen_raw, str):
                try:
                    curr_gen = int(curr_gen_raw)
                except ValueError:
                    curr_gen = 0
            else:
                curr_gen = 0
            if latest_generation >= curr_gen:
                if getattr(latest_world_mesh, "profile", None) is not None:
                    try:
                        from ...bridge.diagnostics import record_operation_profile
                        record_operation_profile("Live Sync Mesh", latest_world_mesh.profile)
                    except Exception:
                        pass
                get_mesh_obj_fn = _get_pump_delegate("get_or_create_world_mesh_object", get_or_create_world_mesh_object)
                world_obj = get_mesh_obj_fn(bpy.context, root_container=active_container)
                # Delta updates deliberately avoid copying the native storage; the
                # initial baseline/point-cloud sync registers it for biome re-tints.
                storage = session.get_storage() if needs_voxel_sync else None
                update_mesh_fn = _get_pump_delegate("update_world_mesh", update_world_mesh)
                v_count, f_count = update_mesh_fn(
                    world_obj,
                    latest_world_mesh,
                    skip_string_attributes=True,
                    storage=storage,
                    sync_point_cloud=needs_voxel_sync,
                    atlas=getattr(session, "atlas", None),
                    session=session,
                )
                _safe_set_custom_prop(active_container, "mtk:client_generation", latest_generation)
                _sync_prop("point_count", v_count)
                _sync_prop("faces_count", f_count)
                _sync_prop("last_update_info", f"World Mesh updated: {v_count:,} vertices, {f_count:,} faces")
                _sync_prop("is_streaming", False)

                if _progress_reporter is not None:
                    _progress_reporter.update(_progress_reporter.total, _progress_reporter.total, message=f"Mesh ready ({v_count:,} v, {f_count:,} f)")
            else:
                logger.debug("Skipping stale world mesh generation %s < %s", latest_generation, curr_gen)
        except Exception as e:
            logger.error("Failed to inject WorldMesh into Blender for container '%s': %s", active_container.name, e)
        finally:
            _close_progress_reporter(delay_sec=1.5)
    elif needs_voxel_sync:
        try:
            storage = session.get_storage()
            if storage is not None:
                from ...utils.point_cloud import ensure_voxel_child_cloud
                ensure_voxel_child_cloud(active_container, storage=storage, origin_centered=True, initial_hidden=True)

                # Cache the storage under the container/mesh names so the biome
                # panel can restore WORLD even if the initial mesh event arrived
                # before this STREAM_FINISHED event.
                from ...utils.materials.biome.updater import (
                    mark_world_biome,
                    register_object_storage,
                )
                if mark_world_biome is not None:
                    container_name = getattr(active_container, "name", "")
                    if isinstance(container_name, str) and container_name:
                        register_object_storage(container_name, storage)
                        register_object_storage(f"{container_name}_Mesh", storage)
                    container_id = active_container.get("mtk:container_id") if hasattr(active_container, "get") else None
                    if isinstance(container_id, str) and container_id:
                        register_object_storage(container_id, storage)
                    mark_world_biome(active_container, storage)
        except Exception as e:
            logger.debug("Failed syncing voxel cloud on event: %s", e)

    if needs_voxel_sync and active_container is not None and session is not None:
        try:
            s_uuid = getattr(session, "server_uuid", "") or ""
            s_hash = getattr(session, "state_hash", 0) or 0
            if s_uuid and s_hash != 0:
                _safe_set_custom_prop(active_container, "mtk_sync_server_uuid", s_uuid)
                _safe_set_custom_prop(active_container, "mtk_sync_epoch", int(getattr(session, "current_epoch", 0)))
                _safe_set_custom_prop(active_container, "mtk_sync_sequence", int(getattr(session, "current_sequence", 0)))
                _safe_set_custom_prop(active_container, "mtk_sync_state_hash", f"0x{s_hash:016x}")
                _safe_set_custom_prop(
                    active_container,
                    "mtk_sync_dimension",
                    getattr(props, "dimension", "minecraft:overworld") or "minecraft:overworld",
                )
                _safe_set_custom_prop(
                    active_container,
                    "mtk_sync_bounds",
                    [
                        int(getattr(props, "min_x", 0)),
                        int(getattr(props, "min_y", 0)),
                        int(getattr(props, "min_z", 0)),
                        int(getattr(props, "size_x", 0)),
                        int(getattr(props, "size_y", 0)),
                        int(getattr(props, "size_z", 0)),
                    ],
                )
                logger.info("Persisted sync session state for container '%s' (hash: 0x%016x)", active_container.name, s_hash)
        except Exception as e:
            logger.debug("Failed persisting sync session state: %s", e)

    # Check debounced point cloud sync (only update if storage is fully populated to avoid wiping existing cloud)
    p_time = _get_pending_cloud_sync_time()
    if p_time is not None and time.time() >= p_time:
        _set_pending_cloud_sync_time(None)
        try:
            storage = session.get_storage()
            non_empty_count = getattr(props, "non_empty_sections", 0) if props else 0
            if storage is not None and hasattr(storage, "get_all_non_empty_sections"):
                storage_non_empty = len(storage.get_all_non_empty_sections())
                # Safety barrier: Do NOT overwrite an existing large point cloud if in-memory storage is sparse/unpopulated
                if storage_non_empty > 0 and (non_empty_count == 0 or storage_non_empty >= non_empty_count):
                    from ...utils.point_cloud import ensure_voxel_child_cloud
                    ensure_voxel_child_cloud(active_container, storage=storage, origin_centered=True, initial_hidden=True)
                    _safe_set_custom_prop(active_container, "mtk_sync_sequence", int(getattr(session, "current_sequence", 0)))
                    logger.debug("Debounced voxel child cloud updated successfully")
                else:
                    logger.debug("Skipping point cloud sync: sparse storage (%s/%s sections)", storage_non_empty, non_empty_count)
        except Exception as e:
            logger.debug("Failed debounced voxel cloud sync: %s", e)

    # If the session ended (fatal disconnect or explicit stop), clear stale UI state
    # and unregister the timer cleanly. This runs after event processing so the final
    # DISCONNECTED event can never be lost.
    if not session_active:
        _close_progress_reporter()
        _sync_prop("is_connected", False)
        _sync_prop("connection_status", "DISCONNECTED")
        _sync_prop("is_streaming", False)
        if terminal_status:
            _sync_prop("validation_info", terminal_status)
        else:
            last_err = getattr(session, "last_error", "") if session is not None else ""
            _sync_prop("validation_info", last_err or "Disconnected")
        stop_fn = _get_pump_delegate("stop_sync_timer", stop_sync_timer)
        stop_fn()
        return None

    return 0.016  # ~60 fps poll interval
