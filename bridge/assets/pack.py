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


def get_configured_pack_stack(prefs=None) -> Optional[Any]:
    """
    Builds a libmtk ResourcePackStack from the Blender preferences configuration.
    Traverses enabled entries in order and pushes them to Rust.
    Reuses cached stack instance if configuration paths and mtimes are unchanged.
    """
    global _CACHED_PACK_STACK, _CACHED_STACK_SIGNATURE
    mtk = get_libmtk()
    if mtk is None:
        return None

    if prefs is None:
        prefs = get_prefs()

    # Collect active entries to compute a lightweight mtime-based signature
    paths_to_check = []
    single_path = getattr(prefs, "resource_pack_path", None) if prefs else None
    if single_path and Path(single_path).exists():
        paths_to_check.append(str(Path(single_path).resolve()))

    if prefs and hasattr(prefs, "resource_packs"):
        for entry in prefs.resource_packs:
            if getattr(entry, "enabled", True):
                raw_path = getattr(entry, "path", "").strip()
                if raw_path and Path(raw_path).exists():
                    paths_to_check.append(str(Path(raw_path).resolve()))

    env_fallback_path = None
    if not paths_to_check:
        for env_var in ("MTK_TEST_JAR", "MC_JAR", "MC_DIR", "MC_ASSETS_DIR", "MTK_TEST_ASSETS"):
            val = os.environ.get(env_var)
            if val and Path(val).exists():
                env_fallback_path = str(Path(val).resolve())
                paths_to_check.append(env_fallback_path)
                break

    sig = tuple((p, os.path.getmtime(p) if os.path.exists(p) else 0.0) for p in paths_to_check)
    if _CACHED_PACK_STACK is not None and _CACHED_STACK_SIGNATURE == sig:
        return _CACHED_PACK_STACK

    stack = mtk.ResourcePackStack()

    if not paths_to_check:
        _CACHED_PACK_STACK = stack
        _CACHED_STACK_SIGNATURE = sig
        return stack

    # 1. Single direct pack path if present
    if single_path and Path(single_path).exists():
        p = Path(single_path)
        if p.is_dir():
            stack.add_directory_pack(str(p.resolve()), p.stem)
        elif p.is_file() and p.suffix.lower() in {".zip", ".jar"}:
            stack.add_zip_pack(str(p.resolve()))

    # 2. Resource pack list entries
    if hasattr(prefs, "resource_packs"):
        for entry in prefs.resource_packs:
            if not getattr(entry, "enabled", True):
                continue

            raw_path = getattr(entry, "path", "").strip()
            if not raw_path:
                continue

            p = Path(raw_path)
            if not p.exists():
                continue

            name = getattr(entry, "name", None) or p.stem

            if p.is_dir():
                stack.add_directory_pack(str(p.resolve()), name)
            elif p.is_file() and p.suffix.lower() in {".zip", ".jar"}:
                stack.add_zip_pack(str(p.resolve()))

    # 3. Fallback from environment if no preference packs configured
    if env_fallback_path and not stack.get_pack_count():
        p = Path(env_fallback_path)
        if p.is_dir():
            stack.add_directory_pack(str(p.resolve()), p.stem)
        elif p.is_file() and p.suffix.lower() in {".zip", ".jar"}:
            stack.add_zip_pack(str(p.resolve()))

    _CACHED_PACK_STACK = stack
    _CACHED_STACK_SIGNATURE = sig
    return stack


def get_configured_ctm_solver(prefs=None, force: bool = False) -> Optional[Any]:
    """
    Builds a CtmSolver from the configured resource pack stack if any CTM rules exist.
    Memoized against the active pack stack signature.
    """
    global _CACHED_CTM_SOLVER, _CACHED_CTM_SIGNATURE
    if not force and prefs is not None and not getattr(prefs, "enable_ctm", True):
        return None
    stack = get_configured_pack_stack(prefs)
    if stack is None:
        return None

    ctm_sig = (_CACHED_STACK_SIGNATURE, True)
    if _CACHED_CTM_SOLVER is not None and _CACHED_CTM_SIGNATURE == ctm_sig:
        return _CACHED_CTM_SOLVER

    try:
        if stack.get_ctm_rule_count() == 0:
            _CACHED_CTM_SOLVER = None
            _CACHED_CTM_SIGNATURE = ctm_sig
            return None
        solver = stack.build_ctm_solver()
        _CACHED_CTM_SOLVER = solver
        _CACHED_CTM_SIGNATURE = ctm_sig
        return solver
    except Exception as e:
        logger.warning("Failed building CTM solver: %s", e, exc_info=True)
    return None


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
    single_path = getattr(prefs, "resource_pack_path", None)
    return isinstance(single_path, str) and bool(single_path.strip())


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
    single = getattr(prefs, "resource_pack_path", "") or ""
    single_mtime = 0.0
    if single:
        try:
            single_mtime = os.path.getmtime(single)
        except OSError:
            single_mtime = 0.0
    env_sig = tuple((k, os.environ.get(k, "")) for k in ENV_ASSET_ROOTS + ENV_ASSET_ARCHIVES)
    return (tuple(entries), single, single_mtime, env_sig)


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

        if not result:
            single_path = getattr(prefs, "resource_pack_path", None)
            if isinstance(single_path, str) and single_path.strip():
                if path_provides_vanilla_client(Path(single_path.strip())):
                    result = True

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
    "get_configured_pack_stack",
    "get_configured_ctm_solver",
    "invalidate_pack_stack_cache",
    "has_valid_vanilla_pack",
    "path_provides_vanilla_client",
    "ENV_ASSET_ROOTS",
    "ENV_ASSET_ARCHIVES",
    "_VANILLA_PROBE_CACHE",
    "_VANILLA_CHECK_CACHE",
]
