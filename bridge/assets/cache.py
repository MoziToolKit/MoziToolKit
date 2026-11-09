"""
Asset Cache Reader & Package Lifecycle Subsystem.
Handles persistent .mtkcache container opening, zero-disk texture extraction,
and precompiled asset manifest queries.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from ..engine import get_libmtk
from .pack import get_configured_pack_stack
from .readers import (
    get_cached_texture_f32 as _readers_get_cached_texture_f32,
    get_cached_texture_rgba as _readers_get_cached_texture_rgba,
    load_atlas_mapping_from_cache as _readers_load_atlas_mapping,
    load_baked_atlas_inner,
    load_baked_model_database_inner,
    load_biome_resolver_inner,
    load_colormaps_inner,
)
from ...utils.assets.cache_utils import (
    clear_cache as _utils_clear_cache,
    get_cache_dir,
    get_cache_stats,
    open_cache_folder,
)

clear_cache = _utils_clear_cache

logger = logging.getLogger("MoziToolKit.Bridge.Assets.Cache")

# In-memory session memoization to eliminate redundant disk hashing & package reopening
_ACTIVE_ASSET_CACHE: Optional[Any] = None
_ACTIVE_CACHE_PKG_PATH: Optional[str] = None
_ACTIVE_CACHE_MTIME: float = 0.0
_ACTIVE_CACHE_MANIFEST: Optional[Dict[str, Any]] = None

_CACHED_BIOME_RESOLVER: Optional[Any] = None
_CACHED_BIOME_RESOLVER_PKG_PATH: Optional[str] = None
_CACHED_BIOME_RESOLVER_MTIME: float = 0.0

_CACHED_ATLAS: Optional[Any] = None
_CACHED_ATLAS_PKG_PATH: Optional[str] = None
_CACHED_ATLAS_MTIME: float = 0.0

_CACHED_MODEL_DB: Optional[Any] = None
_CACHED_MODEL_DB_PKG_PATH: Optional[str] = None
_CACHED_MODEL_DB_MTIME: float = 0.0
_CACHED_COLORMAPS: Optional[Any] = None
_CACHED_COLORMAPS_PKG_PATH: Optional[str] = None
_CACHED_COLORMAPS_MTIME: float = 0.0


def invalidate_asset_cache_memory() -> None:
    """Invalidates in-memory cached AssetCache, manifest, and pipeline objects."""
    global _ACTIVE_ASSET_CACHE, _ACTIVE_CACHE_PKG_PATH, _ACTIVE_CACHE_MTIME, _ACTIVE_CACHE_MANIFEST
    global _CACHED_BIOME_RESOLVER, _CACHED_BIOME_RESOLVER_PKG_PATH, _CACHED_BIOME_RESOLVER_MTIME
    global _CACHED_ATLAS, _CACHED_ATLAS_PKG_PATH, _CACHED_ATLAS_MTIME
    global _CACHED_MODEL_DB, _CACHED_MODEL_DB_PKG_PATH, _CACHED_MODEL_DB_MTIME
    global _CACHED_COLORMAPS, _CACHED_COLORMAPS_PKG_PATH, _CACHED_COLORMAPS_MTIME
    _ACTIVE_ASSET_CACHE = None
    _ACTIVE_CACHE_PKG_PATH = None
    _ACTIVE_CACHE_MTIME = 0.0
    _ACTIVE_CACHE_MANIFEST = None
    _CACHED_BIOME_RESOLVER = None
    _CACHED_BIOME_RESOLVER_PKG_PATH = None
    _CACHED_BIOME_RESOLVER_MTIME = 0.0
    _CACHED_ATLAS = None
    _CACHED_ATLAS_PKG_PATH = None
    _CACHED_ATLAS_MTIME = 0.0
    _CACHED_MODEL_DB = None
    _CACHED_MODEL_DB_PKG_PATH = None
    _CACHED_MODEL_DB_MTIME = 0.0
    _CACHED_COLORMAPS = None
    _CACHED_COLORMAPS_PKG_PATH = None
    _CACHED_COLORMAPS_MTIME = 0.0
    try:
        from .pack import invalidate_pack_stack_cache
        invalidate_pack_stack_cache()
    except Exception:
        pass
    try:
        from ..world import invalidate_world_pipeline_assets, clear_world_sessions
        invalidate_world_pipeline_assets()
        clear_world_sessions()
    except Exception:
        pass

    try:
        from ...utils.assets.guard import invalidate_guard_cache
        invalidate_guard_cache()
    except Exception:
        pass

    try:
        from ...utils.assets.cache_utils import invalidate_cache_stats_memo
        invalidate_cache_stats_memo()
    except Exception:
        pass

    try:
        from ..debug import clear_debug_world_cache
        clear_debug_world_cache()
    except Exception:
        pass


def get_active_cache_package_path(prefs=None, compute_fingerprint: bool = True) -> Optional[Path]:
    """
    Returns the Path to the active .mtkcache file for the configured pack stack.
    Checks stack fingerprint match if compute_fingerprint is True (default), strictly
    binding active cache to the configured resource pack stack state.
    Falls back to newest package by mtime only when no pack stack is configured.
    """
    global _ACTIVE_CACHE_PKG_PATH, _ACTIVE_ASSET_CACHE
    cache_dir = get_cache_dir(prefs).resolve()
    if not cache_dir.exists():
        invalidate_asset_cache_memory()
        return None

    # O(1) Fast-path: return cached valid path immediately if not checking fingerprint
    if not compute_fingerprint and _ACTIVE_CACHE_PKG_PATH is not None:
        p = Path(_ACTIVE_CACHE_PKG_PATH)
        if p.parent == cache_dir and p.exists() and p.is_file():
            return p

    mtk = get_libmtk()

    is_test_sandbox = os.environ.get("MTK_TEST_SANDBOX") == "1"

    # Test sandbox isolation guard: if isolated via MTK_TEST_SANDBOX and caller did not specify custom prefs,
    # avoid letting host-level global preferences override synthetic test packages.
    if prefs is None and is_test_sandbox:
        compute_fingerprint = False

    if compute_fingerprint:
        stack = get_configured_pack_stack(prefs)
        if stack is not None and stack.get_pack_count() > 0:
            fp = stack.compute_stack_fingerprint()

            # O(1) fast path: current package already matches the stack fingerprint.
            if _ACTIVE_CACHE_PKG_PATH is not None:
                p = Path(_ACTIVE_CACHE_PKG_PATH)
                if p.parent == cache_dir and p.exists() and p.is_file():
                    cached_fp = getattr(_ACTIVE_ASSET_CACHE, "fingerprint", None)
                    if cached_fp == fp or p.stem == fp or p.stem == f"mtk_fp_{fp}":
                        return p
                invalidate_asset_cache_memory()

            # Host-agnostic Rust resolver: header fingerprint validation + mtime fallback.
            if mtk is not None:
                resolved = mtk.resolve_active_cache_package(str(cache_dir), fp)
                if resolved:
                    return Path(resolved)

                # Test sandboxes may contain synthetic packages whose header does not
                # match the host-level stack fingerprint.
                if is_test_sandbox:
                    candidates = sorted(
                        cache_dir.glob("*.mtkcache"),
                        key=lambda p: p.stat().st_mtime,
                        reverse=True,
                    )
                    if candidates:
                        return candidates[0]

                if _ACTIVE_ASSET_CACHE is not None or _ACTIVE_CACHE_PKG_PATH is not None:
                    invalidate_asset_cache_memory()
                return None

    # Fallback when compute_fingerprint=False or no pack stack configured
    if _ACTIVE_CACHE_PKG_PATH is not None:
        p = Path(_ACTIVE_CACHE_PKG_PATH)
        if p.parent != cache_dir:
            invalidate_asset_cache_memory()
        elif p.exists() and p.is_file():
            return p

    # Search for all .mtkcache files in cache dir sorted by mtime descending
    candidates = sorted(cache_dir.glob("*.mtkcache"), key=lambda p: p.stat().st_mtime, reverse=True)
    if candidates:
        return candidates[0]
    return None


def open_active_asset_cache(prefs=None) -> Optional[Any]:
    """Opens active AssetCache reader from .mtkcache package, reusing open instance when valid."""
    global _ACTIVE_ASSET_CACHE, _ACTIVE_CACHE_PKG_PATH, _ACTIVE_CACHE_MTIME, _ACTIVE_CACHE_MANIFEST
    mtk = get_libmtk()
    if mtk is None:
        return None

    cache_dir = get_cache_dir(prefs).resolve()
    if not cache_dir.exists():
        if _ACTIVE_ASSET_CACHE is not None or _ACTIVE_CACHE_PKG_PATH is not None:
            invalidate_asset_cache_memory()
        return None

    # O(1) Fast-path: return currently cached instance if path & mtime still match on disk
    if _ACTIVE_ASSET_CACHE is not None and _ACTIVE_CACHE_PKG_PATH is not None:
        p = Path(_ACTIVE_CACHE_PKG_PATH)
        if p.parent == cache_dir and p.exists() and p.is_file():
            try:
                mtime = p.stat().st_mtime
                if _ACTIVE_CACHE_MTIME == mtime:
                    return _ACTIVE_ASSET_CACHE
            except Exception:
                pass

    if _ACTIVE_CACHE_PKG_PATH is not None and Path(_ACTIVE_CACHE_PKG_PATH).parent != cache_dir:
        invalidate_asset_cache_memory()

    pkg = get_active_cache_package_path(prefs, compute_fingerprint=True)
    if pkg is None or not pkg.exists():
        if _ACTIVE_ASSET_CACHE is not None or _ACTIVE_CACHE_PKG_PATH is not None:
            invalidate_asset_cache_memory()
        return None

    pkg_str = str(pkg.resolve())
    try:
        mtime = pkg.stat().st_mtime
    except Exception:
        mtime = 0.0

    if (
        _ACTIVE_ASSET_CACHE is not None
        and _ACTIVE_CACHE_PKG_PATH == pkg_str
        and _ACTIVE_CACHE_MTIME == mtime
    ):
        return _ACTIVE_ASSET_CACHE

    try:
        _ACTIVE_ASSET_CACHE = mtk.AssetCache.open(pkg_str)
        _ACTIVE_CACHE_PKG_PATH = pkg_str
        _ACTIVE_CACHE_MTIME = mtime
        _ACTIVE_CACHE_MANIFEST = _ACTIVE_ASSET_CACHE.get_manifest()
        return _ACTIVE_ASSET_CACHE
    except Exception as e:
        logger.debug("Failed opening active asset cache: %s", e)
        invalidate_asset_cache_memory()
        return None


def load_atlas_mapping_from_cache(prefs=None) -> Optional[Dict[str, Any]]:
    """Loads precompiled atlas mapping table as dictionary directly from memory."""
    return _readers_load_atlas_mapping(open_active_asset_cache, prefs)


def get_cached_texture_rgba(chunk_id: str, prefs=None, flip_y: bool = True) -> Optional[Tuple[int, int, Any]]:
    """Reads and decodes a texture chunk directly from .mtkcache into memory as (width, height, memoryview)."""
    return _readers_get_cached_texture_rgba(open_active_asset_cache, chunk_id, prefs, flip_y)


def get_cached_texture_f32(chunk_id: str, prefs=None, flip_y: bool = True) -> Optional[Tuple[int, int, Any]]:
    """Reads and decodes a texture chunk directly into normalized float32 memoryview (width, height, memoryview)."""
    return _readers_get_cached_texture_f32(open_active_asset_cache, chunk_id, prefs, flip_y)


def ensure_atlas_textures_extracted(prefs=None) -> Path:
    """Returns the atlas cache directory path reference."""
    base_cache = get_cache_dir(prefs)
    return base_cache / "atlas"


def load_baked_model_database(prefs=None, verify_fingerprint: bool = False) -> Optional[Any]:
    """Loads precompiled binary model database from cache package into memory."""
    global _CACHED_MODEL_DB, _CACHED_MODEL_DB_PKG_PATH, _CACHED_MODEL_DB_MTIME
    db, pkg, mtime = load_baked_model_database_inner(
        open_active_asset_cache,
        _ACTIVE_CACHE_PKG_PATH,
        _ACTIVE_CACHE_MTIME,
        _CACHED_MODEL_DB,
        _CACHED_MODEL_DB_PKG_PATH,
        _CACHED_MODEL_DB_MTIME,
        prefs,
        verify_fingerprint,
    )
    _CACHED_MODEL_DB = db
    _CACHED_MODEL_DB_PKG_PATH = pkg
    _CACHED_MODEL_DB_MTIME = mtime
    return db


def load_model_database_from_cache(prefs=None, verify_fingerprint: bool = False) -> Optional[Any]:
    """Alias for load_baked_model_database."""
    return load_baked_model_database(prefs, verify_fingerprint=verify_fingerprint)


def load_baked_atlas_from_cache(prefs=None) -> Optional[Any]:
    """Loads precompiled BakedAtlas from cache package into memory, reusing cached instance."""
    global _CACHED_ATLAS, _CACHED_ATLAS_PKG_PATH, _CACHED_ATLAS_MTIME
    atlas, pkg, mtime = load_baked_atlas_inner(
        open_active_asset_cache,
        _ACTIVE_CACHE_PKG_PATH,
        _ACTIVE_CACHE_MTIME,
        _CACHED_ATLAS,
        _CACHED_ATLAS_PKG_PATH,
        _CACHED_ATLAS_MTIME,
        prefs,
    )
    _CACHED_ATLAS = atlas
    _CACHED_ATLAS_PKG_PATH = pkg
    _CACHED_ATLAS_MTIME = mtime
    return atlas


load_atlas_from_cache = load_baked_atlas_from_cache


def load_biome_resolver_from_cache(prefs=None) -> Optional[Any]:
    """Loads precompiled BiomeResolver from cache package into memory, reusing cached instance."""
    global _CACHED_BIOME_RESOLVER, _CACHED_BIOME_RESOLVER_PKG_PATH, _CACHED_BIOME_RESOLVER_MTIME
    res, pkg, mtime = load_biome_resolver_inner(
        open_active_asset_cache,
        _ACTIVE_CACHE_PKG_PATH,
        _ACTIVE_CACHE_MTIME,
        _CACHED_BIOME_RESOLVER,
        _CACHED_BIOME_RESOLVER_PKG_PATH,
        _CACHED_BIOME_RESOLVER_MTIME,
        prefs,
    )
    _CACHED_BIOME_RESOLVER = res
    _CACHED_BIOME_RESOLVER_PKG_PATH = pkg
    _CACHED_BIOME_RESOLVER_MTIME = mtime
    return res


def load_colormaps_from_cache(prefs=None) -> Optional[Any]:
    """Loads precompiled VanillaColormaps from active cache package into memory, reusing cached instance."""
    global _CACHED_COLORMAPS, _CACHED_COLORMAPS_PKG_PATH, _CACHED_COLORMAPS_MTIME
    cms, pkg, mtime = load_colormaps_inner(
        open_active_asset_cache,
        _ACTIVE_CACHE_PKG_PATH,
        _ACTIVE_CACHE_MTIME,
        _CACHED_COLORMAPS,
        _CACHED_COLORMAPS_PKG_PATH,
        _CACHED_COLORMAPS_MTIME,
        prefs,
    )
    _CACHED_COLORMAPS = cms
    _CACHED_COLORMAPS_PKG_PATH = pkg
    _CACHED_COLORMAPS_MTIME = mtime
    return cms


def get_cache_manifest(prefs=None) -> Optional[Dict[str, Any]]:
    """Reads manifest from active .mtkcache package."""
    global _ACTIVE_CACHE_MANIFEST
    cache = open_active_asset_cache(prefs)
    if cache is not None:
        if _ACTIVE_CACHE_MANIFEST is not None:
            return _ACTIVE_CACHE_MANIFEST
        try:
            _ACTIVE_CACHE_MANIFEST = cache.get_manifest()
            return _ACTIVE_CACHE_MANIFEST
        except Exception:
            pass
    return None


def get_cache_fingerprint(prefs=None) -> Optional[str]:
    """Returns active stack fingerprint recorded in package manifest or cache object."""
    cache = open_active_asset_cache(prefs)
    if cache is not None:
        fp = cache.fingerprint
        if fp:
            return str(fp)
    manifest = get_cache_manifest(prefs)
    if manifest and isinstance(manifest, dict):
        return manifest.get("fingerprint")
    return None


def get_cache_timestamp(prefs=None) -> float:
    """Returns creation epoch seconds recorded in package manifest or file mtime."""
    manifest = get_cache_manifest(prefs)
    if manifest and isinstance(manifest, dict) and "created_at_epoch_secs" in manifest:
        try:
            return float(manifest["created_at_epoch_secs"])
        except (ValueError, TypeError):
            pass

    pkg = get_active_cache_package_path(prefs, compute_fingerprint=False)
    if pkg is not None and pkg.exists():
        try:
            return float(pkg.stat().st_mtime)
        except Exception:
            pass

    return 0.0


def check_cache_dirty(
    cached_fingerprint: Optional[str] = None,
    cached_timestamp: float = 0.0,
    prefs=None,
) -> Tuple[bool, Optional[str], float]:
    """Compares cached fingerprint and timestamp with current on-disk asset cache package."""
    current_fp = get_cache_fingerprint(prefs)
    current_ts = get_cache_timestamp(prefs)

    if cached_fingerprint is None and cached_timestamp == 0.0:
        is_dirty = current_fp is not None or current_ts > 0.0
        return is_dirty, current_fp, current_ts

    if current_fp != cached_fingerprint:
        invalidate_asset_cache_memory()
        return True, current_fp, current_ts

    if abs(current_ts - cached_timestamp) > 1e-3:
        invalidate_asset_cache_memory()
        return True, current_fp, current_ts

    return False, current_fp, current_ts


__all__ = [
    "invalidate_asset_cache_memory",
    "get_active_cache_package_path",
    "open_active_asset_cache",
    "load_atlas_mapping_from_cache",
    "get_cached_texture_rgba",
    "get_cached_texture_f32",
    "ensure_atlas_textures_extracted",
    "load_baked_model_database",
    "load_model_database_from_cache",
    "load_baked_atlas_from_cache",
    "load_biome_resolver_from_cache",
    "load_colormaps_from_cache",
    "get_cache_manifest",
    "get_cache_fingerprint",
    "get_cache_timestamp",
    "check_cache_dirty",
    "clear_cache",
    "get_cache_dir",
    "get_cache_stats",
    "open_cache_folder",
]
