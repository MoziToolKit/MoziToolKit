"""
Target resolution for unified voxel mesh reconstruction.
Resolves (root_container, world_mesh_obj, cloud_obj, source_type) from selection.
"""

from __future__ import annotations

from typing import Any, Callable, Optional, Tuple
import bpy

from ...bridge.sync import get_sync_bridge_session
from ...utils.mesh.ownership import (
    ORIGIN_EXTERNAL,
    ORIGIN_VOXEL,
    classify_origin,
    stamp_geometry_origin,
)


def _safe_set_custom_prop(obj: Any, key: str, val: Any) -> None:
    if obj is None:
        return
    try:
        obj[key] = val
    except Exception:
        pass


def _resolve_mesh_rebuild_targets_from_root(
    root_container: bpy.types.Object,
) -> Tuple[bpy.types.Object, Optional[bpy.types.Object], Optional[bpy.types.Object], str]:
    """Helper to locate child mesh and child point cloud under an Empty root container."""
    container_type = "SAVE"
    if hasattr(root_container, "get"):
        c_type = root_container.get("mtk:container_type")
        if c_type:
            container_type = str(c_type)
        elif root_container.get("mtk:is_yefira_world") or root_container.name.startswith("Yefira_World"):
            container_type = "SYNC"
    elif root_container.name.startswith("Yefira_World"):
        container_type = "SYNC"

    mesh_child = None
    cloud_child = None
    children = getattr(root_container, "children", [])
    for ch in children:
        if getattr(ch, "type", "") == "MESH":
            is_cloud = False
            if hasattr(ch, "get"):
                is_cloud = bool(ch.get("mtk_is_voxel_cloud"))
            if not is_cloud and getattr(ch, "name", "").endswith("_VoxelCloud"):
                is_cloud = True

            if is_cloud:
                cloud_child = ch
            else:
                if mesh_child is None:
                    mesh_child = ch
                elif (
                    classify_origin(mesh_child) == ORIGIN_EXTERNAL
                    and classify_origin(ch) != ORIGIN_EXTERNAL
                ):
                    mesh_child = ch

    if mesh_child is None and hasattr(bpy, "data") and hasattr(bpy.data, "objects"):
        cand = bpy.data.objects.get(f"{root_container.name}_Mesh")
        if cand and getattr(cand, "type", "") == "MESH":
            mesh_child = cand
    if cloud_child is None and hasattr(bpy, "data") and hasattr(bpy.data, "objects"):
        cand = bpy.data.objects.get(f"{root_container.name}_VoxelCloud")
        if cand and getattr(cand, "type", "") == "MESH":
            cloud_child = cand

    return root_container, mesh_child, cloud_child, container_type


def resolve_mesh_rebuild_targets(
    context: Optional[bpy.types.Context],
) -> Tuple[Optional[bpy.types.Object], Optional[bpy.types.Object], Optional[bpy.types.Object], str]:
    """
    Resolves (root_container, world_mesh_obj, cloud_obj, source_type) from context selection.
    source_type can be:
      - "SYNC": Live Sync world container or section mesh
      - "SAVE": Minecraft Save container or mesh
      - "CLOUD": Standalone or companion Voxel Point Cloud
      - "NONE": Unrecognized or non-voxel object
    """
    if context is None:
        return None, None, None, "NONE"

    active = getattr(context, "active_object", None)
    if not active:
        selected = getattr(context, "selected_objects", [])
        active = selected[0] if selected else None

    if not active:
        return None, None, None, "NONE"

    # 1. Target is an Empty container root (SYNC or SAVE)
    if getattr(active, "type", "") == 'EMPTY':
        return _resolve_mesh_rebuild_targets_from_root(active)

    # 2. Target is a Mesh object
    if getattr(active, "type", "") == "MESH":
        parent = getattr(active, "parent", None)
        if parent and getattr(parent, "type", "") == 'EMPTY':
            is_cont = False
            if hasattr(parent, "get"):
                is_cont = bool(parent.get("mtk:is_container") or parent.get("mtk:is_yefira_world"))
            if is_cont or parent.name.startswith("Save_") or parent.name.startswith("Yefira_World") or parent.name.startswith("[Scene]"):
                c_root, c_mesh, c_cloud, c_type = _resolve_mesh_rebuild_targets_from_root(parent)
                if getattr(active, "name", "").endswith("_VoxelCloud") or (hasattr(active, "get") and active.get("mtk_is_voxel_cloud")):
                    c_cloud = active
                else:
                    c_mesh = active
                return c_root, c_mesh, c_cloud, c_type

        is_cloud = False
        if hasattr(active, "get"):
            is_cloud = bool(active.get("mtk_is_voxel_cloud"))
        if not is_cloud and getattr(active, "name", "").endswith("_VoxelCloud"):
            is_cloud = True

        if is_cloud:
            cloud_obj = active
            world_mesh = None
            if hasattr(active, "get"):
                w_name = active.get("mtk_world_mesh")
                if w_name and hasattr(bpy, "data") and hasattr(bpy.data, "objects"):
                    world_mesh = bpy.data.objects.get(w_name)
            if world_mesh is None and active.name.endswith("_VoxelCloud"):
                base = active.name[:-11]
                for cand_name in (f"{base}_Mesh", base):
                    if hasattr(bpy, "data") and hasattr(bpy.data, "objects"):
                        cand = bpy.data.objects.get(cand_name)
                        if cand and getattr(cand, "type", "") == "MESH":
                            world_mesh = cand
                            break
            return None, world_mesh, cloud_obj, "CLOUD"

        cloud_obj = None
        if hasattr(active, "get"):
            c_name = active.get("mtk_voxel_cloud")
            if c_name and hasattr(bpy, "data") and hasattr(bpy.data, "objects"):
                cloud_obj = bpy.data.objects.get(c_name)
        if cloud_obj is None and hasattr(bpy, "data") and hasattr(bpy.data, "objects"):
            conv_name = f"{active.name}_VoxelCloud"
            cand = bpy.data.objects.get(conv_name)
            if cand and getattr(cand, "type", "") == "MESH":
                cloud_obj = cand
            elif active.name.endswith("_Mesh"):
                base = active.name[:-5]
                cand = bpy.data.objects.get(f"{base}_VoxelCloud")
                if cand and getattr(cand, "type", "") == "MESH":
                    cloud_obj = cand

        is_sync = False
        if hasattr(active, "get") and (active.get("mtk:is_yefira_mesh") or active.get("mtk:is_yefira_world")):
            is_sync = True
        elif active.name.startswith("Yefira_World"):
            is_sync = True

        if is_sync:
            return None, active, cloud_obj, "SYNC"

        if cloud_obj is not None:
            return None, active, cloud_obj, "SAVE"

    return None, None, None, "NONE"


def _live_session_matches_container(
    context: Optional[Any],
    root_container: Optional[bpy.types.Object],
    get_session_fn: Optional[Callable[[], Any]] = None,
) -> bool:
    """Return True when an active Live Sync session owns this container."""
    fn = get_session_fn if get_session_fn is not None else get_sync_bridge_session
    session = fn() if callable(fn) else None
    if not session or not getattr(session, "is_active", False):
        return False

    scene = getattr(context, "scene", None) if context is not None else None
    active_name = getattr(scene, "mozi_active_sync_container_name", "") if scene is not None else ""
    if not isinstance(active_name, str):
        active_name = ""

    if root_container is not None and active_name:
        return getattr(root_container, "name", "") == active_name
    return True


def get_or_create_container_mesh(
    context: Any,
    root_container: bpy.types.Object,
) -> bpy.types.Object:
    """Creates a new mesh object parented under root_container, stamped with voxel origin."""
    mesh_name = f"{root_container.name}_Mesh"
    b_mesh = bpy.data.meshes.new(mesh_name)
    world_mesh_obj = bpy.data.objects.new(mesh_name, b_mesh)
    if hasattr(world_mesh_obj, "parent"):
        world_mesh_obj.parent = root_container
    target_coll = context.collection if context and context.collection else (
        bpy.context.scene.collection if hasattr(bpy.context, "scene") else None
    )
    if target_coll and hasattr(target_coll, "objects") and hasattr(target_coll.objects, "link"):
        target_coll.objects.link(world_mesh_obj)

    root_origin = classify_origin(root_container)
    if root_origin == ORIGIN_EXTERNAL:
        root_origin = ORIGIN_VOXEL
    stamp_geometry_origin(world_mesh_obj, root_origin, overwrite=True)
    return world_mesh_obj
