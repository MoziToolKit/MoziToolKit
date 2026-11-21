"""
Base material tint type resolution.
Decouples intrinsic material identity from biome-level hardcoded overrides.
"""

from __future__ import annotations

import logging
from typing import Any, Optional
import numpy as np

from ....bridge.mesh.attribute_schema import ATTR_BIOME_TINT_DATA, ATTR_SOURCE_TEXTURE_IDX
from ..constants import PROP_CREATED_BY
from .biome import (
    TINT_TYPE_NONE,
    TINT_TYPE_HARDCODED,
)
from .registry import _MESH_BASE_TINT_TYPES

logger = logging.getLogger("MoziToolKit.Materials.Biome.BaseTint")


def _extract_raw_tint_data_2d(mesh: Any, num_polys: int, raw_tint_data_2d: Optional[Any]) -> Optional[np.ndarray]:
    if raw_tint_data_2d is not None:
        arr = np.asarray(raw_tint_data_2d, dtype=np.float32)
        if arr.size == num_polys * 4:
            return arr.reshape(num_polys, 4)
    if hasattr(mesh, "attributes"):
        tint_attr = mesh.attributes.get(ATTR_BIOME_TINT_DATA)
        if tint_attr is not None and len(tint_attr.data) == num_polys and hasattr(tint_attr.data, "foreach_get"):
            arr = np.empty(num_polys * 4, dtype=np.float32)
            tint_attr.data.foreach_get("color", arr)
            return arr.reshape(num_polys, 4)
    return None


def _mask_untinted_faces(base_tt: np.ndarray, raw_2d: Optional[np.ndarray], num_polys: int) -> None:
    if raw_2d is not None and len(raw_2d) == num_polys:
        # A face is untinted if its baked tint_weight (channel 2) is <= 0.0
        # or its tint_type (channel 3) is explicitly TINT_TYPE_NONE (0.0).
        raw_zero_mask = (raw_2d[:, 2] <= 0.0) | (raw_2d[:, 3] == float(TINT_TYPE_NONE))
        base_tt[raw_zero_mask] = TINT_TYPE_NONE


def resolve_mesh_base_tint_types(
    mesh: Any,
    obj: Optional[Any] = None,
    raw_tint_data_2d: Optional[Any] = None,
    pack_stack: Optional[Any] = None,
) -> Any:
    """
    Resolve intrinsic material base tint types (0=None, 1=Grass, 2=Foliage, 3=Water, 4=Hardcoded, 5=Dry Foliage)
    for each face of the mesh, decoupling intrinsic material identity from biome-level hardcoded overrides.

    Prevents faces from being permanently trapped in TINT_TYPE_HARDCODED (4.0) after imports from biomes
    with custom colors (such as dark_forest or badlands).
    """
    if mesh is None:
        return np.empty(0, dtype=np.int32)

    num_polys = len(mesh.polygons) if hasattr(mesh, "polygons") else 0
    if num_polys == 0:
        return np.empty(0, dtype=np.int32)

    mesh_name = getattr(mesh, "name", "")
    cached = _MESH_BASE_TINT_TYPES.get(mesh_name)
    if cached is not None and len(cached) == num_polys:
        return cached

    effective_raw_2d = _extract_raw_tint_data_2d(mesh, num_polys, raw_tint_data_2d)

    from .biome import get_or_load_biome_resolver
    resolver = get_or_load_biome_resolver(pack_stack=pack_stack)

    # Strategy A: Sub-millisecond INT Palette Indexing ('mtk_source_textures' + 'mtk_source_texture_idx')
    palette = None
    if hasattr(mesh, "get") and mesh.get("mtk_source_textures") is not None:
        palette = list(mesh.get("mtk_source_textures"))
    elif hasattr(mesh, "__contains__") and "mtk_source_textures" in mesh:
        palette = list(mesh["mtk_source_textures"])
    elif hasattr(mesh, "keys") and "mtk_source_textures" in mesh:
        palette = list(mesh["mtk_source_textures"])
    elif obj is not None and hasattr(obj, "get") and "mtk_source_textures" in obj:
        palette = list(obj["mtk_source_textures"])

    attr_idx = mesh.attributes.get(ATTR_SOURCE_TEXTURE_IDX) if hasattr(mesh, "attributes") else None
    if palette is not None and attr_idx is not None and len(attr_idx.data) == num_polys:
        if hasattr(attr_idx.data, "foreach_get"):
            pal_tt = np.array([
                resolver.get_tint_info(tex).get("tint_type", 0) if hasattr(resolver, "get_tint_info") else 0
                for tex in palette
            ], dtype=np.int32)
            idx_arr = np.empty(num_polys, dtype=np.int32)
            attr_idx.data.foreach_get("value", idx_arr)
            valid_mask = (idx_arr >= 0) & (idx_arr < len(pal_tt))
            base_tt = np.zeros(num_polys, dtype=np.int32)
            base_tt[valid_mask] = pal_tt[idx_arr[valid_mask]]

            _mask_untinted_faces(base_tt, effective_raw_2d, num_polys)

            if np.any(base_tt > 0) or effective_raw_2d is not None:
                if mesh_name:
                    _MESH_BASE_TINT_TYPES[mesh_name] = base_tt
                return base_tt

    # Strategy C: Material slots on object
    if obj is not None and len(getattr(obj, "material_slots", [])) > 0:
        slot_tt = []
        for slot in obj.material_slots:
            mat = slot.material
            if mat:
                key = str(mat.get("mtk_source_texture_key") or mat.get("mtk:material_id") or mat.name)
                tt = resolver.get_tint_info(key).get("tint_type", 0) if hasattr(resolver, "get_tint_info") else 0
            else:
                tt = 0
            slot_tt.append(tt)

        slot_tt_arr = np.array(slot_tt, dtype=np.int32)
        poly_mat_indices = np.empty(num_polys, dtype=np.int32)
        mesh.polygons.foreach_get("material_index", poly_mat_indices)
        valid_mask = (poly_mat_indices >= 0) & (poly_mat_indices < len(slot_tt_arr))
        base_tt = np.zeros(num_polys, dtype=np.int32)
        base_tt[valid_mask] = slot_tt_arr[poly_mat_indices[valid_mask]]
        _mask_untinted_faces(base_tt, effective_raw_2d, num_polys)

        if np.any(base_tt > 0):
            if mesh_name:
                _MESH_BASE_TINT_TYPES[mesh_name] = base_tt
            return base_tt

    # Strategy D: Fallback to raw_tint_data_2d
    if effective_raw_2d is not None and len(effective_raw_2d) == num_polys:
        base_tt = np.round(effective_raw_2d[:, 3]).astype(np.int32)
        _mask_untinted_faces(base_tt, effective_raw_2d, num_polys)
        if mesh_name:
            _MESH_BASE_TINT_TYPES[mesh_name] = base_tt
        return base_tt

    base_tt = np.zeros(num_polys, dtype=np.int32)
    if mesh_name:
        _MESH_BASE_TINT_TYPES[mesh_name] = base_tt
    return base_tt
