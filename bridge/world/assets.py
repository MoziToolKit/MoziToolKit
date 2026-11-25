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
from ..config_validation import (
    sanitize_bool_property,
    sanitize_unknown_block_strategy,
)
from ..engine import get_libmtk

logger = logging.getLogger("MoziToolKit.Bridge.World.Assets")

_WORLD_PIPELINE_ASSETS_CACHE: Optional[Tuple[Any, Any, Any]] = None
# Cheap resolution signature the memo above was built for. Deliberately *not* the stack
# fingerprint: computing that walks every pack file, and doing it on every mesh call cost
# 300-400 ms when no cache matched (the exact case where the memo never engaged).
_WORLD_PIPELINE_SIGNATURE: Optional[Tuple[Any, ...]] = None

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
    global _WORLD_PIPELINE_ASSETS_CACHE, _WORLD_PIPELINE_SIGNATURE
    _WORLD_PIPELINE_ASSETS_CACHE = None
    _WORLD_PIPELINE_SIGNATURE = None


def _cache_dir_packages_sig(cache_dir: str) -> Tuple[Tuple[str, int], ...]:
    """Signature of all `.mtkcache` files in `cache_dir` (name + mtime_ns)."""
    try:
        from pathlib import Path

        entries = []
        for entry in Path(cache_dir).glob("*.mtkcache"):
            try:
                entries.append((entry.name, entry.stat().st_mtime_ns))
            except OSError:
                continue
        return tuple(sorted(entries))
    except Exception:
        return ()


def _assets_signature(prefs=None) -> Tuple[Any, ...]:
    """Cheap key for the asset memo: packs, cache dir, package identities.

    Includes all package filenames and mtimes so adding, baking, replacing, or
    deleting a cache invalidates the memo without recomputing the stack fingerprint.
    """
    from ..assets import get_cache_dir
    from ..assets.pack import get_configured_pack_paths

    try:
        paths = tuple(get_configured_pack_paths(prefs))
    except Exception:
        paths = ()
    try:
        cache_dir = str(get_cache_dir(prefs))
    except Exception:
        cache_dir = ""
    return (paths, cache_dir, _cache_dir_packages_sig(cache_dir))


def get_world_pipeline_assets(prefs=None) -> Tuple[Optional[Any], Optional[Any], Optional[Any]]:
    """
    Acquires precompiled assets required for high-fidelity world meshing:
    Returns (model_db, atlas, biome_resolver).

    Memoized against a cheap resolution signature (packs, cache dir, newest package mtime).
    A miss is memoized too: when the configured packs do not match any package, every caller
    used to re-walk the pack directory and re-open the cache, which is exactly the misconfigured
    case where meshing silently fell back to cubes and took seconds per rebuild.
    The failure is logged once per signature so it is observable, and callers still receive
    `(None, None, None)` to keep their explicit fallback policy.
    """
    global _WORLD_PIPELINE_ASSETS_CACHE, _WORLD_PIPELINE_SIGNATURE

    signature = _assets_signature(prefs)
    if _WORLD_PIPELINE_ASSETS_CACHE is not None and _WORLD_PIPELINE_SIGNATURE == signature:
        return _WORLD_PIPELINE_ASSETS_CACHE

    model_db = load_baked_model_database(prefs, verify_fingerprint=False)
    atlas = load_baked_atlas_from_cache(prefs)
    biome_resolver = load_biome_resolver_from_cache(prefs)

    if model_db is None or atlas is None or biome_resolver is None:
        logger.warning(
            "World pipeline assets incomplete (model_db=%s atlas=%s biome_resolver=%s) for packs %s; "
            "meshing will fall back to untextured cubes until a matching %s cache exists. "
            "Run the asset precompile operator.",
            model_db is not None,
            atlas is not None,
            biome_resolver is not None,
            list(signature[0]),
            "*.mtkcache",
        )

    _WORLD_PIPELINE_ASSETS_CACHE = (model_db, atlas, biome_resolver)
    _WORLD_PIPELINE_SIGNATURE = signature
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
        anim_enabled = sanitize_bool_property("enable_animated_textures", raw_anim, True, log=logger)
    else:
        anim_enabled = sanitize_bool_property(
            "enable_animated_textures", enable_animated_textures, True, log=logger
        )
    skin_cache_dir = None
    try:
        from ..assets import get_cache_dir
        sc = get_cache_dir(prefs) / "skins"
        sc.mkdir(parents=True, exist_ok=True)
        skin_cache_dir = str(sc)
    except Exception as e:
        logger.debug("Failed configuring skin cache dir in prepare_world_mesh_context: %s", e)
    unknown_block_strategy = sanitize_unknown_block_strategy(
        getattr(prefs, "unknown_block_strategy", "FALLBACK_CUBE")
        if prefs is not None
        else "FALLBACK_CUBE",
        log=logger,
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
