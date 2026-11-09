"""
MoziToolKit Material Replacement & Provenance Pipeline.
Executes pure Rust (libmtk) material resolution, UV remapping,
LabPBR 1.3 shader construction, and mesh attribute synchronization.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional
import numpy as np

logger = logging.getLogger("MoziToolKit.Material.Pipeline")

from ...bridge.material import (
    is_material_bridge_available,
    reroute_mesh_animation,
    resolve_mesh_material_routes,
)
from ...utils.assets import get_cache_dir
from ...bridge.mesh import (
    _get_mesh,
    resolve_source_texture_keys,
    resolve_source_texture_keys_paletted,
)
from .biome import (
    get_or_load_biome_resolver,
    compute_biome_tint_attributes_paletted,
    apply_biome_tint_attributes,
)
from .cleaner import (
    mesh_has_animation,
    prune_optional_mesh_attributes,
)
from .uv_layers import (
    compute_atlas_layout_hash,
    get_stored_layout_hash,
    read_uv_layer,
    resolve_uv_layers,
    set_stored_layout_hash,
)
from .pipeline_stages import (
    animated_textures_enabled,
    ensure_stack_precompiled,
    _load_atlas_data_and_baked_atlas,
    load_material_cache_context,
    extract_face_material_palette_context,
    remap_face_materials_and_uvs_paletted,
    assign_atlas_chunk_materials,
    inject_mesh_provenance_attributes,
    resolve_and_apply_biome_tint,
    _needs_animation_reconcile,
    _is_external_object,
)

__all__ = [
    "replace_materials",
    "restore_materials_from_provenance",
    "ensure_stack_precompiled",
    "load_material_cache_context",
    "extract_face_material_palette_context",
    "remap_face_materials_and_uvs_paletted",
    "assign_atlas_chunk_materials",
    "inject_mesh_provenance_attributes",
    "resolve_and_apply_biome_tint",
    "animated_textures_enabled",
    "get_cache_dir",
    "compute_atlas_layout_hash",
    "get_or_load_biome_resolver",
]


def replace_materials(
    mesh_or_obj: Any,
    mode: str = "ATLAS",
    origin: str = "AUTO",
    biome: str = "PLAINS",
    prefs: Optional[Any] = None,
    force_remap: bool = False,
    reconcile_animation: Optional[bool] = None,
) -> Dict[str, Any]:
    """
    Executes end-to-end material replacement on a Blender mesh object using libmtk Rust backend.
    External meshes keep their original local UV map and receive a separate ``MTK_Atlas`` UV map.
    """
    if not is_material_bridge_available():
        raise RuntimeError("libmtk material bridge is not installed or available.")

    mesh = _get_mesh(mesh_or_obj)
    if mesh is None or len(mesh.polygons) == 0:
        return {"success": False, "message": "Mesh has no geometry or polygons."}

    mode_upper = "ATLAS"

    # Step 1: Load cache context & precompiled assets
    base_cache, atlas_dir, manifest_fingerprint = load_material_cache_context(prefs)

    atlas_data, baked_atlas = _load_atlas_data_and_baked_atlas(atlas_dir, prefs)
    anim_enabled = animated_textures_enabled(prefs)
    num_polys = len(mesh.polygons)
    current_layout_hash = compute_atlas_layout_hash(atlas_data)
    stored_layout_hash = get_stored_layout_hash(mesh)

    # Step 2: Resolve preserved local UV map plus atlas UV map
    local_uv_layer, atlas_uv_layer, atlas_created = resolve_uv_layers(
        mesh,
        create_atlas=True,
        activate=True,
    )
    if atlas_uv_layer is None:
        return {"success": False, "message": "Mesh has no UV map to store Atlas coordinates."}
    if local_uv_layer is None:
        return {
            "success": False,
            "message": (
                "Mesh has no preserved local UV map. Re-import external imports to enable "
                "lossless re-mapping; MTK voxel meshes must use Rebuild Voxel Mesh."
            ),
        }

    provenance_palette, provenance_indices = resolve_source_texture_keys_paletted(mesh)
    has_provenance = len(provenance_indices) == num_polys and len(provenance_palette) > 0
    same_layout = bool(stored_layout_hash) and stored_layout_hash == current_layout_hash

    # Fast path: keep existing atlas UVs and only rebind materials
    if (
        not force_remap
        and has_provenance
        and atlas_uv_layer is not None
        and not atlas_created
        and same_layout
    ):
        needs_reconcile = (
            _needs_animation_reconcile(mesh, prefs)
            if reconcile_animation is None
            else bool(reconcile_animation)
        )
        try:
            restored = restore_materials_from_provenance(
                mesh_or_obj,
                mode=mode,
                biome=biome,
                prefs=prefs,
                reconcile_animation=needs_reconcile,
                enable_animated_textures=anim_enabled,
                atlas_uv_layer=atlas_uv_layer,
                layout_hash=current_layout_hash,
                material_namespace="EXT",
            )
        except Exception as exc:
            logger.warning("Atlas UV reuse raised %s; re-mapping from local UVs.", exc)
            restored = {"success": False, "message": str(exc)}
        if restored.get("success"):
            set_stored_layout_hash(mesh, current_layout_hash)
            restored["face_count"] = num_polys
            restored["materials_count"] = len(mesh.materials)
            restored["unmapped_faces"] = 0
            restored["mode"] = mode_upper
            restored["reused_atlas_uvs"] = True
            restored["atlas_uv_layer"] = getattr(atlas_uv_layer, "name", "")
            restored["local_uv_layer"] = getattr(local_uv_layer, "name", "")
            return restored

        logger.warning(
            "Atlas UV reuse failed (%s); re-mapping from local UVs.",
            restored.get("message"),
        )

    # Step 3: Extract face materials & local UVs as palette + indices
    (
        face_material_palette,
        face_material_indices,
        face_loop_ranges,
        loop_uvs,
        _local_uv_layer,
        existing_key_palette,
        existing_key_indices,
    ) = extract_face_material_palette_context(mesh, uv_layer=local_uv_layer)

    # Step 4: Palette-native Rust multi-UV remapping + source-key provenance
    remap = remap_face_materials_and_uvs_paletted(
        baked_atlas=baked_atlas,
        face_material_palette=face_material_palette,
        face_material_indices=face_material_indices,
        loop_uvs=loop_uvs,
        face_loop_ranges=face_loop_ranges,
        origin=origin,
        existing_key_palette=existing_key_palette,
        existing_key_indices=existing_key_indices,
        enable_animated_textures=anim_enabled,
    )

    # Step 5: Construct and assign materials into the atlas UV map
    layout_changed = bool(stored_layout_hash) and stored_layout_hash != current_layout_hash
    assign_atlas_chunk_materials(
        mesh=mesh,
        uv_layer=atlas_uv_layer,
        atlas_data=atlas_data,
        atlas_dir=atlas_dir,
        face_chunk_ids=remap["face_chunk_ids"],
        atlas_uvs=remap["atlas_uvs"],
        manifest_fingerprint=manifest_fingerprint,
        force_rebuild_materials=layout_changed,
        material_namespace="EXT",
    )
    set_stored_layout_hash(mesh, current_layout_hash)

    # Step 6: Inject mesh provenance attributes (palette + zero-copy buffers)
    inject_mesh_provenance_attributes(
        mesh=mesh,
        face_source_key_palette=remap["source_key_palette"],
        face_source_key_indices=remap["face_source_key_indices"],
        face_chunk_ids=remap["face_chunk_ids"],
        face_uv_transforms=remap["face_uv_transforms"],
        face_uv_rotations=remap.get("face_uv_rotations"),
        face_anim_timing=remap.get("face_anim_timing"),
        face_anim_frame_size=remap.get("face_anim_frame_size"),
    )

    # Step 7: Compute & apply biome tint attributes through palette-native path
    resolve_and_apply_biome_tint(
        mesh_or_obj,
        mesh,
        None,
        biome,
        base_cache,
        prefs,
        source_key_palette=remap["source_key_palette"],
        source_key_indices=np.frombuffer(
            remap["face_source_key_indices"], dtype=np.uint32
        ),
    )

    if hasattr(mesh, "__setitem__"):
        try:
            mesh["mtk_enable_animated_textures"] = bool(anim_enabled)
        except Exception:
            pass

    mesh.update()
    prune_optional_mesh_attributes(
        mesh,
        keep_animation=mesh_has_animation(mesh),
        keep_block_coords=not _is_external_object(mesh_or_obj),
    )
    animated_faces = sum(1 for flag in remap.get("face_is_animated", []) if flag)
    return {
        "success": True,
        "mode": mode_upper,
        "biome": biome,
        "face_count": len(mesh.polygons),
        "materials_count": len(mesh.materials),
        "unmapped_faces": remap["unmapped_faces"],
        "animated_faces": animated_faces,
        "enable_animated_textures": bool(anim_enabled),
        "reused_atlas_uvs": False,
        "local_uv_layer": getattr(local_uv_layer, "name", ""),
        "atlas_uv_layer": getattr(atlas_uv_layer, "name", ""),
    }


def restore_materials_from_provenance(
    mesh_or_obj: Any,
    mode: str = "ATLAS",
    biome: Optional[str] = None,
    prefs: Optional[Any] = None,
    reconcile_animation: bool = False,
    enable_animated_textures: Optional[bool] = None,
    atlas_uv_layer: Optional[Any] = None,
    layout_hash: Optional[str] = None,
    material_namespace: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Reconstructs Blender material slots, nodes, and assignments purely from mesh face provenance attributes.
    This is the same-layout fast path used by replace_materials.
    """
    if not is_material_bridge_available():
        raise RuntimeError("libmtk material bridge is not installed or available.")

    mesh = _get_mesh(mesh_or_obj)
    if mesh is None or len(mesh.polygons) == 0:
        return {"success": False, "message": "Mesh has no geometry or polygons."}

    num_polys = len(mesh.polygons)
    face_source_keys = resolve_source_texture_keys(mesh)
    face_source_palette, face_source_indices = resolve_source_texture_keys_paletted(mesh)
    if not face_source_keys or len(face_source_keys) != num_polys:
        return {
            "success": False,
            "message": "Mesh lacks required mtk_source_texture_key / mtk_source_texture_idx attribute for provenance.",
        }

    effective_biome = biome
    if not effective_biome and hasattr(mesh_or_obj, "get"):
        effective_biome = mesh_or_obj.get("mtk:biome_preset", "PLAINS")
    if not effective_biome:
        effective_biome = "PLAINS"

    anim_enabled = (
        bool(enable_animated_textures)
        if enable_animated_textures is not None
        else animated_textures_enabled(prefs)
    )

    # Step 1: Load cache context and atlas routing data
    base_cache, atlas_dir, manifest_fingerprint = load_material_cache_context(prefs)
    atlas_data, baked_atlas = _load_atlas_data_and_baked_atlas(atlas_dir, prefs)

    current_layout_hash = layout_hash or compute_atlas_layout_hash(atlas_data)
    stored_layout_hash = get_stored_layout_hash(mesh)
    if stored_layout_hash and current_layout_hash and stored_layout_hash != current_layout_hash:
        return {
            "success": False,
            "message": (
                "Atlas layout changed since the last replacement. Re-run Replace Material "
                "to re-map atlas UVs from the preserved local UV map."
            ),
        }

    if not hasattr(mesh, "uv_layers") or len(mesh.uv_layers) == 0:
        return {"success": False, "message": "Mesh has no UV map to restore Atlas coordinates into."}

    _local_uv_layer, resolved_atlas_uv, _ = resolve_uv_layers(
        mesh, create_atlas=False, activate=True
    )
    if atlas_uv_layer is None:
        atlas_uv_layer = resolved_atlas_uv
    if atlas_uv_layer is None:
        return {
            "success": False,
            "message": "Mesh has no MTK_Atlas UV map. Run Replace Material to create it.",
        }

    if material_namespace is None and _is_external_object(mesh_or_obj):
        material_namespace = "EXT"

    chunk_attr = mesh.attributes.get("mtk_atlas_chunk_id") if hasattr(mesh, "attributes") else None
    if chunk_attr is not None and len(chunk_attr.data) == num_polys:
        if hasattr(chunk_attr.data, "foreach_get"):
            chunk_ids_np = np.empty(num_polys, dtype=np.int32)
            chunk_attr.data.foreach_get("value", chunk_ids_np)
            face_chunk_ids = [int(x) for x in chunk_ids_np]
        else:
            face_chunk_ids = [int(elem.value) for elem in chunk_attr.data]
    else:
        if baked_atlas is None:
            return {
                "success": False,
                "message": "Atlas data is required to reconstruct missing mtk_atlas_chunk_id.",
            }
        routes = resolve_mesh_material_routes(
            face_source_keys,
            baked_atlas,
            enable_animated_textures=anim_enabled,
        )
        face_chunk_ids = [int(cid) for cid in routes["chunk_ids"]]
        unmapped = int(routes.get("unmapped_faces", 0))
        if unmapped:
            logger.warning(
                "Restore resolved %d/%d chunk routes from provenance; %d faces are unmapped",
                num_polys - unmapped,
                num_polys,
                unmapped,
            )

    atlas_uvs: Optional[List[float]] = None
    face_anim_timing: Optional[List[List[float]]] = None
    face_anim_frame_size: Optional[List[List[float]]] = None
    animation_reconciled = False

    if reconcile_animation and atlas_uv_layer is not None:
        num_loops = len(mesh.loops)
        loop_uvs = read_uv_layer(atlas_uv_layer, num_loops)

        starts = np.empty(num_polys, dtype=np.int32)
        totals = np.empty(num_polys, dtype=np.int32)
        mesh.polygons.foreach_get("loop_start", starts)
        mesh.polygons.foreach_get("loop_total", totals)
        face_loop_ranges = list(zip(starts.tolist(), totals.tolist()))

        reroute = reroute_mesh_animation(
            loop_uvs=loop_uvs,
            face_loop_ranges=face_loop_ranges,
            face_texture_keys=face_source_keys,
            face_old_chunk_ids=face_chunk_ids,
            atlas=baked_atlas,
            enable_animated_textures=anim_enabled,
        )
        face_chunk_ids = [int(x) for x in reroute["face_chunk_ids"]]
        atlas_uvs = reroute["atlas_uvs"]
        face_anim_timing = reroute.get("face_anim_timing")
        face_anim_frame_size = reroute.get("face_anim_frame_size")
        animation_reconciled = True

    # Step 2: Reconstruct material slots
    assign_atlas_chunk_materials(
        mesh=mesh,
        uv_layer=atlas_uv_layer,
        atlas_data=atlas_data,
        atlas_dir=atlas_dir,
        face_chunk_ids=face_chunk_ids,
        atlas_uvs=atlas_uvs,
        manifest_fingerprint=manifest_fingerprint,
        material_namespace=material_namespace,
    )

    # Step 3: Sync material slot / animation attributes
    inject_mesh_provenance_attributes(
        mesh=mesh,
        face_source_keys=face_source_keys,
        face_source_key_palette=face_source_palette or None,
        face_source_key_indices=face_source_indices if len(face_source_indices) == num_polys else None,
        face_chunk_ids=face_chunk_ids,
        face_anim_timing=face_anim_timing,
        face_anim_frame_size=face_anim_frame_size,
    )

    if animation_reconciled and hasattr(mesh, "__setitem__"):
        try:
            mesh["mtk_enable_animated_textures"] = bool(anim_enabled)
        except Exception:
            pass

    # Step 4: Re-apply biome tint attributes
    resolve_and_apply_biome_tint(
        mesh_or_obj,
        mesh,
        face_source_keys,
        effective_biome,
        base_cache,
        prefs,
        source_key_palette=face_source_palette or None,
        source_key_indices=face_source_indices if len(face_source_indices) == num_polys else None,
    )

    set_stored_layout_hash(mesh, current_layout_hash)
    mesh.update()
    prune_optional_mesh_attributes(
        mesh,
        keep_animation=mesh_has_animation(mesh),
        keep_block_coords=not _is_external_object(mesh_or_obj),
    )
    animated_faces = sum(1 for flag in (face_anim_timing or []) if flag and flag[0] > 1.0)
    return {
        "success": True,
        "restored_faces": num_polys,
        "biome": effective_biome,
        "materials_count": len(mesh.materials),
        "animated_faces": animated_faces,
        "animation_reconciled": animation_reconciled,
        "enable_animated_textures": bool(anim_enabled),
        "atlas_uv_layer": getattr(atlas_uv_layer, "name", ""),
    }
