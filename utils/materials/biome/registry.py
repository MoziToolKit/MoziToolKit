"""
In-memory registry and storage caching for biome updating.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger("MoziToolKit.Materials.Biome.Registry")

# In-memory cache for original face tint types (to prevent RNA allocation overhead and preserve base categories)
_MESH_BASE_TINT_TYPES: dict[str, Any] = {}

# In-memory storage registry to avoid serializing unpicklable native VoxelStorage into bpy properties
_STORAGE_CACHE: dict[str, Any] = {}
_COLORMAPS_CACHE: dict[str, Any] = {}


def clear_biome_updater_cache() -> None:
    """Clear in-memory caches for mesh tint types, voxel storage registry, and colormaps."""
    _MESH_BASE_TINT_TYPES.clear()
    _STORAGE_CACHE.clear()
    _COLORMAPS_CACHE.clear()


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
