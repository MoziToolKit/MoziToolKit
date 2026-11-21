"""
Resource Pack Stack Configuration Bridge.
Extracts enabled resource packs and Fabric/Vanilla JARs from Blender preferences
and configures the libmtk native ResourcePackStack.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from ..engine import get_libmtk
from ..prefs import get_prefs

logger = logging.getLogger("MoziToolKit.Bridge.Assets.Pack")

#: Host-level asset roots / archives honoured when no preferences are registered (CLI, CI).
ENV_ASSET_ROOTS = ("MC_DIR", "MC_ASSETS_DIR", "MTK_TEST_ASSETS")
ENV_ASSET_ARCHIVES = ("MTK_TEST_JAR", "MC_JAR")

_CACHED_PACK_STACK: Optional[Any] = None
_CACHED_STACK_SIGNATURE: Optional[tuple] = None
_CACHED_CTM_SOLVER: Optional[Any] = None
_CACHED_CTM_SIGNATURE: Optional[tuple] = None
_VANILLA_PROBE_CACHE: Dict[Tuple[str, int, int], bool] = {}
_VANILLA_CHECK_CACHE: Dict[Any, bool] = {}


def invalidate_pack_stack_cache() -> None:
    """Invalidates memoized ResourcePackStack, CtmSolver, and vanilla check caches."""
    global _CACHED_PACK_STACK, _CACHED_STACK_SIGNATURE, _CACHED_CTM_SOLVER, _CACHED_CTM_SIGNATURE
    _CACHED_PACK_STACK = None
    _CACHED_STACK_SIGNATURE = None
    _CACHED_CTM_SOLVER = None
    _CACHED_CTM_SIGNATURE = None
    _VANILLA_PROBE_CACHE.clear()
    _VANILLA_CHECK_CACHE.clear()


def get_configured_pack_paths(prefs=None) -> List[str]:
    """
    Ordered absolute paths of the packs the configured stack loads.

    This is the single source of ordering: the fingerprint and the loaded stack must agree
    entry for entry, otherwise the cache resolves to a package built from different packs.
    Does NOT open or decompress anything.
    """
    if prefs is None:
        prefs = get_prefs()

    paths: List[str] = []
    if prefs and hasattr(prefs, "resource_packs"):
        for entry in prefs.resource_packs:
            if not getattr(entry, "enabled", True):
                continue
            raw_path = getattr(entry, "path", "").strip()
            if not raw_path:
                continue
            p = Path(raw_path)
            if not p.exists():
                continue
            if p.is_dir() or (p.is_file() and p.suffix.lower() in {".zip", ".jar"}):
                paths.append(str(p.resolve()))

    if not paths:
        for env_var in ("MTK_TEST_JAR", "MC_JAR", "MC_DIR", "MC_ASSETS_DIR", "MTK_TEST_ASSETS"):
            val = os.environ.get(env_var)
            if val and Path(val).exists():
                paths.append(str(Path(val).resolve()))
                break

    return paths


def get_configured_stack_fingerprint(prefs=None) -> str:
    """
    Content-addressed fingerprint of the configured pack stack.

    Hashes each pack's path plus its file sizes and mtimes (directories are walked), so editing a
    texture in place invalidates the cache. Still no archive is opened or decompressed.
    """
    paths = get_configured_pack_paths(prefs)
    mtk = get_libmtk()
    if mtk is None:
        return ""
    return mtk.compute_stack_fingerprint_from_paths(paths)


def get_configured_pack_stack(prefs=None) -> Optional[Any]:
    """
    Builds a libmtk ResourcePackStack from the configured pack paths.

    Reuses the cached stack while the configuration and its file mtimes are unchanged.
    """
    global _CACHED_PACK_STACK, _CACHED_STACK_SIGNATURE
    mtk = get_libmtk()
    if mtk is None:
        return None

    paths = get_configured_pack_paths(prefs)
    stack = mtk.ResourcePackStack()
    if not paths:
        _CACHED_PACK_STACK = stack
        _CACHED_STACK_SIGNATURE = ()
        return stack

    sig = tuple(
        (path, os.path.getmtime(path) if os.path.exists(path) else 0.0)
        for path in paths
    )
    if _CACHED_PACK_STACK is not None and _CACHED_STACK_SIGNATURE == sig:
        return _CACHED_PACK_STACK

    for path in paths:
        p = Path(path)
        if p.is_dir():
            stack.add_directory_pack(path, p.stem)
        elif p.is_file() and p.suffix.lower() in {".zip", ".jar"}:
            stack.add_zip_pack(path)

    _CACHED_PACK_STACK = stack
    _CACHED_STACK_SIGNATURE = sig
    return stack


def get_configured_ctm_solver(prefs=None, force: bool = False) -> Optional[Any]:
    """
    Retrieves the CtmSolver loaded from the active .mtkcache package.
    Runtime strictly reads precompiled CTM rules from the asset cache (SSOT).
    Returns None if cache is missing, invalid, or has no CTM rules.
    """
    global _CACHED_CTM_SOLVER
    if not force and prefs is not None and not getattr(prefs, "enable_ctm", True):
        return None

    try:
        from .cache import load_ctm_solver_from_cache, open_active_asset_cache
        active_cache = open_active_asset_cache(prefs)
        if active_cache is not None:
            return load_ctm_solver_from_cache(prefs)
    except Exception as e:
        logger.warning("Failed loading CTM solver from cache package: %s", e)

    return None


def probe_pack_native(path: str) -> Dict[str, Any]:
    """Classify a resource pack archive or directory using libmtk.probe_pack."""
    mtk = get_libmtk()
    if mtk is None:
        raise RuntimeError("libmtk native core is required for pack detection")
    return dict(mtk.probe_pack(str(path)))


def path_provides_vanilla_client(path: Path) -> bool:
    """Verifies that a path or ZIP/JAR provides core Minecraft vanilla client assets via libmtk_py."""
    mtk = get_libmtk()
    if mtk is None:
        raise RuntimeError("libmtk native core is required for vanilla client validation")
    try:
        if not path.exists():
            return False
        stat = path.stat()
        key = (str(path.resolve()), stat.st_mtime_ns, stat.st_size)
    except OSError:
        return False

    if key in _VANILLA_PROBE_CACHE:
        return _VANILLA_PROBE_CACHE[key]

    res = mtk.path_provides_vanilla_client(str(path.resolve()))
    _VANILLA_PROBE_CACHE[key] = res
    return res


def _is_entry_valid_vanilla_client(entry: Any) -> bool:
    if not getattr(entry, "enabled", True):
        return False
    raw_path = getattr(entry, "path", "")
    if not raw_path or not isinstance(raw_path, str):
        return False
    return path_provides_vanilla_client(Path(raw_path.strip()))


def _has_configured_packs(prefs: Any) -> bool:
    for entry in getattr(prefs, "resource_packs", None) or []:
        raw_path = getattr(entry, "path", "")
        if isinstance(raw_path, str) and raw_path.strip():
            return True
    return False


def _get_pack_signature(prefs=None) -> tuple:
    if prefs is None:
        prefs = get_prefs()
    packs = getattr(prefs, "resource_packs", None) or []
    entries = []
    for e in packs:
        raw = getattr(e, "path", "") or ""
        enabled = getattr(e, "enabled", True)
        pt = getattr(e, "pack_type", "")
        mtime = 0.0
        if raw:
            try:
                mtime = os.path.getmtime(raw)
            except OSError:
                mtime = 0.0
        entries.append((raw, enabled, pt, mtime))
    env_sig = tuple((k, os.environ.get(k, "")) for k in ENV_ASSET_ROOTS + ENV_ASSET_ARCHIVES)
    return (tuple(entries), env_sig)


def has_valid_vanilla_pack(prefs=None) -> bool:
    """Checks whether the active configuration or environment can provide Minecraft vanilla client assets."""
    sig = _get_pack_signature(prefs)
    if sig in _VANILLA_CHECK_CACHE:
        return _VANILLA_CHECK_CACHE[sig]

    if prefs is None:
        prefs = get_prefs()

    result = False
    if prefs is not None:
        for entry in getattr(prefs, "resource_packs", None) or []:
            if _is_entry_valid_vanilla_client(entry):
                result = True
                break

        if not result and _has_configured_packs(prefs):
            _VANILLA_CHECK_CACHE[sig] = False
            return False

    if not result:
        for env_var in ENV_ASSET_ROOTS + ENV_ASSET_ARCHIVES:
            val = os.environ.get(env_var)
            if val and path_provides_vanilla_client(Path(val)):
                result = True
                break

    _VANILLA_CHECK_CACHE[sig] = result
    return result


__all__ = [
    "get_configured_pack_paths",
    "get_configured_stack_fingerprint",
    "get_configured_pack_stack",
    "get_configured_ctm_solver",
    "invalidate_pack_stack_cache",
    "has_valid_vanilla_pack",
    "path_provides_vanilla_client",
    "probe_pack_native",
    "ENV_ASSET_ROOTS",
    "ENV_ASSET_ARCHIVES",
    "_VANILLA_PROBE_CACHE",
    "_VANILLA_CHECK_CACHE",
]
