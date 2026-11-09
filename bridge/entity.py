"""
MoziToolKit Entity Bridge Module.

Pure Data-In, Data-Out bridge for Minecraft entity procedural mesh generation,
coordinate calculations, and variant resolution. Strictly decoupled from Blender UI
and DCC node tree wiring.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .engine import get_libmtk

logger = logging.getLogger("MoziToolKit.Bridge.Entity")


def build_painting_mesh(
    variant: str,
    facing: str = "north",
    offset: Optional[Sequence[float]] = None,
) -> Tuple[Any, List[str]]:
    """Builds raw MeshData and texture identifiers for a Minecraft painting.

    Args:
        variant: Painting variant identifier (e.g. "kebab", "minecraft:alban").
        facing: Horizontal facing direction ("north", "south", "east", "west").
        offset: Optional [x, y, z] translation offset.

    Returns:
        Tuple of (libmtk_py.MeshData, ordered list of texture strings).
    """
    mtk = get_libmtk()
    off = [float(x) for x in offset] if offset is not None else None
    return mtk.build_painting_mesh(variant, facing, off)


def get_painting_dimension(variant: str) -> Tuple[int, int]:
    """Retrieves the integer (width, height) in blocks for a painting variant."""
    mtk = get_libmtk()
    return mtk.get_painting_dimension(variant)


def get_painting_variant(variant: str) -> Optional[Dict[str, Any]]:
    """Retrieves metadata dictionary for a specific painting variant."""
    mtk = get_libmtk()
    return mtk.get_painting_variant(variant)


def get_all_painting_variants() -> List[Dict[str, Any]]:
    """Retrieves all 51 official Minecraft 1.21+ Painting variant metadata dictionaries."""
    mtk = get_libmtk()
    return mtk.get_all_painting_variants()


def calculate_painting_position(
    support_pos: Sequence[int],
    facing: str = "north",
    width: Optional[int] = None,
    height: Optional[int] = None,
    variant: Optional[str] = None,
) -> Tuple[float, float, float]:
    """Calculates the world center position for a painting attached to a support block.

    Args:
        support_pos: [x, y, z] integer coordinates of the support/anchor block.
        facing: Direction painting faces outward into open air ("north", "south", "east", "west").
        width: Optional width in blocks.
        height: Optional height in blocks.
        variant: Optional variant name to infer width/height if not explicitly given.

    Returns:
        [x, y, z] float world center coordinates.
    """
    mtk = get_libmtk()
    pos = [int(x) for x in support_pos]
    res = mtk.calculate_painting_position(pos, facing, width, height, variant)
    return (res[0], res[1], res[2])
