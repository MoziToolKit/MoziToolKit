"""
Asset Cache Reader & Package Lifecycle Subsystem.
Handles persistent .mtkcache container opening, zero-disk texture extraction,
and precompiled asset manifest queries.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from ..engine import get_libmtk
from ..prefs import get_prefs
from .pack import (
    get_configured_pack_paths,
    get_configured_pack_stack,
    get_configured_stack_fingerprint,
)
from .readers import (
    get_cached_texture_f32 as _readers_get_cached_texture_f32,
    get_cached_texture_rgba as _readers_get_cached_texture_rgba,
    load_atlas_mapping_from_cache as _readers_load_atlas_mapping,
    load_baked_atlas_inner,
    load_baked_model_database_inner,
    load_biome_resolver_inner,
    load_colormaps_inner,
    load_ctm_solver_inner,
)

logger = logging.getLogger("MoziToolKit.Bridge.Assets.Cache")

_CACHE_INVALIDATION_CALLBACKS: list[Any] = []


def register_cache_invalidation_callback(callback: Any) -> None:
    """Registers a listener to be notified when the in-memory asset cache is invalidated."""
    if callback not in _CACHE_INVALIDATION_CALLBACKS:
        _CACHE_INVALIDATION_CALLBACKS.append(callback)


def unregister_cache_invalidation_callback(callback: Any) -> None:
    """Unregisters a previously registered asset cache invalidation listener."""
    if callback in _CACHE_INVALIDATION_CALLBACKS:
        _CACHE_INVALIDATION_CALLBACKS.remove(callback)


def _resolve_prefs(prefs=None):
    if prefs is not None:
        return prefs
    return get_prefs()


def _resolve_prefs_cache_dir(target_prefs: Any) -> Optional[Path]:
    if target_prefs is None or not hasattr(target_prefs, "cache_dir"):
        return None
    raw = str(getattr(target_prefs, "cache_dir", "") or "").strip()
    if not raw:
        return None
    try:
        import bpy
        resolved = bpy.path.abspath(raw)
    except Exception:
        resolved = raw
    p = Path(resolved)
    p.mkdir(parents=True, exist_ok=True)
    return p


def get_cache_dir(prefs=None) -> Path:
    """
    Returns the persistent cache directory for compiled assets.
    Delegates path resolution to the host (Blender/Python):
    1. Caller-specified explicit `prefs.cache_dir` takes highest precedence (isolates synthetic unit tests).
    2. Environment override (`MOZI_CACHE_DIR`) for test sandboxing / isolation.
    3. User-customized path in AddonPreferences (`prefs.cache_dir`).
    4. Blender standard plugin user data directory:
       `bpy.utils.user_resource("DATAFILES") / "MoziToolKit" / "cache"`
    5. Fallback to `~/.config/blender/MoziToolKit/cache`.
    """
    # 1. Highest precedence: explicit caller-provided prefs with non-empty cache_dir
    explicit = _resolve_prefs_cache_dir(prefs)
    if explicit is not None:
        return explicit

    # 2. Environment override (`MOZI_CACHE_DIR`) for test sandboxing / isolation
    env_dir = os.environ.get("MOZI_CACHE_DIR")
    if env_dir:
        p = Path(env_dir)
        p.mkdir(parents=True, exist_ok=True)
        return p

    # 3. User-customized path in AddonPreferences
    resolved_pref_dir = _resolve_prefs_cache_dir(_resolve_prefs(prefs))
    if resolved_pref_dir is not None:
        return resolved_pref_dir

    from ..prefs import get_extension_user_dir

    return get_extension_user_dir(subdir="cache", legacy_resource="DATAFILES")

# In-memory session memoization to eliminate redundant disk hashing & package reopening
_ACTIVE_ASSET_CACHE: Optional[Any] = None
_ACTIVE_CACHE_PKG_PATH: Optional[str] = None
_ACTIVE_CACHE_MTIME: float = 0.0
_ACTIVE_CACHE_PACK_SIG: Optional[Tuple[Tuple[str, int], ...]] = None
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
_CACHED_CTM_SOLVER: Optional[Any] = None
_CACHED_CTM_SOLVER_PKG_PATH: Optional[str] = None
_CACHED_CTM_SOLVER_MTIME: float = 0.0


def invalidate_asset_cache_memory() -> None:
    """Invalidates in-memory cached AssetCache, manifest, and pipeline objects."""
    global _ACTIVE_ASSET_CACHE, _ACTIVE_CACHE_PKG_PATH, _ACTIVE_CACHE_MTIME, _ACTIVE_CACHE_PACK_SIG, _ACTIVE_CACHE_MANIFEST
    global _CACHED_BIOME_RESOLVER, _CACHED_BIOME_RESOLVER_PKG_PATH, _CACHED_BIOME_RESOLVER_MTIME
    global _CACHED_ATLAS, _CACHED_ATLAS_PKG_PATH, _CACHED_ATLAS_MTIME
    global _CACHED_MODEL_DB, _CACHED_MODEL_DB_PKG_PATH, _CACHED_MODEL_DB_MTIME
    global _CACHED_COLORMAPS, _CACHED_COLORMAPS_PKG_PATH, _CACHED_COLORMAPS_MTIME
    global _CACHED_CTM_SOLVER, _CACHED_CTM_SOLVER_PKG_PATH, _CACHED_CTM_SOLVER_MTIME
    _ACTIVE_ASSET_CACHE = None
    _ACTIVE_CACHE_PKG_PATH = None
    _ACTIVE_CACHE_MTIME = 0.0
    _ACTIVE_CACHE_PACK_SIG = None
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
    _CACHED_CTM_SOLVER = None
    _CACHED_CTM_SOLVER_PKG_PATH = None
    _CACHED_CTM_SOLVER_MTIME = 0.0
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

    for callback in list(_CACHE_INVALIDATION_CALLBACKS):
        try:
            callback()
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

    if compute_fingerprint:
        fp = get_configured_stack_fingerprint(prefs)
        if fp:
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

                if _ACTIVE_ASSET_CACHE is not None or _ACTIVE_CACHE_PKG_PATH is not None:
                    invalidate_asset_cache_memory()
                return None

    # No fingerprint means no way to prove the package matches the configured packs. Returning
    # the newest file would serve a cache built from a different pack stack, which is exactly the
    # "I changed my textures and nothing happened" failure. Callers must configure packs.
    return None


def _cheap_pack_paths_sig(prefs=None) -> Tuple[Tuple[str, int], ...]:
    """O(num_packs) signature of configured pack paths and top-level mtimes (no tree walk)."""
    sig = []
    for raw in get_configured_pack_paths(prefs):
        try:
            mtime_ns = os.stat(raw).st_mtime_ns
        except OSError:
            mtime_ns = 0
        sig.append((raw, mtime_ns))
    return tuple(sig)


def _get_active_cache_identity() -> Tuple[Optional[str], float]:
    return _ACTIVE_CACHE_PKG_PATH, _ACTIVE_CACHE_MTIME


def open_active_asset_cache(prefs=None) -> Optional[Any]:
    """Opens active AssetCache reader from .mtkcache package, reusing open instance when valid."""
    global _ACTIVE_ASSET_CACHE, _ACTIVE_CACHE_PKG_PATH, _ACTIVE_CACHE_MTIME, _ACTIVE_CACHE_PACK_SIG, _ACTIVE_CACHE_MANIFEST
    mtk = get_libmtk()
    if mtk is None:
        return None

    cache_dir = get_cache_dir(prefs).resolve()
    if not cache_dir.exists():
        if _ACTIVE_ASSET_CACHE is not None or _ACTIVE_CACHE_PKG_PATH is not None:
            invalidate_asset_cache_memory()
        return None

    pack_sig = _cheap_pack_paths_sig(prefs)

    # O(1) Fast-path: return currently cached instance only if pack stack, cache_dir, path & mtime match
    if (
        _ACTIVE_ASSET_CACHE is not None
        and _ACTIVE_CACHE_PKG_PATH is not None
        and _ACTIVE_CACHE_PACK_SIG == pack_sig
    ):
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
        _ACTIVE_CACHE_PACK_SIG = pack_sig
        return _ACTIVE_ASSET_CACHE

    try:
        _ACTIVE_ASSET_CACHE = mtk.AssetCache.open(pkg_str)
        _ACTIVE_CACHE_PKG_PATH = pkg_str
        _ACTIVE_CACHE_MTIME = mtime
        _ACTIVE_CACHE_PACK_SIG = pack_sig
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
        _get_active_cache_identity,
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


def load_baked_atlas_from_cache(prefs=None) -> Optional[Any]:
    """Loads precompiled BakedAtlas from cache package into memory, reusing cached instance."""
    global _CACHED_ATLAS, _CACHED_ATLAS_PKG_PATH, _CACHED_ATLAS_MTIME
    atlas, pkg, mtime = load_baked_atlas_inner(
        open_active_asset_cache,
        _get_active_cache_identity,
        _CACHED_ATLAS,
        _CACHED_ATLAS_PKG_PATH,
        _CACHED_ATLAS_MTIME,
        prefs,
    )
    _CACHED_ATLAS = atlas
    _CACHED_ATLAS_PKG_PATH = pkg
    _CACHED_ATLAS_MTIME = mtime
    return atlas


def load_biome_resolver_from_cache(prefs=None) -> Optional[Any]:
    """Loads precompiled BiomeResolver from cache package into memory, reusing cached instance."""
    global _CACHED_BIOME_RESOLVER, _CACHED_BIOME_RESOLVER_PKG_PATH, _CACHED_BIOME_RESOLVER_MTIME
    res, pkg, mtime = load_biome_resolver_inner(
        open_active_asset_cache,
        _get_active_cache_identity,
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
        _get_active_cache_identity,
        _CACHED_COLORMAPS,
        _CACHED_COLORMAPS_PKG_PATH,
        _CACHED_COLORMAPS_MTIME,
        prefs,
    )
    _CACHED_COLORMAPS = cms
    _CACHED_COLORMAPS_PKG_PATH = pkg
    _CACHED_COLORMAPS_MTIME = mtime
    return cms


def load_ctm_solver_from_cache(prefs=None) -> Optional[Any]:
    """Loads precompiled CtmSolver from active cache package into memory, reusing cached instance."""
    global _CACHED_CTM_SOLVER, _CACHED_CTM_SOLVER_PKG_PATH, _CACHED_CTM_SOLVER_MTIME
    solver, pkg, mtime = load_ctm_solver_inner(
        open_active_asset_cache,
        _get_active_cache_identity,
        _CACHED_CTM_SOLVER,
        _CACHED_CTM_SOLVER_PKG_PATH,
        _CACHED_CTM_SOLVER_MTIME,
        prefs,
    )
    _CACHED_CTM_SOLVER = solver
    _CACHED_CTM_SOLVER_PKG_PATH = pkg
    _CACHED_CTM_SOLVER_MTIME = mtime
    return solver


def _newest_cache_package(prefs=None) -> Optional[Path]:
    """Newest `.mtkcache` in the cache directory, for introspection only.

    Serving geometry goes through `get_active_cache_package_path`, which validates the stack
    fingerprint. The panel still needs to read the manifest of whatever package is on disk so it
    can show "this cache was built from different packs".
    """
    cache_dir = get_cache_dir(prefs).resolve()
    if not cache_dir.is_dir():
        return None
    mtk = get_libmtk()
    if mtk is None:
        return None
    resolved = mtk.newest_cache_package(str(cache_dir))
    return Path(resolved) if resolved else None


def get_cache_manifest(prefs=None) -> Optional[Dict[str, Any]]:
    """Reads the manifest of the newest `.mtkcache` on disk (introspection, never serving)."""
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

    pkg = _newest_cache_package(prefs)
    mtk = get_libmtk()
    if pkg is not None and mtk is not None:
        try:
            reader = mtk.AssetCache.open(str(pkg))
            manifest = reader.get_manifest()
            if manifest is not None:
                _ACTIVE_CACHE_MANIFEST = manifest
                return manifest
        except Exception:
            pass
    return None


def _has_enabled_pack_entries(prefs=None) -> bool:
    target = _resolve_prefs(prefs)
    entries = getattr(target, "resource_packs", None) or []
    return any(
        getattr(e, "enabled", True) and bool(str(getattr(e, "path", "") or "").strip())
        for e in entries
    )


def get_cache_fingerprint(prefs=None) -> Optional[str]:
    """Returns active stack fingerprint recorded in matching package or cache manifest."""
    cache = open_active_asset_cache(prefs)
    if cache is not None:
        fp = cache.fingerprint
        if fp:
            return str(fp)
    # When packs are configured in prefs but no matching .mtkcache exists on disk,
    # do NOT fall back to an unrelated package's manifest; returning None ensures
    # callers recognize the cache as missing/stale for the active stack.
    if _has_enabled_pack_entries(prefs) and get_configured_stack_fingerprint(prefs):
        return None
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

    pkg = _newest_cache_package(prefs)
    if pkg is not None and pkg.exists():
        try:
            return float(pkg.stat().st_mtime)
        except Exception:
            pass

    return 0.0


def evaluate_cache_status(
    cache_dir: str,
    pack_count: int,
    has_vanilla: bool,
    expected_fp: str,
) -> Optional[str]:
    """Evaluate asset cache readiness natively via libmtk_py (returns None when native core is unloaded)."""
    mtk = get_libmtk()
    if mtk is None:
        return None
    return mtk.evaluate_cache_status(str(cache_dir), pack_count, has_vanilla, expected_fp)


def check_cache_dirty(
    cached_fingerprint: Optional[str] = None,
    cached_timestamp: float = 0.0,
    prefs=None,
) -> Tuple[bool, Optional[str], float]:
    """Compares cached fingerprint and timestamp with current on-disk asset cache package."""
    if _has_enabled_pack_entries(prefs):
        expected_fp = get_configured_stack_fingerprint(prefs)
        if expected_fp and open_active_asset_cache(prefs) is None:
            invalidate_asset_cache_memory()
            return True, None, 0.0

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
    "load_baked_atlas_from_cache",
    "load_biome_resolver_from_cache",
    "load_colormaps_from_cache",
    "load_ctm_solver_from_cache",
    "get_cache_manifest",
    "get_cache_fingerprint",
    "get_cache_timestamp",
    "check_cache_dirty",
    "evaluate_cache_status",
    "get_cache_dir",
    "register_cache_invalidation_callback",
    "unregister_cache_invalidation_callback",
]
