"""
In-memory registry and storage caching for biome updating.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger("MoziToolKit.Materials.Biome.Registry")

# In-memory cache for original face tint types (to prevent RNA allocation overhead and preserve base categories)
_MESH_BASE_TINT_TYPES: dict[str, Any] = {}
_WORLD_BIOME_SNAPSHOTS: dict[str, tuple[int, int, Any, Any]] = {}

# In-memory storage registry to avoid serializing unpicklable native VoxelStorage into bpy properties
_STORAGE_CACHE: dict[str, Any] = {}
_COLORMAPS_CACHE: dict[str, Any] = {}


def clear_biome_updater_cache() -> None:
    """Clear in-memory caches for mesh tint types, voxel storage registry, and colormaps."""
    _MESH_BASE_TINT_TYPES.clear()
    _WORLD_BIOME_SNAPSHOTS.clear()
    _STORAGE_CACHE.clear()
    _COLORMAPS_CACHE.clear()


def invalidate_mesh_biome_cache(mesh_or_name: Any = None) -> None:
    """Invalidate cached base tint types and world biome snapshots for one mesh or all meshes."""
    if mesh_or_name is None:
        _MESH_BASE_TINT_TYPES.clear()
        _WORLD_BIOME_SNAPSHOTS.clear()
        return
    name = mesh_or_name if isinstance(mesh_or_name, str) else getattr(mesh_or_name, "name", "")
    if name:
        _MESH_BASE_TINT_TYPES.pop(name, None)
        _WORLD_BIOME_SNAPSHOTS.pop(name, None)


def snapshot_world_biome_buffers(mesh: Any) -> None:
    """Snapshot mesher-baked WORLD biome tint data (FACE) and 4-corner colors (CORNER) on mesh."""
    if mesh is None or not hasattr(mesh, "attributes") or not hasattr(mesh, "polygons"):
        return
    mesh_name = getattr(mesh, "name", "")
    if not mesh_name:
        return
    num_polys = len(mesh.polygons)
    if num_polys == 0:
        return
    attr_data = mesh.attributes.get("mtk_biome_tint_data")
    attr_col = mesh.attributes.get("mtk_biome_tint_color")
    if (
        attr_data is None
        or attr_col is None
        or len(attr_data.data) != num_polys
        or not hasattr(attr_data.data, "foreach_get")
        or not hasattr(attr_col.data, "foreach_get")
    ):
        return
    import numpy as np

    td = np.empty(num_polys * 4, dtype=np.float32)
    attr_data.data.foreach_get("color", td)
    col_elems = len(attr_col.data)
    tc = np.empty(col_elems * 4, dtype=np.float32)
    attr_col.data.foreach_get("color", tc)
    _WORLD_BIOME_SNAPSHOTS[mesh_name] = (num_polys, col_elems, td, tc)


def get_world_biome_snapshot(mesh: Any) -> Optional[tuple[Any, Any]]:
    """Return `(packed_tint_data_flat, tint_colors_flat)` if a valid WORLD snapshot exists for `mesh`."""
    if mesh is None or not hasattr(mesh, "polygons") or not hasattr(mesh, "attributes"):
        return None
    mesh_name = getattr(mesh, "name", "")
    snap = _WORLD_BIOME_SNAPSHOTS.get(mesh_name)
    if snap is None:
        return None
    snap_polys, snap_col_elems, td, tc = snap
    num_polys = len(mesh.polygons)
    attr_col = mesh.attributes.get("mtk_biome_tint_color")
    col_elems = len(attr_col.data) if attr_col is not None else (len(mesh.loops) if hasattr(mesh, "loops") else num_polys * 4)
    if snap_polys != num_polys or snap_col_elems != col_elems:
        return None
    return td, tc


def resolve_container_id(obj: Any) -> Optional[str]:
    """Resolves the immutable mtk:container_id UUID across object hierarchy with legacy fallbacks."""
    if obj is None:
        return None
    curr = obj
    depth = 0
    while curr is not None and depth < 64:
        if hasattr(curr, "get"):
            c_id = curr.get("mtk:container_id") or curr.get("mozi_save_uuid") or curr.get("mozi_scene_uuid")
            if c_id:
                return str(c_id)
        parent = getattr(curr, "parent", None)
        if parent is None and hasattr(curr, "id_data") and curr.id_data != curr:
            parent = getattr(curr.id_data, "parent", None)
        curr = parent
        depth += 1
    return None


def register_object_colormaps(key: str, colormaps: Any) -> None:
    """Register native VanillaColormaps in memory for a given container UUID."""
    if key and colormaps is not None:
        _COLORMAPS_CACHE[str(key)] = colormaps


def get_object_colormaps(key: str) -> Any:
    """Retrieve native VanillaColormaps from memory cache."""
    if not key:
        return None
    return _COLORMAPS_CACHE.get(str(key))


def register_object_storage(key: str, storage: Any) -> None:
    """Register native VoxelStorage in memory for a given container UUID."""
    if key and storage is not None:
        _STORAGE_CACHE[str(key)] = storage


def get_object_storage(key: str) -> Any:
    """Retrieve native VoxelStorage from memory cache."""
    if not key:
        return None
    return _STORAGE_CACHE.get(str(key))


def mark_world_biome(obj: Any, storage: Any = None, aliases: Any = ()) -> bool:
    """
    Tag an MTK voxel mesh as using true 3D World Biome tinting and cache its storage.

    Every voxel-derived surface mesh (save import, Live Sync, rebuild) bakes real 3D
    biome colors into its tint attributes. Tagging ``mtk:biome_preset`` here keeps the
    biome panel from falling back to the uniform ``PLAINS`` default, while registering
    the storage lets a later WORLD re-selection recompute colors without a live session.
    """
    if obj is None:
        return False

    try:
        obj["mtk:biome_preset"] = "WORLD"
    except (TypeError, AttributeError, KeyError):
        try:
            setattr(obj, "mtk:biome_preset", "WORLD")
        except Exception:
            pass

    if storage is not None:
        c_id = resolve_container_id(obj)
        if c_id:
            register_object_storage(c_id, storage)
        for key in aliases:
            if isinstance(key, str) and key:
                register_object_storage(key, storage)
        if not c_id and not aliases:
            for key in (getattr(obj, "name", ""), getattr(getattr(obj, "data", None), "name", "")):
                if isinstance(key, str) and key:
                    register_object_storage(key, storage)

    return True


def _rehydrate_storage_from_point_cloud(obj: Any, target_mesh: Any = None) -> Any:
    """
    Rebuild a VoxelStorage from the companion point cloud persisted in the .blend.

    This is the offline fallback for save imports and disconnected Live Sync
    containers: the point cloud keeps per-voxel block states and 3D biomes, so a
    later WORLD selection can restore the exact tint instead of refusing or
    repainting with the uniform Plains fallback.
    """
    if obj is None:
        return None

    from ....bridge.point_cloud import extract_voxel_point_cloud
    from ....utils.point_cloud import get_associated_voxel_cloud
    from ....bridge.point_cloud.constants import ATTR_BIOME, ATTR_BIOME_IDX, PROP_BIOME_PALETTE

    try:
        cloud = get_associated_voxel_cloud(obj)
        if cloud is None:
            return None

        cloud_mesh = getattr(cloud, "data", None)
        attrs = getattr(cloud_mesh, "attributes", None)
        if attrs is None:
            return None

        has_biomes = ATTR_BIOME in attrs
        if not has_biomes and ATTR_BIOME_IDX in attrs:
            has_biomes = (
                (hasattr(cloud_mesh, "__contains__") and PROP_BIOME_PALETTE in cloud_mesh)
                or (hasattr(cloud_mesh, "get") and cloud_mesh.get(PROP_BIOME_PALETTE) is not None)
                or (hasattr(cloud, "__contains__") and PROP_BIOME_PALETTE in cloud)
                or (hasattr(cloud, "get") and cloud.get(PROP_BIOME_PALETTE) is not None)
            )
        if not has_biomes:
            return None

        cloud_data = extract_voxel_point_cloud(cloud)
        if cloud_data is None:
            return None
        storage = cloud_data.to_storage()
    except Exception:
        return None

    if storage is None:
        return None

    for target in (obj, cloud):
        name = getattr(target, "name", "")
        if isinstance(name, str) and name:
            register_object_storage(name, storage)
    if target_mesh is not None:
        name = getattr(target_mesh, "name", "")
        if isinstance(name, str) and name:
            register_object_storage(name, storage)
    if hasattr(obj, "get"):
        for prop in ("mtk:container_id", "mozi_save_uuid"):
            key = obj.get(prop)
            if isinstance(key, str) and key:
                register_object_storage(key, storage)

    return storage
