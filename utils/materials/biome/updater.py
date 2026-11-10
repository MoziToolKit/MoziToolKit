"""
Instant Biome Updater for Atlas and Standalone Material Modes.
Updates mesh face attributes or material shader nodes in < 1ms without rerunning the texture replacement pipeline.
"""

from __future__ import annotations

import logging
from typing import Any, Optional
import bpy
import numpy as np

from ....bridge.material import get_biome_meta
from ..cleaner import clean_obsolete_mesh_attributes
from ....bridge.mesh.attribute_schema import (
    ATTR_ATLAS_CHUNK_ID,
    ATTR_BIOME_TINT_DATA,
    ATTR_BIOME_TINT_COLOR,
    ATTR_SOURCE_TEXTURE_KEY,
    ATTR_SOURCE_TEXTURE_IDX,
)
from ..constants import PROP_ATLAS_MAPPING
from .biome import (
    get_biome_colors,
    TINT_TYPE_HARDCODED,
)

from .registry import (
    _MESH_BASE_TINT_TYPES,
    _STORAGE_CACHE,
    _COLORMAPS_CACHE,
    clear_biome_updater_cache,
    resolve_container_id,
    register_object_colormaps,
    get_object_colormaps,
    register_object_storage,
    get_object_storage,
    mark_world_biome,
    _rehydrate_storage_from_point_cloud,
)
from .base_tint import resolve_mesh_base_tint_types
from .world_tint import resolve_and_apply_world_biome_tint

logger = logging.getLogger("MoziToolKit.Materials.Biome.Updater")


def is_mtk_object(obj: Optional[bpy.types.Object]) -> bool:
    """Check if the given object or container belongs to MoziToolKit."""
    if not obj:
        return False
    if getattr(obj, "type", "") == "EMPTY":
        from ...world.hierarchy import is_mtk_container
        return is_mtk_container(obj)
    if obj.type != "MESH" or obj.data is None:
        return False
    if obj.get("mtk:provenance_schema_version") is not None or obj.get("mtk:biome_preset") is not None:
        return True
    if obj.parent and getattr(obj.parent, "type", "") == "EMPTY":
        from ...world.hierarchy import is_mtk_container
        if is_mtk_container(obj.parent):
            return True
    mesh = obj.data
    attrs = mesh.attributes
    if any(
        name in attrs
        for name in (
            ATTR_SOURCE_TEXTURE_KEY,
            ATTR_SOURCE_TEXTURE_IDX,
            ATTR_BIOME_TINT_DATA,
            ATTR_BIOME_TINT_COLOR,
            ATTR_ATLAS_CHUNK_ID,
        )
    ):
        return True
    for slot in obj.material_slots:
        mat = slot.material
        if mat:
            name_lower = mat.name.lower()
            if (
                name_lower.startswith(("mtk:", "mtk_", "mc_atlas"))
                or mat.get("mozi_created_by")
                or mat.get("mtk_material_mode")
                or PROP_ATLAS_MAPPING in mat
            ):
                return True
    return False


def detect_object_material_mode(obj: bpy.types.Object) -> str:
    """Determine whether an object is currently configured in Atlas mode."""
    if not obj:
        return "UNKNOWN"
    if getattr(obj, "type", "") == "EMPTY":
        from ...world.hierarchy import resolve_hierarchy
        ctx = resolve_hierarchy(obj)
        if ctx.primary_mesh:
            return detect_object_material_mode(ctx.primary_mesh)
        return "ATLAS"
    if obj.type != "MESH" or not obj.data:
        return "UNKNOWN"
    return "ATLAS"


def update_object_biome(
    obj: bpy.types.Object,
    biome_name: str,
    pack_stack: Any = None,
) -> bool:
    """
    Instantly update an object's biome colors and colormap UVs without rerunning the texture replacement pipeline.
    Supports both Atlas mode (mesh face attributes) and Standalone mode (material shader node trees).
    """
    if not is_mtk_object(obj):
        return False

    is_custom = biome_name.upper() == "CUSTOM"

    if getattr(obj, "type", "") == "EMPTY":
        from ...world.hierarchy import resolve_hierarchy
        ctx = resolve_hierarchy(obj)
        target_meshes = ctx.child_meshes or ([ctx.primary_mesh] if ctx.primary_mesh else [])
        updated = False
        for m in target_meshes:
            if is_custom:
                for prop_name in (
                    "mtk_biome_temp", "mtk_biome_humidity",
                    "mtk_biome_use_custom_grass", "mtk_biome_grass_color",
                    "mtk_biome_use_custom_foliage", "mtk_biome_foliage_color",
                    "mtk_biome_use_custom_dry_foliage", "mtk_biome_dry_foliage_color",
                    "mtk_biome_water_color",
                ):
                    if hasattr(obj, prop_name) and hasattr(m, prop_name):
                        setattr(m, prop_name, getattr(obj, prop_name))
            if update_object_biome(m, biome_name, pack_stack=pack_stack):
                updated = True
        try:
            obj["mtk:biome_preset"] = biome_name
        except Exception:
            pass
        return updated or bool(target_meshes)

    mesh = obj.data
    mode = detect_object_material_mode(obj)
    if is_custom:
        def _get_prop(k: str, default: Any) -> Any:
            if hasattr(obj, "get") and obj.get(k) is not None:
                return obj.get(k)
            return getattr(obj, k, default)

        plains_meta = get_biome_meta("PLAINS")
        custom_temp = float(_get_prop("mtk_biome_temp", plains_meta["temperature"]))
        custom_humidity = float(_get_prop("mtk_biome_humidity", plains_meta["humidity"]))
        has_custom_grass = bool(_get_prop("mtk_biome_use_custom_grass", False))
        has_custom_foliage = bool(_get_prop("mtk_biome_use_custom_foliage", False))
        has_custom_dry_foliage = bool(_get_prop("mtk_biome_use_custom_dry_foliage", False))
        custom_grass = list(_get_prop("mtk_biome_grass_color", plains_meta["grass_linear"]))
        custom_foliage = list(_get_prop("mtk_biome_foliage_color", plains_meta["foliage_linear"]))
        custom_dry_foliage = list(_get_prop("mtk_biome_dry_foliage_color", plains_meta["dry_foliage_linear"]))
        custom_water = list(_get_prop("mtk_biome_water_color", plains_meta["water_linear"]))
    else:
        custom_temp = None
        custom_humidity = None
        has_custom_grass = False
        has_custom_foliage = False
        has_custom_dry_foliage = False
        custom_grass = None
        custom_foliage = None
        custom_dry_foliage = None
        custom_water = None

    biome_colors = get_biome_colors(
        biome_name,
        pack_stack=pack_stack,
        custom_temp=custom_temp,
        custom_humidity=custom_humidity,
        custom_grass=custom_grass,
        custom_foliage=custom_foliage,
        custom_dry_foliage=custom_dry_foliage,
        custom_water=custom_water,
        has_custom_grass=has_custom_grass,
        has_custom_foliage=has_custom_foliage,
        has_custom_dry_foliage=has_custom_dry_foliage,
    )
    if not is_custom:
        has_custom_grass = bool(biome_colors.get("has_custom_grass", False))
        has_custom_foliage = bool(biome_colors.get("has_custom_foliage", False))
        has_custom_dry_foliage = bool(biome_colors.get("has_custom_dry_foliage", False))

    effective_stack = pack_stack
    if effective_stack is None:
        try:
            from ....bridge.assets import get_configured_pack_stack
            effective_stack = get_configured_pack_stack()
        except Exception:
            effective_stack = None

    # Update mesh face attributes
    num_polys = len(mesh.polygons) if hasattr(mesh, "polygons") else 0
    if num_polys > 0:
        from .biome import (
            compute_biome_tint_attributes_paletted,
            apply_biome_tint_attributes,
            read_face_string_attribute,
            get_or_load_biome_resolver,
        )
        from ....bridge.mesh import resolve_source_texture_keys_paletted

        if biome_name.upper() == "WORLD":
            source_keys = []
            if ATTR_SOURCE_TEXTURE_KEY in mesh.attributes or ATTR_SOURCE_TEXTURE_IDX in mesh.attributes:
                source_keys = read_face_string_attribute(mesh, ATTR_SOURCE_TEXTURE_KEY)
            if resolve_and_apply_world_biome_tint(
                mesh_or_obj=obj,
                mesh=mesh,
                face_source_keys=source_keys,
                effective_stack=effective_stack,
            ):
                return True

            logger.warning(
                "Cannot restore WORLD biome for '%s': no voxel storage or source-texture "
                "provenance. Rebuild the voxel mesh from its point cloud/storage.",
                getattr(obj, "name", "<unnamed>"),
            )
            return False

        # FAST IN-PLACE VECTORIZED UPDATE PATH (< 1.5ms)
        tint_data_attr = mesh.attributes.get(ATTR_BIOME_TINT_DATA)
        has_tint_data = tint_data_attr is not None and len(tint_data_attr.data) == num_polys
        fast_path_done = False

        if has_tint_data and hasattr(tint_data_attr.data, "foreach_get"):
            try:
                raw_data = np.empty(num_polys * 4, dtype=np.float32)
                tint_data_attr.data.foreach_get("color", raw_data)

                base_tt = resolve_mesh_base_tint_types(
                    mesh, obj, raw_data.reshape(num_polys, 4), pack_stack=effective_stack
                )

                is_init = bool(mesh.get("mtk:biome_tint_initialized", False)) if hasattr(mesh, "get") else False
                if not np.any(base_tt > 0) and not is_init:
                    fast_path_done = False
                else:
                    plains_meta = get_biome_meta("PLAINS")
                    grass_col = biome_colors.get("grass_linear", plains_meta["grass_linear"])
                    foliage_col = biome_colors.get("foliage_linear", plains_meta["foliage_linear"])
                    water_col = biome_colors.get("water_linear", plains_meta["water_linear"])
                    dry_foliage_col = biome_colors.get("dry_foliage_linear", plains_meta["dry_foliage_linear"])

                    from ....bridge.material import reapply_uniform_biome_tint

                    if reapply_uniform_biome_tint is not None and len(base_tt) == num_polys:
                        attr_col = mesh.attributes.get(ATTR_BIOME_TINT_COLOR)
                        if attr_col is None:
                            try:
                                attr_col = mesh.attributes.new(name=ATTR_BIOME_TINT_COLOR, type="FLOAT_COLOR", domain="CORNER")
                            except Exception:
                                attr_col = None

                        is_corner_domain = getattr(attr_col, "domain", None) == "CORNER"
                        num_col_elems = len(attr_col.data) if attr_col is not None else 0

                        needs_existing_colors = bool(np.any(base_tt == TINT_TYPE_HARDCODED))
                        if (
                            needs_existing_colors
                            and attr_col is not None
                            and hasattr(attr_col.data, "foreach_get")
                        ):
                            if is_corner_domain and num_col_elems >= num_polys * 4:
                                raw_cols = np.empty(num_col_elems * 4, dtype=np.float32)
                                attr_col.data.foreach_get("color", raw_cols)
                                old_cols = raw_cols.reshape(-1, 4, 4)[:, 0, :].reshape(-1)
                            elif num_col_elems == num_polys:
                                old_cols = np.empty(num_polys * 4, dtype=np.float32)
                                attr_col.data.foreach_get("color", old_cols)
                            else:
                                old_cols = np.empty(0, dtype=np.float32)
                        else:
                            old_cols = np.empty(0, dtype=np.float32)

                        packed_mv, colors_mv = reapply_uniform_biome_tint(
                            np.ascontiguousarray(base_tt, dtype=np.uint8),
                            np.ascontiguousarray(raw_data, dtype=np.float32),
                            np.ascontiguousarray(old_cols, dtype=np.float32),
                            grass_col,
                            foliage_col,
                            dry_foliage_col,
                            water_col,
                            has_custom_grass=has_custom_grass,
                            has_custom_foliage=has_custom_foliage,
                            has_custom_dry_foliage=has_custom_dry_foliage,
                        )

                        tint_data_attr.data.foreach_set(
                            "color", np.frombuffer(packed_mv, dtype=np.float32)
                        )
                        if attr_col is not None and hasattr(attr_col.data, "foreach_set"):
                            face_colors = np.frombuffer(colors_mv, dtype=np.float32)
                            if is_corner_domain:
                                loop_colors = np.repeat(face_colors.reshape(num_polys, 4), 4, axis=0).reshape(-1)
                                attr_col.data.foreach_set("color", loop_colors)
                            elif len(attr_col.data) == num_polys:
                                attr_col.data.foreach_set("color", face_colors)

                        clean_obsolete_mesh_attributes(mesh)

                        if hasattr(mesh, "__setitem__"):
                            try:
                                mesh["mtk:biome_tint_initialized"] = True
                            except Exception:
                                pass
                        fast_path_done = True
            except Exception:
                fast_path_done = False

        if not fast_path_done:
            pal, pal_idx = resolve_source_texture_keys_paletted(mesh)
            if not pal or len(pal_idx) != num_polys:
                source_keys = []
                if ATTR_SOURCE_TEXTURE_KEY in mesh.attributes or ATTR_SOURCE_TEXTURE_IDX in mesh.attributes:
                    source_keys = read_face_string_attribute(mesh, ATTR_SOURCE_TEXTURE_KEY)
                if any(source_keys):
                    pal = list(dict.fromkeys(source_keys))
                    k2i = {k: i for i, k in enumerate(pal)}
                    pal_idx = np.fromiter((k2i[k] for k in source_keys), dtype=np.uint32, count=len(source_keys))

            if pal and len(pal_idx) == num_polys and any(pal):
                biome_resolver = get_or_load_biome_resolver(pack_stack=effective_stack)
                packed_tint_data, tint_colors = compute_biome_tint_attributes_paletted(
                    pal,
                    pal_idx,
                    biome_preset=biome_name,
                    resolver=biome_resolver,
                    custom_temp=custom_temp,
                    custom_humidity=custom_humidity,
                    custom_grass=custom_grass,
                    custom_foliage=custom_foliage,
                    custom_dry_foliage=custom_dry_foliage,
                    custom_water=custom_water,
                    has_custom_grass=has_custom_grass,
                    has_custom_foliage=has_custom_foliage,
                    has_custom_dry_foliage=has_custom_dry_foliage,
                )
                apply_biome_tint_attributes(mesh, packed_tint_data, tint_colors)
                if hasattr(mesh, "__setitem__"):
                    try:
                        mesh["mtk:biome_tint_initialized"] = True
                    except Exception:
                        pass
            elif len(obj.material_slots) > 0:
                derived_keys = []
                for poly in mesh.polygons:
                    m_idx = poly.material_index
                    mat = obj.material_slots[m_idx].material if m_idx < len(obj.material_slots) else None
                    key = (mat.get("mtk_source_texture_key") or mat.get("mtk:material_id") or "") if mat else ""
                    derived_keys.append(str(key))

                if derived_keys and all(derived_keys):
                    d_pal = list(dict.fromkeys(derived_keys))
                    d_k2i = {k: i for i, k in enumerate(d_pal)}
                    d_idx = np.fromiter((d_k2i[k] for k in derived_keys), dtype=np.uint32, count=len(derived_keys))
                    biome_resolver = get_or_load_biome_resolver(pack_stack=effective_stack)
                    packed_tint_data, tint_colors = compute_biome_tint_attributes_paletted(
                        d_pal,
                        d_idx,
                        biome_preset=biome_name,
                        resolver=biome_resolver,
                        custom_temp=custom_temp,
                        custom_humidity=custom_humidity,
                        custom_grass=custom_grass,
                        custom_foliage=custom_foliage,
                        custom_dry_foliage=custom_dry_foliage,
                        custom_water=custom_water,
                        has_custom_grass=has_custom_grass,
                        has_custom_foliage=has_custom_foliage,
                        has_custom_dry_foliage=has_custom_dry_foliage,
                    )
                    apply_biome_tint_attributes(mesh, packed_tint_data, tint_colors)
                    if hasattr(mesh, "__setitem__"):
                        try:
                            mesh["mtk:biome_tint_initialized"] = True
                        except Exception:
                            pass

    obj["mtk:biome_preset"] = biome_name
    if getattr(obj, "parent", None) and getattr(obj.parent, "type", "") == "EMPTY":
        try:
            obj.parent["mtk:biome_preset"] = biome_name
        except Exception:
            pass
    if hasattr(mesh, "update"):
        mesh.update()
    return True


__all__ = [
    "clear_biome_updater_cache",
    "resolve_container_id",
    "register_object_colormaps",
    "get_object_colormaps",
    "register_object_storage",
    "get_object_storage",
    "mark_world_biome",
    "resolve_mesh_base_tint_types",
    "resolve_and_apply_world_biome_tint",
    "is_mtk_object",
    "detect_object_material_mode",
    "update_object_biome",
]
