"""
3D World Biome tinting computation and application from VoxelStorage.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, List, Optional
import numpy as np

from ..constants import (
    ATTR_BIOME_TINT_DATA,
    ATTR_SOURCE_TEXTURE_KEY,
    ATTR_SOURCE_TEXTURE_IDX,
)
from .registry import (
    resolve_container_id,
    get_object_storage,
    get_object_colormaps,
    _rehydrate_storage_from_point_cloud,
)

logger = logging.getLogger("MoziToolKit.Materials.Biome.WorldTint")


def resolve_and_apply_world_biome_tint(
    mesh_or_obj: Any,
    mesh: Optional[Any] = None,
    face_source_keys: Optional[List[str]] = None,
    effective_stack: Optional[Any] = None,
    prefs: Optional[Any] = None,
    base_cache: Optional[Path] = None,
) -> bool:
    """
    Resolve 3D World Biome tinting attributes from an associated VoxelStorage container
    and apply packed tint data, colors, and colormap UVs to the mesh.
    Returns True if world biome attributes were successfully computed or verified.
    """
    if mesh_or_obj is None and mesh is None:
        return False

    is_obj = hasattr(mesh_or_obj, "type") and getattr(mesh_or_obj, "type", "") == "MESH"
    obj = mesh_or_obj if is_obj else None
    target_mesh = mesh if mesh is not None else (getattr(mesh_or_obj, "data", mesh_or_obj) if is_obj else mesh_or_obj)

    if not hasattr(target_mesh, "polygons"):
        return False

    from .biome import (
        apply_biome_tint_attributes,
        read_face_string_attribute,
        get_or_load_biome_resolver,
    )

    storage = getattr(mesh_or_obj, "_storage", None) or getattr(target_mesh, "_storage", None)
    if storage is None:
        c_id = resolve_container_id(mesh_or_obj) or resolve_container_id(target_mesh)
        if c_id:
            storage = get_object_storage(c_id)
    if storage is None and hasattr(mesh_or_obj, "name"):
        storage = get_object_storage(mesh_or_obj.name)
    if storage is None and hasattr(target_mesh, "name"):
        storage = get_object_storage(target_mesh.name)
    if storage is None and hasattr(mesh_or_obj, "get"):
        storage = mesh_or_obj.get("mtk:storage", None)
    if storage is None and hasattr(target_mesh, "get"):
        storage = target_mesh.get("mtk:storage", None)
    if storage is None:
        try:
            from ....bridge.sync import get_sync_bridge_session
            session = get_sync_bridge_session()
            if session is not None and getattr(session, "is_active", False):
                storage = session.get_storage()
        except Exception:
            storage = None
    if storage is None:
        storage = _rehydrate_storage_from_point_cloud(obj, target_mesh)

    source_keys = list(face_source_keys) if face_source_keys else []
    if not any(source_keys) and hasattr(target_mesh, "attributes"):
        if ATTR_SOURCE_TEXTURE_KEY in target_mesh.attributes or ATTR_SOURCE_TEXTURE_IDX in target_mesh.attributes:
            source_keys = read_face_string_attribute(target_mesh, ATTR_SOURCE_TEXTURE_KEY)

    if not any(source_keys) and obj and len(getattr(obj, "material_slots", [])) > 0:
        derived = []
        for poly in target_mesh.polygons:
            m_idx = poly.material_index
            mat = obj.material_slots[m_idx].material if m_idx < len(obj.material_slots) else None
            key = (mat.get("mtk_source_texture_key") or mat.get("mtk:material_id") or "") if mat else ""
            derived.append(str(key))
        if derived and all(derived):
            source_keys = derived

    has_coords = (
        hasattr(target_mesh, "attributes")
        and "mtk_block_x" in target_mesh.attributes
        and "mtk_block_y" in target_mesh.attributes
        and "mtk_block_z" in target_mesh.attributes
    )

    if storage is not None and has_coords and any(source_keys):
        from ....bridge.material import compute_mesh_biome_attributes_from_storage

        biome_resolver = get_or_load_biome_resolver(cache_dir=base_cache, prefs=prefs, pack_stack=effective_stack)
        num_polys = len(target_mesh.polygons)
        attr_x = target_mesh.attributes.get("mtk_block_x")
        attr_y = target_mesh.attributes.get("mtk_block_y")
        attr_z = target_mesh.attributes.get("mtk_block_z")

        if hasattr(attr_x.data, "foreach_get"):
            xs = np.empty(num_polys, dtype=np.int32)
            ys = np.empty(num_polys, dtype=np.int32)
            zs = np.empty(num_polys, dtype=np.int32)
            attr_x.data.foreach_get("value", xs)
            attr_y.data.foreach_get("value", ys)
            attr_z.data.foreach_get("value", zs)
            coords_x, coords_y, coords_z = xs, ys, zs
        else:
            coords_x = [int(p.value) for p in attr_x.data]
            coords_y = [int(p.value) for p in attr_y.data]
            coords_z = [int(p.value) for p in attr_z.data]

        colormaps = getattr(mesh_or_obj, "_colormaps", None) or getattr(target_mesh, "_colormaps", None)
        if colormaps is None:
            c_uuid = resolve_container_id(mesh_or_obj) or resolve_container_id(target_mesh)
            if c_uuid:
                colormaps = get_object_colormaps(c_uuid)
        if colormaps is None and hasattr(mesh_or_obj, "name"):
            colormaps = get_object_colormaps(mesh_or_obj.name)

        res = compute_mesh_biome_attributes_from_storage(
            source_keys,
            coords_x,
            coords_y,
            coords_z,
            storage,
            resolver=biome_resolver,
            smooth=True,
            colormaps=colormaps,
        )
        apply_biome_tint_attributes(target_mesh, res["packed_tint_data"], res["tint_colors"])
        if hasattr(mesh_or_obj, "__setitem__"):
            try:
                mesh_or_obj["mtk:biome_preset"] = "WORLD"
            except Exception:
                pass
        if hasattr(target_mesh, "update"):
            target_mesh.update()
        return True

    if hasattr(target_mesh, "attributes") and ATTR_BIOME_TINT_DATA in target_mesh.attributes:
        current_preset = None
        if hasattr(mesh_or_obj, "get"):
            current_preset = mesh_or_obj.get("mtk:biome_preset")
        if isinstance(current_preset, str) and current_preset and current_preset.upper() != "WORLD":
            return False
        if hasattr(mesh_or_obj, "__setitem__"):
            try:
                mesh_or_obj["mtk:biome_preset"] = "WORLD"
            except Exception:
                pass
        if hasattr(target_mesh, "update"):
            target_mesh.update()
        return True

    return False
