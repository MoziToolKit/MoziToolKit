"""
MoziToolKit World Bridge - Pipeline Assets & Culler Cache Submodule.
Manages precompiled world meshing assets, colormaps, and shared process-wide FaceCuller.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Tuple

from ..assets import (
    get_active_cache_package_path,
    get_cache_fingerprint,
    get_cache_timestamp,
    get_configured_ctm_solver,
    load_baked_atlas_from_cache,
    load_baked_model_database,
    load_biome_resolver_from_cache,
    load_colormaps_from_cache,
)
from ..engine import get_libmtk

logger = logging.getLogger("MoziToolKit.Bridge.World.Assets")

_WORLD_PIPELINE_ASSETS_CACHE: Optional[Tuple[Any, Any, Any]] = None
_WORLD_PIPELINE_FP_CACHE: Optional[str] = None
_WORLD_PIPELINE_TS_CACHE: float = 0.0

# Reused across rebuilds: the Rust `FaceCuller` owns a state-string keyed metadata
# cache, so constructing a fresh instance per call forces it to re-derive cull
# metadata for every unique blockstate. The cull modes are fixed here, so a single
# instance is safe for the whole session.
_SHARED_FACE_CULLER: Optional[Any] = None
_SHARED_FACE_CULLER_OWNER: Optional[Any] = None


def get_shared_face_culler(mtk: Optional[Any] = None) -> Optional[Any]:
    """Returns a process-wide `FaceCuller` instance (or None when unavailable).

    The instance is tied to the native module that created it, so reloading the
    extension produces a fresh culler instead of reusing a stale native object.
    """
    global _SHARED_FACE_CULLER, _SHARED_FACE_CULLER_OWNER
    mtk = mtk or get_libmtk()
    if mtk is None:
        return None
    if _SHARED_FACE_CULLER is not None and _SHARED_FACE_CULLER_OWNER is mtk:
        return _SHARED_FACE_CULLER
    _SHARED_FACE_CULLER = mtk.FaceCuller()
    _SHARED_FACE_CULLER_OWNER = mtk
    return _SHARED_FACE_CULLER


def reset_shared_face_culler() -> None:
    """Drops the shared culler so the next call rebuilds (and re-warms) its cache."""
    global _SHARED_FACE_CULLER, _SHARED_FACE_CULLER_OWNER
    _SHARED_FACE_CULLER = None
    _SHARED_FACE_CULLER_OWNER = None


def invalidate_world_pipeline_assets() -> None:
    """Invalidates in-memory cached pipeline assets tuple."""
    global _WORLD_PIPELINE_ASSETS_CACHE, _WORLD_PIPELINE_FP_CACHE, _WORLD_PIPELINE_TS_CACHE
    _WORLD_PIPELINE_ASSETS_CACHE = None
    _WORLD_PIPELINE_FP_CACHE = None
    _WORLD_PIPELINE_TS_CACHE = 0.0


def get_world_pipeline_assets(prefs=None) -> Tuple[Optional[Any], Optional[Any], Optional[Any]]:
    """
    Acquires precompiled assets required for high-fidelity world meshing:
    Returns (model_db, atlas, biome_resolver).
    Memoized in memory against the active package fingerprint and timestamp.
    """
    global _WORLD_PIPELINE_ASSETS_CACHE, _WORLD_PIPELINE_FP_CACHE, _WORLD_PIPELINE_TS_CACHE
    fp = get_cache_fingerprint(prefs)
    ts = get_cache_timestamp(prefs)
    if (
        _WORLD_PIPELINE_ASSETS_CACHE is not None
        and _WORLD_PIPELINE_FP_CACHE == fp
        and _WORLD_PIPELINE_TS_CACHE == ts
        and fp is not None
    ):
        return _WORLD_PIPELINE_ASSETS_CACHE

    model_db = load_baked_model_database(prefs, verify_fingerprint=False)
    atlas = load_baked_atlas_from_cache(prefs)
    biome_resolver = load_biome_resolver_from_cache(prefs)
    _WORLD_PIPELINE_ASSETS_CACHE = (model_db, atlas, biome_resolver)
    _WORLD_PIPELINE_FP_CACHE = fp
    _WORLD_PIPELINE_TS_CACHE = ts
    return _WORLD_PIPELINE_ASSETS_CACHE


def get_world_pipeline_colormaps(prefs=None) -> Optional[Any]:
    """Acquires precompiled colormaps from active asset cache for biome tinting."""
    return load_colormaps_from_cache(prefs)


def prepare_world_mesh_context(prefs=None, enable_animated_textures: Optional[bool] = None) -> Dict[str, Any]:
    """Resolve every asset/config value a background mesher needs, on the main thread.

    The returned dict is plain Python/Rust data: it contains no Blender RNA
    objects, so it is safe to pass into an AsyncTask worker. Call this only after
    the asset cache has been verified/precompiled on the main thread.
    """
    model_db, atlas, biome_resolver = get_world_pipeline_assets(prefs)
    colormaps = get_world_pipeline_colormaps(prefs)
    active_pkg = get_active_cache_package_path(prefs)
    ctm_solver = get_configured_ctm_solver(prefs)
    raw_threads = getattr(prefs, "thread_count", 0) if prefs is not None else 0
    num_threads = raw_threads if isinstance(raw_threads, int) and not isinstance(raw_threads, bool) and raw_threads > 0 else None
    if enable_animated_textures is None:
        raw_anim = getattr(prefs, "enable_animated_textures", True) if prefs is not None else True
        anim_enabled = raw_anim if isinstance(raw_anim, bool) else True
    else:
        anim_enabled = bool(enable_animated_textures)
    skin_cache_dir = None
    try:
        from ..assets import get_cache_dir
        sc = get_cache_dir(prefs) / "skins"
        sc.mkdir(parents=True, exist_ok=True)
        skin_cache_dir = str(sc)
    except Exception as e:
        logger.debug("Failed configuring skin cache dir in prepare_world_mesh_context: %s", e)
    unknown_block_strategy = (
        getattr(prefs, "unknown_block_strategy", "FALLBACK_CUBE")
        if prefs is not None
        else "FALLBACK_CUBE"
    )
    return {
        "model_db": model_db,
        "atlas": atlas,
        "biome_resolver": biome_resolver,
        "colormaps": colormaps,
        "cache_package_path": str(active_pkg.resolve()) if active_pkg else None,
        "ctm_solver": ctm_solver,
        "num_threads": num_threads,
        "enable_animated_textures": anim_enabled,
        "skin_cache_dir": skin_cache_dir,
        "cache_fingerprint": get_cache_fingerprint(prefs),
        "cache_timestamp": get_cache_timestamp(prefs),
        "unknown_block_strategy": unknown_block_strategy,
    }
