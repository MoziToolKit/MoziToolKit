"""
Properties definition for Live Sync real-time synchronization state.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional, Tuple
import bpy
from bpy.props import (
    BoolProperty,
    CollectionProperty,
    EnumProperty,
    IntProperty,
    PointerProperty,
    StringProperty,
)

logger = logging.getLogger("MoziToolKit.Sync.Properties")

NO_IPC_INSTANCE_ID = "__NONE__"
_IPC_DISCOVERY_TTL_SECONDS = 0.5
_IPC_CACHE_TIME: float = 0.0
_IPC_INSTANCES_CACHE: List[Dict[str, Any]] = []
_IPC_ENUM_ITEMS_CACHE: List[Tuple[str, str, str]] = [
    (
        NO_IPC_INSTANCE_ID,
        "No Running Minecraft Instance",
        "Load a world in Minecraft with Yefira installed to appear here",
    )
]


def refresh_ipc_instances_cache(force: bool = False) -> List[Dict[str, Any]]:
    """Refreshes the cached list of locally running Minecraft IPC instances."""
    global _IPC_CACHE_TIME, _IPC_INSTANCES_CACHE, _IPC_ENUM_ITEMS_CACHE
    now = time.monotonic()
    if not force and (now - _IPC_CACHE_TIME) < _IPC_DISCOVERY_TTL_SECONDS:
        return _IPC_INSTANCES_CACHE

    from ...bridge.sync import discover_local_sync_instances

    instances = discover_local_sync_instances()
    _IPC_INSTANCES_CACHE = list(instances)
    _IPC_CACHE_TIME = now

    items: List[Tuple[str, str, str]] = []
    for inst in _IPC_INSTANCES_CACHE:
        ipc_url = str(inst.get("ipc_url") or "")
        if not ipc_url:
            continue
        world = str(inst.get("world_name") or "").strip() or "Minecraft"
        dim = str(inst.get("dimension") or "minecraft:overworld")
        dim_short = dim.split(":", 1)[-1]
        pid = inst.get("pid", 0)
        mc_ver = str(inst.get("mc_version") or "").strip()
        loader = str(inst.get("loader") or "").strip()
        requires_token = bool(inst.get("requires_token", False))

        meta_parts = [p for p in (loader, mc_ver) if p]
        meta_str = f" {' '.join(meta_parts)}" if meta_parts else ""
        token_tag = " [Token]" if requires_token else ""
        label = f"{world} ({dim_short}) [PID {pid}]{token_tag}"
        desc = f"{world}{meta_str} — {ipc_url}"
        items.append((ipc_url, label, desc))

    if not items:
        items = [
            (
                NO_IPC_INSTANCE_ID,
                "No Running Minecraft Instance",
                "Load a world in Minecraft with Yefira installed to appear here",
            )
        ]

    _IPC_ENUM_ITEMS_CACHE = items
    return _IPC_INSTANCES_CACHE


def get_discovered_ipc_instance(ipc_url: str) -> Optional[Dict[str, Any]]:
    """Returns cached metadata dict for `ipc_url`, if currently running."""
    if not ipc_url or ipc_url == NO_IPC_INSTANCE_ID:
        return None
    for inst in refresh_ipc_instances_cache(force=False):
        if inst.get("ipc_url") == ipc_url:
            return inst
    return None


def _ipc_instance_items(_self, _context):
    refresh_ipc_instances_cache(force=False)
    return _IPC_ENUM_ITEMS_CACHE


SYNC_MODE_ITEMS = (
    (
        "LOCAL_IPC",
        "Local IPC",
        "Connect directly to a running local Minecraft instance via IPC (no network port)",
    ),
    (
        "REMOTE_WS",
        "Remote WebSocket",
        "Connect to a local or remote Minecraft server over WebSocket",
    ),
)


class MoziSyncPaletteItem(bpy.types.PropertyGroup):
    """Single item in the block palette."""
    __slots__ = ()

    name: StringProperty(name="BlockState", default="")
    block_count: IntProperty(name="Count", default=0)


class MoziSyncDeltaItem(bpy.types.PropertyGroup):
    """Single modification log entry in delta history."""
    __slots__ = ()

    pos_str: StringProperty(name="Position", default="")
    block_state: StringProperty(name="BlockState", default="")
    time_str: StringProperty(name="Time", default="")


class MoziSyncProperties(bpy.types.PropertyGroup):
    """Scene and object level properties for Live Sync state tracking and UI binding."""
    __slots__ = ()

    sync_mode: EnumProperty(
        name="Sync Mode",
        description="Choose local direct IPC or remote WebSocket synchronization",
        items=SYNC_MODE_ITEMS,
        default="LOCAL_IPC",
    )

    ipc_instance: EnumProperty(
        name="Minecraft Instance",
        description="Running local Minecraft instance with Yefira IPC enabled",
        items=_ipc_instance_items,
    )

    url: StringProperty(
        name="Server URL",
        description="WebSocket address of the Live Sync streaming server",
        default="ws://127.0.0.1:21924",
    )

    auth_token: StringProperty(
        name="Auth Token",
        description="Shared secret required by non-loopback Yefira servers",
        default="",
        subtype="PASSWORD",
        # Never persist this secret into .blend files.
        options={"SKIP_SAVE"},
    )

    is_connected: BoolProperty(
        name="Is Connected",
        description="Whether Live Sync client is actively connected",
        default=False,
    )

    connection_status: StringProperty(
        name="Connection Status",
        default="DISCONNECTED",
    )

    dimension: StringProperty(
        name="World Dimension",
        description="Minecraft dimension identifier of the active selection",
        default="minecraft:overworld",
    )

    has_selection: BoolProperty(
        name="Has Selection",
        default=False,
    )

    # World bounds (in block coordinates)
    min_x: IntProperty(name="Min X", default=0)
    min_y: IntProperty(name="Min Y", default=0)
    min_z: IntProperty(name="Min Z", default=0)
    size_x: IntProperty(name="Size X", default=0)
    size_y: IntProperty(name="Size Y", default=0)
    size_z: IntProperty(name="Size Z", default=0)

    total_blocks: IntProperty(name="Total Blocks", default=0)
    total_sections: IntProperty(name="Total Sections", default=0)
    non_empty_sections: IntProperty(name="Active Sections", default=0)

    # Geometry statistics
    point_count: IntProperty(name="Vertices", default=0)
    faces_count: IntProperty(name="Faces", default=0)

    # Verification and status messages
    sync_verified: BoolProperty(name="Sync Verified", default=False)
    validation_info: StringProperty(name="Validation Status", default="Ready to connect")
    last_update_info: StringProperty(name="Last Update", default="No updates received yet.")

    # Streaming progress
    is_streaming: BoolProperty(name="Is Streaming", default=False)
    stream_stage: StringProperty(name="Stream Stage", default="")
    stream_progress_current: IntProperty(name="Stream Current", default=0)
    stream_progress_total: IntProperty(name="Stream Total", default=0)
    stream_message: StringProperty(name="Stream Message", default="")

    # Lists
    palette_list: CollectionProperty(type=MoziSyncPaletteItem)
    palette_active_index: IntProperty(name="Active Palette Index", default=0)

    delta_history: CollectionProperty(type=MoziSyncDeltaItem)
    delta_active_index: IntProperty(name="Active Delta Index", default=0)


@bpy.app.handlers.persistent
def _on_blend_file_pre_load(dummy=None):
    """Disconnect Live Sync client, cancel timers, and reset session before loading a new blend file."""
    try:
        from .op_sync_connect import stop_sync_timer
        stop_sync_timer()
    except Exception as e:
        logger.debug(f"Error stopping sync timer on file pre-load: {e}")

    try:
        from ...bridge.sync import reset_sync_bridge_session
        reset_sync_bridge_session()
    except Exception as e:
        logger.debug(f"Error resetting sync session on file pre-load: {e}")

    try:
        from ...utils.materials.biome.updater import clear_biome_updater_cache
        clear_biome_updater_cache()
    except Exception as e:
        logger.debug(f"Error clearing biome updater cache on file pre-load: {e}")


def get_active_sync_container(scene: Optional[bpy.types.Scene] = None) -> Optional[bpy.types.Object]:
    """Resolves the currently active Live Sync Empty root container object."""
    if scene is None:
        scene = getattr(bpy.context, "scene", None)
    if not scene:
        return None
    container_name = getattr(scene, "mozi_active_sync_container_name", "")
    if isinstance(container_name, str) and container_name and container_name in bpy.data.objects:
        obj = bpy.data.objects[container_name]
        try:
            from ...utils.world.sync_container import is_yefira_root_object
            if is_yefira_root_object(obj):
                return obj
        except Exception:
            return obj

    # Fallback: find any connected container or container tagged with sync
    objs = bpy.data.objects.values() if hasattr(bpy.data.objects, "values") else bpy.data.objects
    for obj in objs:
        if isinstance(obj, str):
            obj = bpy.data.objects.get(obj)
        if obj is None:
            continue
        obj_name = getattr(obj, "name", "")
        if obj_name.endswith(("_VoxelCloud", "_Mesh")):
            continue
        if getattr(obj, "type", "") == 'EMPTY' and (
            (hasattr(obj, "get") and obj.get("mtk:is_yefira_world")) or obj_name.startswith("Yefira_World")
        ):
            props = getattr(obj, "mozi_sync", None)
            if props and getattr(props, "is_connected", False):
                return obj
    return None


def set_active_sync_container(scene: Optional[bpy.types.Scene], container: Optional[bpy.types.Object]) -> None:
    """Sets or clears the active Live Sync container for the scene."""
    if not scene:
        return
    if hasattr(scene, "mozi_active_sync_container_name"):
        scene.mozi_active_sync_container_name = container.name if container else ""
    else:
        try:
            scene["mozi_active_sync_container_name"] = container.name if container else ""
        except Exception:
            pass


@bpy.app.handlers.persistent
def _on_blend_file_loaded(dummy=None):
    """Reset properties across all scenes and objects upon file load."""
    try:
        for scene in bpy.data.scenes:
            if hasattr(scene, "mozi_sync"):
                props = scene.mozi_sync
                props.is_connected = False
                props.connection_status = "DISCONNECTED"
                props.validation_info = "Ready to connect"
                props.is_streaming = False
            if hasattr(scene, "mozi_active_sync_container_name"):
                scene.mozi_active_sync_container_name = ""
        for obj in bpy.data.objects:
            if hasattr(obj, "mozi_sync"):
                props = obj.mozi_sync
                props.is_connected = False
                props.connection_status = "DISCONNECTED"
                props.is_streaming = False
    except Exception as e:
        logger.debug(f"Error resetting properties on file load: {e}")


CLASSES = (
    MoziSyncPaletteItem,
    MoziSyncDeltaItem,
    MoziSyncProperties,
)


def register():
    for cls in CLASSES:
        try:
            bpy.utils.register_class(cls)
        except ValueError:
            pass
    bpy.types.Scene.mozi_sync = PointerProperty(type=MoziSyncProperties)
    bpy.types.Scene.mozi_active_sync_container_name = StringProperty(
        name="Active Sync Container",
        description="Name of the root Empty container actively bound to Live Sync",
        default="",
    )
    bpy.types.Object.mozi_sync = PointerProperty(type=MoziSyncProperties)

    if _on_blend_file_pre_load not in bpy.app.handlers.load_pre:
        bpy.app.handlers.load_pre.append(_on_blend_file_pre_load)
    if _on_blend_file_loaded not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_on_blend_file_loaded)


def unregister():
    if _on_blend_file_pre_load in bpy.app.handlers.load_pre:
        bpy.app.handlers.load_pre.remove(_on_blend_file_pre_load)
    if _on_blend_file_loaded in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_on_blend_file_loaded)

    try:
        from .op_sync_connect import stop_sync_timer
        stop_sync_timer()
    except Exception:
        pass

    try:
        from ...bridge.sync import reset_sync_bridge_session
        reset_sync_bridge_session()
    except Exception:
        pass

    try:
        from ...utils.materials.biome.updater import clear_biome_updater_cache
        clear_biome_updater_cache()
    except Exception:
        pass

    if hasattr(bpy.types.Object, "mozi_sync"):
        del bpy.types.Object.mozi_sync
    if hasattr(bpy.types.Scene, "mozi_sync"):
        del bpy.types.Scene.mozi_sync
    if hasattr(bpy.types.Scene, "mozi_active_sync_container_name"):
        del bpy.types.Scene.mozi_active_sync_container_name

    for cls in reversed(CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except Exception:
            pass
