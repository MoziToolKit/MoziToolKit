"""
MoziToolKit Asset Cache Readers Submodule.
Routines for loading precompiled models, baked atlases, biome resolvers, and decoded texture buffers.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from ..engine import get_libmtk
from .pack import get_configured_pack_stack

logger = logging.getLogger("MoziToolKit.Bridge.Assets.Readers")


def load_atlas_mapping_from_cache(open_cache_fn, prefs=None) -> Optional[Dict[str, Any]]:
    """
    Loads precompiled atlas mapping table as dictionary directly from memory.
    Reads from the canonical .mtkcache container.
    """
    cache = open_cache_fn(prefs)
    if cache is not None:
        try:
            mapping_str = cache.load_atlas_mapping()
            if mapping_str:
                return json.loads(mapping_str)
        except Exception as exc:
            logger.warning("Failed loading atlas mapping from cache container: %s", exc)
    return None


def get_cached_texture_rgba(open_cache_fn, chunk_id: str, prefs=None, flip_y: bool = True) -> Optional[Tuple[int, int, Any]]:
    """
    Reads and decodes a texture chunk directly from .mtkcache into memory as (width, height, memoryview).
    `flip_y`: If True (default), row order is vertically flipped natively in Rust (bottom-to-top layout)
              to match Blender's native Image.pixels coordinate system with zero Python overhead.
    """
    cache = open_cache_fn(prefs)
    if cache is not None:
        try:
            return cache.read_texture_rgba(chunk_id, flip_v=flip_y)
        except Exception:
            return None
    return None


def get_cached_texture_f32(open_cache_fn, chunk_id: str, prefs=None, flip_y: bool = True) -> Optional[Tuple[int, int, Any]]:
    """
    Reads and decodes a texture chunk directly into normalized float32 memoryview (width, height, memoryview).
    `flip_y`: If True (default), row order is vertically flipped natively in Rust to match Blender Image.pixels.
    Allows zero-copy / direct feeding into `img.pixels.foreach_set` without any Python numpy conversion.
    """
    cache = open_cache_fn(prefs)
    if cache is not None:
        try:
            return cache.read_texture_f32(chunk_id, flip_v=flip_y)
        except Exception:
            pass
    return None


def load_baked_model_database_inner(
    open_cache_fn,
    active_pkg_path: Optional[str],
    active_mtime: float,
    cached_db: Optional[Any],
    cached_pkg: Optional[str],
    cached_mtime: float,
    prefs=None,
    verify_fingerprint: bool = False,
) -> Tuple[Optional[Any], Optional[str], float]:
    """Loads precompiled binary model database from cache package into memory."""
    mtk = get_libmtk()
    if mtk is None:
        return None, cached_pkg, cached_mtime

    cache = open_cache_fn(prefs)
    if cache is None:
        return None, cached_pkg, cached_mtime

    if verify_fingerprint:
        stack = get_configured_pack_stack(prefs)
        if stack is not None:
            current_fp = stack.compute_stack_fingerprint()
            if getattr(cache, "fingerprint", None) != current_fp:
                return None, cached_pkg, cached_mtime

    if (
        not verify_fingerprint
        and cached_db is not None
        and cached_pkg == active_pkg_path
        and cached_mtime == active_mtime
    ):
        return cached_db, cached_pkg, cached_mtime

    try:
        model_db = cache.load_models()
        return model_db, active_pkg_path, active_mtime
    except Exception as exc:
        logger.warning("Failed loading baked model database from %s: %s", active_pkg_path, exc)
        return None, cached_pkg, cached_mtime


def load_baked_atlas_inner(
    open_cache_fn,
    active_pkg_path: Optional[str],
    active_mtime: float,
    cached_atlas: Optional[Any],
    cached_pkg: Optional[str],
    cached_mtime: float,
    prefs=None,
) -> Tuple[Optional[Any], Optional[str], float]:
    """Loads precompiled BakedAtlas from cache package into memory."""
    mtk = get_libmtk()
    if mtk is None:
        return None, cached_pkg, cached_mtime

    cache = open_cache_fn(prefs)
    if cache is not None:
        if (
            cached_atlas is not None
            and cached_pkg == active_pkg_path
            and cached_mtime == active_mtime
        ):
            return cached_atlas, cached_pkg, cached_mtime

        try:
            atlas = cache.load_atlas()
            return atlas, active_pkg_path, active_mtime
        except Exception as exc:
            logger.warning("Failed loading baked atlas from %s: %s", active_pkg_path, exc)
    return None, cached_pkg, cached_mtime


def load_biome_resolver_inner(
    open_cache_fn,
    active_pkg_path: Optional[str],
    active_mtime: float,
    cached_resolver: Optional[Any],
    cached_pkg: Optional[str],
    cached_mtime: float,
    prefs=None,
) -> Tuple[Optional[Any], Optional[str], float]:
    """Loads precompiled BiomeResolver from cache package into memory."""
    mtk = get_libmtk()
    if mtk is None:
        return None, cached_pkg, cached_mtime

    cache = open_cache_fn(prefs)
    if cache is not None:
        if (
            cached_resolver is not None
            and cached_pkg == active_pkg_path
            and cached_mtime == active_mtime
        ):
            return cached_resolver, cached_pkg, cached_mtime

        try:
            resolver = cache.load_biome_resolver()
            if resolver is not None:
                return resolver, active_pkg_path, active_mtime
        except Exception as exc:
            logger.warning("Failed loading biome resolver from %s: %s", active_pkg_path, exc)

    try:
        return mtk.BiomeResolver(), None, 0.0
    except Exception as exc:
        logger.warning("Failed instantiating default BiomeResolver: %s", exc)
    return None, cached_pkg, cached_mtime


def load_colormaps_inner(
    open_cache_fn,
    active_pkg_path: Optional[str],
    active_mtime: float,
    cached_cms: Optional[Any],
    cached_pkg: Optional[str],
    cached_mtime: float,
    prefs=None,
) -> Tuple[Optional[Any], Optional[str], float]:
    """Loads precompiled VanillaColormaps from active cache package into memory."""
    mtk = get_libmtk()
    if mtk is None:
        return None, cached_pkg, cached_mtime

    cache = open_cache_fn(prefs)
    if cache is not None:
        if (
            cached_cms is not None
            and cached_pkg == active_pkg_path
            and cached_mtime == active_mtime
        ):
            return cached_cms, cached_pkg, cached_mtime

        try:
            colormaps = cache.load_colormaps()
            return colormaps, active_pkg_path, active_mtime
        except Exception as exc:
            logger.warning("Failed loading colormaps from %s: %s", active_pkg_path, exc)
    return None, cached_pkg, cached_mtime
