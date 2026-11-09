"""
MoziToolKit Material & Biome Bridge Package.
Encapsulates high-performance Rust core (libmtk_py) routines for material name resolution,
UV remapping, atlas metadata, and Biome color/tint generation.
"""

from __future__ import annotations

from typing import Any

from ..engine import get_libmtk, require_libmtk
from .biome import (
    TINT_TYPE_DRY_FOLIAGE,
    TINT_TYPE_FOLIAGE,
    TINT_TYPE_GRASS,
    TINT_TYPE_HARDCODED,
    TINT_TYPE_NONE,
    TINT_TYPE_WATER,
    compute_biome_tint_attributes,
    compute_biome_tint_attributes_paletted,
    compute_mesh_biome_attributes_from_storage,
    get_all_biomes,
    get_biome_meta,
    load_biome_resolver_from_file,
    reapply_uniform_biome_tint,
    sample_colormap_colors,
)
from .resolve import (
    build_material_palette,
    builtin_biome_suffixes,
    clean_material_identifier,
    clean_material_name,
    get_chunk_texture_specs,
    load_baked_atlas_from_json,
    normalize_banner_pattern,
    remap_mesh_multi_uvs_paletted,
    reroute_mesh_animation,
    resolve_chunk_texture_sources,
    resolve_mesh_material_routes,
)


def is_material_bridge_available() -> bool:
    """Check if native libmtk_py material and biome functions are loaded."""
    return get_libmtk() is not None


def require_material_bridge() -> None:
    """Raise RuntimeError if native libmtk_py material bridge is unavailable."""
    if not is_material_bridge_available():
        require_libmtk("material & biome bridge")


def __getattr__(name: str) -> Any:
    mtk = get_libmtk()
    if name in ("BiomeResolver", "MaterialResolver", "GridAtlasSpec", "BakedAtlas"):
        require_material_bridge()
        val = getattr(mtk, name, None)
        if val is None:
            raise AttributeError(f"libmtk_py does not expose '{name}'")
        return val
    if name.startswith("TINT_TYPE_"):
        require_material_bridge()
        return getattr(mtk, name)
    raise AttributeError(f"module '{__name__}' has no attribute '{name}'")


__all__ = [
    "is_material_bridge_available",
    "require_material_bridge",
    "BiomeResolver",
    "load_biome_resolver_from_file",
    "get_biome_meta",
    "sample_colormap_colors",
    "get_all_biomes",
    "compute_biome_tint_attributes",
    "compute_biome_tint_attributes_paletted",
    "compute_mesh_biome_attributes_from_storage",
    "reapply_uniform_biome_tint",
    "MaterialResolver",
    "clean_material_name",
    "clean_material_identifier",
    "normalize_banner_pattern",
    "builtin_biome_suffixes",
    "GridAtlasSpec",
    "BakedAtlas",
    "load_baked_atlas_from_json",
    "build_material_palette",
    "get_chunk_texture_specs",
    "resolve_chunk_texture_sources",
    "remap_mesh_multi_uvs_paletted",
    "resolve_mesh_material_routes",
    "reroute_mesh_animation",
    "TINT_TYPE_NONE",
    "TINT_TYPE_GRASS",
    "TINT_TYPE_FOLIAGE",
    "TINT_TYPE_WATER",
    "TINT_TYPE_HARDCODED",
    "TINT_TYPE_DRY_FOLIAGE",
]
