"""
MoziToolKit Material Bridge - Biome & Colormap Submodule.
Encapsulates native routines for Biome metadata, sampling, and tint generation.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

import numpy as np

from ..engine import get_libmtk, require_libmtk

logger = logging.getLogger(__name__)

mtk_inst = get_libmtk()
TINT_TYPE_NONE: int = getattr(mtk_inst, "TINT_TYPE_NONE", 0) if mtk_inst else 0
TINT_TYPE_GRASS: int = getattr(mtk_inst, "TINT_TYPE_GRASS", 1) if mtk_inst else 1
TINT_TYPE_FOLIAGE: int = getattr(mtk_inst, "TINT_TYPE_FOLIAGE", 2) if mtk_inst else 2
TINT_TYPE_WATER: int = getattr(mtk_inst, "TINT_TYPE_WATER", 3) if mtk_inst else 3
TINT_TYPE_HARDCODED: int = getattr(mtk_inst, "TINT_TYPE_HARDCODED", 4) if mtk_inst else 4
TINT_TYPE_DRY_FOLIAGE: int = getattr(mtk_inst, "TINT_TYPE_DRY_FOLIAGE", 5) if mtk_inst else 5


def load_biome_resolver_from_file(file_path: Union[str, Path]) -> Any:
    """Load a precompiled BiomeResolver instance from a biome_mapping.json file."""
    mtk = require_libmtk("load_biome_resolver_from_file")
    return mtk.BiomeResolver.from_file(str(file_path))


def get_biome_meta(biome_name: str) -> Dict[str, Any]:
    """Retrieve canonical metadata, temperature, humidity, and colors from Rust core."""
    mtk = require_libmtk("get_biome_meta")
    return mtk.get_biome_meta(biome_name)


def sample_colormap_colors(
    temperature: float, humidity: float, colormaps: Optional[Any] = None
) -> Dict[str, List[float]]:
    """Sample resident vanilla colormap linear RGBA colors from temperature and humidity in Rust."""
    mtk = require_libmtk("sample_colormap_colors")
    if colormaps is not None:
        return mtk.sample_colormap_colors(float(temperature), float(humidity), colormaps)
    return mtk.sample_colormap_colors(float(temperature), float(humidity))


def get_all_biomes() -> List[Dict[str, Any]]:
    """Retrieve all standard vanilla Minecraft biomes with canonical metadata."""
    mtk = require_libmtk("get_all_biomes")
    return mtk.get_all_biomes()


def compute_biome_tint_attributes(
    face_texture_keys: List[str],
    biome_preset: Union[str, List[Tuple[str, float]]] = "PLAINS",
    resolver: Optional[Any] = None,
    custom_temp: Optional[float] = None,
    custom_humidity: Optional[float] = None,
    custom_grass: Optional[List[float]] = None,
    custom_foliage: Optional[List[float]] = None,
    custom_dry_foliage: Optional[List[float]] = None,
    custom_water: Optional[List[float]] = None,
    has_custom_grass: bool = False,
    has_custom_foliage: bool = False,
    has_custom_dry_foliage: bool = False,
    face_biomes: Optional[List[str]] = None,
    colormaps: Optional[Any] = None,
) -> Dict[str, Any]:
    """
    Compute packed tint weights and resolved Linear RGBA colors using the Rust core.
    Returns a dictionary with keys: 'packed_tint_data', 'tint_colors'.
    """
    mtk = require_libmtk("compute_biome_tint_attributes")

    kwargs: Dict[str, Any] = {}
    if colormaps is not None:
        kwargs["colormaps"] = colormaps

    if face_biomes is not None:
        return mtk.compute_biome_tint_attributes(
            face_texture_keys,
            "PLAINS",
            resolver=resolver,
            face_biomes=face_biomes,
            **kwargs,
        )

    if isinstance(biome_preset, list):
        return mtk.compute_biome_tint_attributes(
            face_texture_keys,
            "PLAINS",
            multi_biomes=biome_preset,
            resolver=resolver,
            **kwargs,
        )

    return mtk.compute_biome_tint_attributes(
        face_texture_keys,
        str(biome_preset),
        multi_biomes=None,
        resolver=resolver,
        custom_temp=custom_temp,
        custom_humidity=custom_humidity,
        custom_grass=custom_grass,
        custom_foliage=custom_foliage,
        custom_dry_foliage=custom_dry_foliage,
        custom_water=custom_water,
        has_custom_grass=has_custom_grass,
        has_custom_foliage=has_custom_foliage,
        has_custom_dry_foliage=has_custom_dry_foliage,
        **kwargs,
    )


def compute_biome_tint_attributes_paletted(
    palette: Sequence[str],
    face_indices: Sequence[int],
    biome_preset: Union[str, List[Tuple[str, float]]] = "PLAINS",
    resolver: Optional[Any] = None,
    custom_temp: Optional[float] = None,
    custom_humidity: Optional[float] = None,
    custom_grass: Optional[List[float]] = None,
    custom_foliage: Optional[List[float]] = None,
    custom_dry_foliage: Optional[List[float]] = None,
    custom_water: Optional[List[float]] = None,
    has_custom_grass: bool = False,
    has_custom_foliage: bool = False,
    has_custom_dry_foliage: bool = False,
    colormaps: Optional[Any] = None,
) -> Tuple[np.ndarray, np.ndarray]:
    """Palette-native biome tint computation returning zero-copy flat arrays."""
    mtk = require_libmtk("compute_biome_tint_attributes_paletted")
    multi = list(biome_preset) if isinstance(biome_preset, list) else None
    name = "PLAINS" if multi is not None else str(biome_preset)
    kwargs: Dict[str, Any] = {}
    if colormaps is not None:
        kwargs["colormaps"] = colormaps
    packed, colors = mtk.compute_biome_tint_attributes_paletted(
        list(palette),
        face_indices,
        name,
        multi_biomes=multi,
        resolver=resolver,
        custom_temp=custom_temp,
        custom_humidity=custom_humidity,
        custom_grass=custom_grass,
        custom_foliage=custom_foliage,
        custom_dry_foliage=custom_dry_foliage,
        custom_water=custom_water,
        has_custom_grass=has_custom_grass,
        has_custom_foliage=has_custom_foliage,
        has_custom_dry_foliage=has_custom_dry_foliage,
        **kwargs,
    )
    return (
        np.frombuffer(packed, dtype=np.float32).reshape(-1, 4),
        np.frombuffer(colors, dtype=np.float32).reshape(-1, 4),
    )


def reapply_uniform_biome_tint(
    base_tint_types: Any,
    packed_tint_data: Any,
    existing_tint_colors: Any,
    grass_color: Sequence[float],
    foliage_color: Sequence[float],
    dry_foliage_color: Sequence[float],
    water_color: Sequence[float],
    has_custom_grass: bool = False,
    has_custom_foliage: bool = False,
    has_custom_dry_foliage: bool = False,
) -> Any:
    """
    Re-apply a uniform biome palette using Rust parallel math.

    Inputs are flat buffers:
      - base_tint_types: uint8 per face
      - packed_tint_data: float32 x 4 per face
      - existing_tint_colors: float32 x 4 per face (may be empty)

    Returns zero-copy memoryviews for (packed_tint_data, tint_colors).
    """
    mtk = require_libmtk("reapply_uniform_biome_tint")
    return mtk.reapply_uniform_biome_tint(
        base_tint_types,
        packed_tint_data,
        existing_tint_colors,
        tuple(grass_color),
        tuple(foliage_color),
        tuple(dry_foliage_color),
        tuple(water_color),
        bool(has_custom_grass),
        bool(has_custom_foliage),
        bool(has_custom_dry_foliage),
    )


def compute_mesh_biome_attributes_from_storage(
    face_texture_keys: List[str],
    block_xs: List[int],
    block_ys: List[int],
    block_zs: List[int],
    storage: Any,
    resolver: Optional[Any] = None,
    smooth: bool = True,
    colormaps: Optional[Any] = None,
) -> Dict[str, Any]:
    """
    Compute coordinate-aware 3D biome tint attributes from VoxelStorage container.
    """
    mtk = require_libmtk("compute_mesh_biome_attributes_from_storage")
    kwargs: Dict[str, Any] = {}
    if colormaps is not None:
        kwargs["colormaps"] = colormaps
    return mtk.compute_mesh_biome_attributes_from_storage(
        face_texture_keys,
        block_xs,
        block_ys,
        block_zs,
        storage,
        resolver=resolver,
        smooth=smooth,
        **kwargs,
    )
