"""
Material pipeline stage processors: cache loading, palette extraction,
UV remapping, material assignment, provenance attribute injection, and biome tinting.
"""

from __future__ import annotations

import array
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple
import numpy as np
import bpy

from ...bridge.material import (
    get_biome_meta,
    load_baked_atlas_from_json,
    remap_mesh_multi_uvs_paletted,
    build_material_palette,
    resolve_chunk_texture_sources,
)
from ...utils.assets import (
    get_cache_dir,
    check_asset_cache_status,
)
from ...bridge.assets import (
    get_cache_fingerprint,
    precompile_stack,
    invalidate_asset_cache_memory,
    load_atlas_mapping_from_cache,
    load_baked_atlas_from_cache,
    ensure_atlas_textures_extracted,
)
from ...bridge.mesh import (
    inject_face_attribute_string,
    inject_face_attribute_int,
    inject_face_attribute_float,
    inject_face_attribute_float4,
    inject_face_attribute_vector,
    resolve_source_texture_keys_paletted,
    inject_face_source_texture_palette,
)
from .builder.atlas_builder import build_atlas_chunk_material
from .matching.presets.registry import build_matching_context
from .cleaner import clean_obsolete_mesh_attributes
from .biome import (
    get_or_load_biome_resolver,
    compute_biome_tint_attributes,
    compute_biome_tint_attributes_paletted,
    apply_biome_tint_attributes,
)
from .constants import (
    ATTR_SOURCE_TEXTURE_KEY,
    ATTR_ATLAS_CHUNK_ID,
    ATTR_UV_TRANSFORM,
    ATTR_UV_ROTATION,
    ATTR_ANIM_TIMING,
    ATTR_ANIM_FRAME_SIZE,
    chunk_supports_tint,
)
from .uv_layers import (
    find_local_uv_layer,
    read_uv_layer,
)

logger = logging.getLogger("MoziToolKit.Material.Pipeline.Stages")


def _get_pipeline_mod():
    return sys.modules.get("MoziToolKit.utils.materials.pipeline")


def animated_textures_enabled(prefs: Optional[Any] = None) -> bool:
    """Read the animated-texture preference with a safe True fallback."""
    raw = getattr(prefs, "enable_animated_textures", True) if prefs is not None else True
    return raw if isinstance(raw, bool) else True


def ensure_stack_precompiled(prefs: Optional[Any] = None) -> Path:
    """Ensure asset caches exist in persistent storage and match configured pack stack."""
    pipeline_mod = _get_pipeline_mod()
    cache_dir_fn = getattr(pipeline_mod, "get_cache_dir", get_cache_dir) if pipeline_mod else get_cache_dir
    base_cache = cache_dir_fn(prefs)
    status = check_asset_cache_status(prefs)

    if status in ("MISSING", "STALE"):
        has_custom_packs = prefs is not None and hasattr(prefs, "resource_packs")
        is_test_sandbox = os.environ.get("MTK_TEST_SANDBOX") == "1"
        if not (not has_custom_packs and is_test_sandbox):
            logger.info("Asset cache is %s; running automated precompilation...", status)
            try:
                precompile_stack(prefs)
                invalidate_asset_cache_memory()
            except Exception as e:
                logger.warning("Automated precompilation in ensure_stack_precompiled failed: %s", e)
    return base_cache


def _load_atlas_data_and_baked_atlas(atlas_dir: Path, prefs: Optional[Any] = None) -> Tuple[Dict[str, Any], Any]:
    """Load atlas mapping JSON plus the native BakedAtlas used for Rust routing."""
    atlas_data = load_atlas_mapping_from_cache(prefs)
    baked_atlas = load_baked_atlas_from_cache(prefs)
    if atlas_data is None:
        atlas_mapping_path = atlas_dir / "atlas_mapping.json"
        if not atlas_mapping_path.exists():
            raise FileNotFoundError(f"Atlas mapping missing at {atlas_mapping_path} and no active .mtkcache found")
        atlas_json_str = atlas_mapping_path.read_text(encoding="utf-8")
        atlas_data = json.loads(atlas_json_str)
        baked_atlas = load_baked_atlas_from_json(atlas_json_str)
    elif baked_atlas is None:
        baked_atlas = load_baked_atlas_from_json(json.dumps(atlas_data))
    return atlas_data, baked_atlas


def load_material_cache_context(prefs: Optional[Any] = None) -> Tuple[Path, Path, Optional[str]]:
    """Ensure assets are precompiled and discover cache directory and manifest."""
    base_cache = ensure_stack_precompiled(prefs)
    atlas_dir = ensure_atlas_textures_extracted(prefs)
    manifest_fingerprint = get_cache_fingerprint(prefs)
    return base_cache, atlas_dir, manifest_fingerprint


def extract_face_material_palette_context(
    mesh: Any,
    uv_layer: Optional[Any] = None,
) -> Tuple[
    List[str],
    Any,
    List[Tuple[int, int]],
    array.array,
    Any,
    List[str],
    Any,
]:
    """
    Extract face material names as a compact palette + per-face indices.
    Returns:
        (palette, indices, face_loop_ranges, loop_uvs, uv_layer,
         existing_key_palette, existing_key_indices)
    """
    num_polys = len(mesh.polygons)
    num_loops = len(mesh.loops)

    if uv_layer is None:
        uv_layer = find_local_uv_layer(mesh)
    if uv_layer is None:
        uv_layer = mesh.uv_layers.new(name="UVMap")

    loop_uvs = read_uv_layer(uv_layer, num_loops)

    existing_key_palette, existing_key_indices = resolve_source_texture_keys_paletted(mesh)
    if len(existing_key_indices) != num_polys:
        existing_key_palette, existing_key_indices = [], np.empty(0, dtype=np.uint32)

    poly_mats = np.empty(num_polys, dtype=np.int32)
    mesh.polygons.foreach_get("material_index", poly_mats)
    loop_starts = np.empty(num_polys, dtype=np.int32)
    loop_totals = np.empty(num_polys, dtype=np.int32)
    mesh.polygons.foreach_get("loop_start", loop_starts)
    mesh.polygons.foreach_get("loop_total", loop_totals)

    slot_names: List[str] = []
    slot_is_mtk: List[bool] = []
    slot_source_ids: List[str] = []
    for mat_slot in mesh.materials:
        if not mat_slot:
            slot_names.append("default")
            slot_is_mtk.append(True)
            slot_source_ids.append("")
            continue

        mat_name = str(getattr(mat_slot, "name", "default") or "default")
        mat_lower = mat_name.strip().lower()
        slot_id = (
            mat_slot.get("mtk_source_texture_key")
            or mat_slot.get("mtk:material_id")
            or mat_slot.get("mtk:source_texture")
            or ""
        )
        is_mtk = mat_lower.startswith(("mtk:", "mtk_", "mc_atlas", "default")) or bool(
            mat_slot.get("mtk:material_id")
            or mat_slot.get("mtk_source_texture_key")
            or mat_slot.get("mtk_material_mode")
        )
        slot_names.append(mat_name)
        slot_is_mtk.append(is_mtk)
        slot_source_ids.append(str(slot_id) if slot_id else "")

    palette, indices = build_material_palette(
        slot_names,
        slot_is_mtk,
        slot_source_ids,
        poly_mats,
        existing_key_palette,
        existing_key_indices,
    )

    face_loop_ranges: List[Tuple[int, int]] = list(zip(loop_starts.tolist(), loop_totals.tolist()))

    return (
        palette,
        indices,
        face_loop_ranges,
        loop_uvs,
        uv_layer,
        existing_key_palette,
        existing_key_indices,
    )


def remap_face_materials_and_uvs_paletted(
    baked_atlas: Any,
    face_material_palette: List[str],
    face_material_indices: Any,
    loop_uvs: array.array,
    face_loop_ranges: List[Tuple[int, int]],
    origin: str,
    existing_key_palette: Optional[List[str]],
    existing_key_indices: Any,
    enable_animated_textures: bool = False,
) -> Dict[str, Any]:
    """Palette-native Step 3: Rust remap + compact source-key provenance."""
    alias_map, grid_spec = build_matching_context(face_material_palette, origin=origin)
    return remap_mesh_multi_uvs_paletted(
        loop_uvs=loop_uvs,
        face_material_palette=face_material_palette,
        face_material_indices=face_material_indices,
        face_loop_ranges=face_loop_ranges,
        atlas=baked_atlas,
        alias_map=alias_map,
        grid_spec=grid_spec,
        existing_key_palette=existing_key_palette if existing_key_palette else None,
        existing_key_indices=(
            existing_key_indices
            if existing_key_indices is not None and len(existing_key_indices)
            else None
        ),
        enable_animated_textures=enable_animated_textures,
    )


def assign_atlas_chunk_materials(
    mesh: Any,
    uv_layer: Optional[Any],
    atlas_data: Dict[str, Any],
    atlas_dir: Path,
    face_chunk_ids: List[int],
    atlas_uvs: Optional[List[float]],
    manifest_fingerprint: Optional[str],
    force_rebuild_materials: bool = False,
    material_namespace: Optional[str] = None,
) -> array.array:
    """Construct Atlas Chunk materials, assign to mesh material slots, and optionally set Atlas UVs."""
    num_polys = len(mesh.polygons)
    if isinstance(face_chunk_ids, (list, tuple)):
        chunk_arr = np.asarray(face_chunk_ids, dtype=np.int32)
    else:
        chunk_arr = np.frombuffer(face_chunk_ids, dtype=np.int32)
    if chunk_arr.size != num_polys:
        chunk_arr = np.resize(chunk_arr, num_polys)

    unique_chunks = np.unique(chunk_arr)
    chunk_to_slot: Dict[int, int] = {}
    mesh.materials.clear()

    chunk_meta_list = atlas_data.get("chunks", [])
    chunk_meta_map: Dict[int, Dict[str, Any]] = {
        c.get("chunk_id", idx): c for idx, c in enumerate(chunk_meta_list)
    }

    for slot_idx, chunk_id_raw in enumerate(unique_chunks):
        chunk_id = int(chunk_id_raw)
        cm = chunk_meta_map.get(chunk_id, {})
        cat = cm.get("category", "blocks")
        c_idx = cm.get("category_chunk_index", chunk_id + 1)
        is_anim = cm.get("is_animated", False)

        stem = cm.get("file_stem", f"{cat}_anim_chunk_{c_idx:03}" if is_anim else f"{cat}_chunk_{c_idx:03}")
        chunk_width = float(cm.get("width", 1024))
        chunk_height = float(cm.get("height", 512))
        uv_map_name = uv_layer.name if uv_layer else "UVMap"
        material_name = None
        if material_namespace == "EXT":
            if is_anim:
                material_name = f"MTK:Atlas:Ext:anim:{cat}:{c_idx:03}"
            else:
                material_name = f"MTK:Atlas:Ext:{cat}:{c_idx:03}"

        enable_biome_tint = chunk_supports_tint(cm, cat)
        texture_sources = resolve_chunk_texture_sources(cm, atlas_dir=atlas_dir)
        mat = build_atlas_chunk_material(
            chunk_id=chunk_id,
            category=cat,
            category_chunk_index=c_idx,
            is_animated=is_anim,
            stack_fingerprint=manifest_fingerprint,
            enable_biome_tint=enable_biome_tint,
            atlas_width=chunk_width,
            atlas_height=chunk_height,
            tile_width=16.0,
            tile_height=16.0,
            uv_map_name=uv_map_name,
            material_name=material_name,
            force_rebuild=force_rebuild_materials,
            chunk_stem=stem,
            texture_sources=texture_sources,
        )
        mesh.materials.append(mat)
        chunk_to_slot[chunk_id] = slot_idx

    poly_mat_indices = np.searchsorted(unique_chunks, chunk_arr).astype(np.int32)

    if atlas_uvs is not None and uv_layer is not None:
        if isinstance(atlas_uvs, (list, tuple)):
            atlas_uv_np = np.ascontiguousarray(atlas_uvs, dtype=np.float32)
        else:
            atlas_uv_np = np.frombuffer(atlas_uvs, dtype=np.float32)
        uv_attr = mesh.attributes.get(uv_layer.name) if hasattr(mesh, "attributes") else None
        if uv_attr is not None and len(uv_attr.data) * 2 == atlas_uv_np.size:
            uv_attr.data.foreach_set("vector", atlas_uv_np)
        else:
            uv_layer.data.foreach_set("uv", atlas_uv_np)

    mat_attr = mesh.attributes.get("material_index") if hasattr(mesh, "attributes") else None
    if mat_attr is not None and len(mat_attr.data) == poly_mat_indices.size:
        mat_attr.data.foreach_set("value", poly_mat_indices)
    else:
        mesh.polygons.foreach_set("material_index", poly_mat_indices)
    return poly_mat_indices


def inject_mesh_provenance_attributes(
    mesh: Any,
    face_source_keys: Optional[List[str]] = None,
    face_chunk_ids: Any = None,
    face_uv_transforms: Optional[Any] = None,
    face_uv_rotations: Optional[Any] = None,
    face_anim_timing: Optional[Any] = None,
    face_anim_frame_size: Optional[Any] = None,
    face_source_key_palette: Optional[List[str]] = None,
    face_source_key_indices: Optional[Any] = None,
) -> None:
    """Inject provenance and shader-driver attributes."""
    num_polys = len(mesh.polygons)

    if face_source_key_palette is not None and face_source_key_indices is not None:
        inject_face_source_texture_palette(
            mesh, face_source_key_palette, face_source_key_indices
        )
    elif face_source_keys is not None:
        inject_face_attribute_string(mesh, ATTR_SOURCE_TEXTURE_KEY, face_source_keys)

    if face_chunk_ids is not None:
        inject_face_attribute_int(mesh, ATTR_ATLAS_CHUNK_ID, face_chunk_ids)

    if face_uv_transforms is not None:
        if isinstance(face_uv_transforms, (list, tuple)):
            flat_transforms = np.ascontiguousarray(face_uv_transforms, dtype=np.float32).ravel()
        else:
            flat_transforms = np.frombuffer(face_uv_transforms, dtype=np.float32)
        inject_face_attribute_float4(mesh, ATTR_UV_TRANSFORM, flat_transforms)

    if face_uv_rotations is not None:
        inject_face_attribute_float(mesh, ATTR_UV_ROTATION, face_uv_rotations)

    if face_anim_timing is not None:
        if isinstance(face_anim_timing, (list, tuple)):
            timing = np.ascontiguousarray(face_anim_timing, dtype=np.float32).ravel()
        else:
            timing = np.frombuffer(face_anim_timing, dtype=np.float32)
        if timing.size >= num_polys * 3:
            timing = timing[: num_polys * 3]
            if face_anim_frame_size is None:
                sizes = np.tile(np.array([16.0, 16.0, 0.0], dtype=np.float32), num_polys)
            elif isinstance(face_anim_frame_size, (list, tuple)):
                sizes = np.ascontiguousarray(face_anim_frame_size, dtype=np.float32).ravel()
            else:
                sizes = np.frombuffer(face_anim_frame_size, dtype=np.float32)
            if sizes.size >= num_polys * 3:
                sizes = sizes[: num_polys * 3]
                inject_face_attribute_vector(mesh, ATTR_ANIM_TIMING, timing)
                inject_face_attribute_vector(mesh, ATTR_ANIM_FRAME_SIZE, sizes)

    clean_obsolete_mesh_attributes(mesh)


def resolve_and_apply_biome_tint(
    mesh_or_obj: Any,
    mesh: Any,
    face_source_keys: Optional[List[str]],
    biome: str,
    base_cache: Path,
    prefs: Optional[Any] = None,
    source_key_palette: Optional[Sequence[str]] = None,
    source_key_indices: Optional[Any] = None,
) -> None:
    """Step 6: Compute and apply biome tint attributes and colormap UVs based on current preset."""
    biome_resolver = get_or_load_biome_resolver(cache_dir=base_cache, prefs=prefs)
    is_world = biome.upper() == "WORLD"

    if is_world:
        if face_source_keys is None and source_key_palette is not None and source_key_indices is not None:
            face_source_keys = np.asarray(source_key_palette, dtype=object)[source_key_indices].tolist()
        from .biome.updater import resolve_and_apply_world_biome_tint
        if resolve_and_apply_world_biome_tint(
            mesh_or_obj=mesh_or_obj,
            mesh=mesh,
            face_source_keys=face_source_keys or [],
            prefs=prefs,
            base_cache=base_cache,
        ):
            return

    is_custom = biome.upper() == "CUSTOM"
    if is_custom:
        def _get_val(prop: str, default: Any) -> Any:
            return getattr(mesh_or_obj, prop, mesh_or_obj.get(prop, default) if hasattr(mesh_or_obj, "get") else default)

        plains_meta = get_biome_meta("PLAINS")
        custom_temp = float(_get_val("mtk_biome_temp", plains_meta["temperature"]))
        custom_humidity = float(_get_val("mtk_biome_humidity", plains_meta["humidity"]))
        has_custom_grass = bool(_get_val("mtk_biome_use_custom_grass", False))
        has_custom_foliage = bool(_get_val("mtk_biome_use_custom_foliage", False))
        has_custom_dry_foliage = bool(_get_val("mtk_biome_use_custom_dry_foliage", False))
        custom_grass = list(_get_val("mtk_biome_grass_color", plains_meta["grass_linear"]))
        custom_foliage = list(_get_val("mtk_biome_foliage_color", plains_meta["foliage_linear"]))
        custom_dry_foliage = list(_get_val("mtk_biome_dry_foliage_color", plains_meta["dry_foliage_linear"]))
        custom_water = list(_get_val("mtk_biome_water_color", plains_meta["water_linear"]))
    else:
        custom_temp, custom_humidity, custom_grass, custom_foliage, custom_dry_foliage, custom_water = None, None, None, None, None, None
        has_custom_grass, has_custom_foliage, has_custom_dry_foliage = False, False, False

    if not is_world and source_key_palette is not None and source_key_indices is not None:
        packed_tint_data, tint_colors = compute_biome_tint_attributes_paletted(
            source_key_palette,
            source_key_indices,
            biome_preset=biome,
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
    else:
        if face_source_keys is None:
            return
        packed_tint_data, tint_colors = compute_biome_tint_attributes(
            face_source_keys,
            biome_preset=biome,
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

    if hasattr(mesh_or_obj, "__setitem__"):
        try:
            mesh_or_obj["mtk:biome_preset"] = biome
        except Exception:
            pass


def _needs_animation_reconcile(mesh: Any, prefs: Optional[Any] = None) -> bool:
    """Decide whether static/animated routing must be re-resolved."""
    anim_enabled = animated_textures_enabled(prefs)
    stored = mesh.get("mtk_enable_animated_textures", None) if hasattr(mesh, "get") else None
    if stored is not None:
        return bool(stored) != anim_enabled
    return not (hasattr(mesh, "attributes") and "mtk_anim_timing" in mesh.attributes)


def _is_external_object(obj: Any) -> bool:
    """Best-effort ownership check used to pick the external material namespace."""
    if obj is None:
        return False
    from ..mesh.ownership import ORIGIN_EXTERNAL, classify_origin
    try:
        return classify_origin(obj) == ORIGIN_EXTERNAL
    except Exception:
        return False
