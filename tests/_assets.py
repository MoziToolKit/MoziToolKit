"""
Shared test-asset resolver for the MoziToolKit test suite (mirrors `mtk-testkit`).

Real Minecraft resource packs / saves are large and licensed, so they are never committed.
This module resolves them at test time using a deterministic priority chain:

1. Environment variable overrides (set by CI / local dev).
2. Conventional paths adjacent to the workspace (`../mc`, `../26.2-Fabric.jar`, ...).

Tests that require real assets must call `require_assets()` / `require_jar()` and skip
gracefully when the asset is unavailable, so the suite stays green on a fresh clone and
inside CI regardless of whether the real packs were downloaded.
"""

from __future__ import annotations

import contextlib
import os
from pathlib import Path
from typing import Optional
from unittest import mock


# tests/_assets.py -> parents[0] = tests/, parents[1] = MoziToolKit/, parents[2] = <mtk>/
_WORKSPACE_PARENT = Path(__file__).resolve().parents[2]

_ASSET_ENV_VARS = ("MTK_TEST_ASSETS", "MC_ASSETS_DIR", "MC_DIR")
_JAR_ENV_VARS = ("MTK_TEST_JAR", "MC_JAR")
_MOD_ENV_VARS = ("MTK_TEST_MOD_DIR", "MTK_MOD_SAMPLES_DIR")


def _is_asset_root(path: Path) -> bool:
    return (path / "assets" / "minecraft").is_dir()


def _require_real_assets() -> bool:
    """True when a missing asset must fail the suite instead of skipping it."""
    return os.environ.get("MTK_REQUIRE_REAL_ASSETS", "").strip().lower() in {"1", "true", "yes"}


def _skip(reason: str) -> None:
    """Skip `reason` loudly, or fail when `MTK_REQUIRE_REAL_ASSETS` is set."""
    if _require_real_assets():
        raise AssertionError(
            f"[assets] required assets are unavailable: {reason}. "
            "Set MTK_TEST_ASSETS (or MTK_REQUIRE_REAL_ASSETS=0 to allow skipping)."
        )
    import pytest

    pytest.skip(reason)


def assets_root() -> Optional[Path]:
    """Root directory that contains `assets/minecraft`, or `None` if unavailable."""
    for key in _ASSET_ENV_VARS:
        value = os.environ.get(key)
        if value and _is_asset_root(Path(value)):
            return Path(value)

    candidates = [
        _WORKSPACE_PARENT / "mc",
        _WORKSPACE_PARENT / "Minecraft_Source",
        _WORKSPACE_PARENT / "Minecraft_Unpacked",
        _WORKSPACE_PARENT / "libmozitoolkit" / ".." / "mc",
        _WORKSPACE_PARENT / "libmozitoolkit" / ".." / "Minecraft_Source",
        _WORKSPACE_PARENT / "libmozitoolkit" / ".." / "Minecraft_Unpacked",
    ]
    for candidate in candidates:
        if _is_asset_root(candidate):
            return candidate.resolve()
    return None


def fabric_jar() -> Optional[Path]:
    """Minecraft client / modpack jar used for pack-level baking tests."""
    for key in _JAR_ENV_VARS:
        value = os.environ.get(key)
        if value and Path(value).exists():
            return Path(value)

    candidates = [
        _WORKSPACE_PARENT / "26.2-Fabric.jar",
        Path.home() / "26.2-Fabric.jar",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def resource_pack_zip() -> Optional[Path]:
    """External resource pack `.zip` (SPBR / Continuity / PBR companions)."""
    value = os.environ.get("MTK_TEST_RESOURCE_PACK") or os.environ.get("MTK_TEST_RESOURCEPACK")
    if value and Path(value).exists():
        return Path(value)

    candidates = [
        _WORKSPACE_PARENT / "SPBR-21.zip",
        Path.home() / "Downloads" / "SPBR-21.zip",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def ctm_resource_pack_zip() -> Optional[Path]:
    """CTM resource pack `.zip` (e.g. clean-connected-glass, SPBR, or custom CTM pack)."""
    value = os.environ.get("MTK_TEST_CTM_PACK")
    if value and Path(value).exists():
        return Path(value)

    candidates = [
        _WORKSPACE_PARENT / "clean_connected_glass.zip",
        _WORKSPACE_PARENT / "clean-connected-glass.zip",
        Path.home() / "Downloads" / "clean_connected_glass.zip",
        _WORKSPACE_PARENT / "SPBR-21.zip",
        Path.home() / "Downloads" / "SPBR-21.zip",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate

    rp = resource_pack_zip()
    if rp is not None:
        return rp

    return None


def save_world() -> Optional[Path]:
    """A Minecraft save directory containing `level.dat`."""
    value = os.environ.get("MTK_TEST_SAVE")
    if value and Path(value).exists():
        return Path(value)

    candidates = [
        _WORKSPACE_PARENT / "saves" / "test_world",
        _WORKSPACE_PARENT / "saves" / "mtk-save-world",
        _WORKSPACE_PARENT / "mtk-save-world",
        Path.home() / "saves" / "test_world",
    ]
    for candidate in candidates:
        if candidate.exists() and (candidate / "level.dat").exists():
            return candidate.resolve()
    return None


def blender_datafiles_cache() -> Optional[Path]:
    """Resolves the Blender asset cache root cross-platform (contains `models/`, `atlas/`, ...).

    Priority:
      1. `MTK_TEST_CACHE` / `MOZI_CACHE_DIR` sandbox overrides (used by CI / local tests).
      2. Production `bridge.assets.get_cache_dir()` (honours `MOZI_CACHE_DIR`, user prefs,
         `bpy.utils.user_resource("DATAFILES")`, then a `~/.config/blender/...` fallback).
      3. Repository-adjacent `cache/` directories.
    """
    value = os.environ.get("MTK_TEST_CACHE") or os.environ.get("MOZI_CACHE_DIR")
    if value:
        path = Path(value)
        return path if path.exists() else None

    try:
        from MoziToolKit.bridge.assets import get_cache_dir

        return get_cache_dir()
    except Exception:
        pass

    candidates = [
        _WORKSPACE_PARENT / "cache",
        _WORKSPACE_PARENT / "MoziToolKit" / "cache",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def require_assets() -> Path:
    """Returns the asset root or skips the enclosing test.

    The resolved path is exported to `MC_DIR` / `MC_ASSETS_DIR` so that production bridge
    code (`bridge/debug.py` etc.) locates the same assets the test suite found.
    """
    root = assets_root()
    if root is None:
        _skip(
            "Minecraft assets unavailable: set MTK_TEST_ASSETS / MC_DIR or place an "
            "unpacked jar at ../mc, ../Minecraft_Source or ../Minecraft_Unpacked"
        )
    os.environ["MC_DIR"] = str(root)
    os.environ["MC_ASSETS_DIR"] = str(root)
    os.environ["MTK_TEST_ASSETS"] = str(root)
    return root


def mod_samples_dir() -> Optional[Path]:
    """Directory containing sample mod `.jar` archives (e.g. `mod-samples`)."""
    def _has_jars(path: Path) -> bool:
        return path.is_dir() and any(path.glob("*.jar"))

    for key in _MOD_ENV_VARS:
        value = os.environ.get(key)
        if value and _has_jars(Path(value)):
            return Path(value)

    candidates = [
        _WORKSPACE_PARENT / "mod-samples",
        Path.home() / "mod-samples",
    ]
    for candidate in candidates:
        if _has_jars(candidate):
            return candidate.resolve()
    return None


def require_mod_samples() -> Path:
    """Returns the mod-samples directory or skips the enclosing test."""
    mod_dir = mod_samples_dir()
    if mod_dir is None:
        _skip(
            "Sample mod JARs unavailable: set MTK_TEST_MOD_DIR or place archives under ../mod-samples"
        )
    os.environ["MTK_TEST_MOD_DIR"] = str(mod_dir)
    return mod_dir


def require_jar() -> Path:
    """Returns the Minecraft jar path or skips the enclosing test."""
    jar = fabric_jar()
    if jar is None:
        _skip("Minecraft jar unavailable: set MTK_TEST_JAR or place ../26.2-Fabric.jar")
    os.environ["MTK_TEST_JAR"] = str(jar)
    return jar


def require_ctm_resource_pack() -> Path:
    """Returns the CTM resource pack path or skips the enclosing test if unavailable."""
    pack = ctm_resource_pack_zip()
    if pack is None or not pack.exists():
        _skip(
            "CTM resource pack unavailable: set MTK_TEST_CTM_PACK or place a pack zip "
            "next to the workspace"
        )
    os.environ["MTK_TEST_CTM_PACK"] = str(pack)
    return pack


@contextlib.contextmanager
def activated_cache_package(path):
    """Make a synthetic `.mtkcache` the active asset cache for the duration of the block.

    Production resolution only accepts a package whose manifest fingerprint matches the
    configured pack stack, so a fabricated package can never be picked up by accident. Tests that
    build one declare it explicitly here instead of relying on a "newest file on disk" fallback.
    """
    from MoziToolKit.bridge.assets import cache as cache_mod

    resolved = Path(path).resolve()
    with mock.patch.object(
        cache_mod, "get_active_cache_package_path", return_value=resolved
    ):
        cache_mod.invalidate_asset_cache_memory()
        try:
            yield resolved
        finally:
            cache_mod.invalidate_asset_cache_memory()


_CACHE_READY = False


def ensure_test_cache() -> None:
    """Precompiles the test stack once when the cache directory has no package for it.

    Cache resolution validates the stack fingerprint, so a package baked from different packs (or
    from an older asset-cache format) is rejected. Tests therefore bake their own cache instead of
    borrowing whatever happens to sit in the cache directory.
    """
    global _CACHE_READY
    if _CACHE_READY:
        return

    from MoziToolKit.bridge.assets import invalidate_asset_cache_memory, precompile_stack
    from MoziToolKit.utils.assets.guard import check_asset_cache_status

    status = check_asset_cache_status(None)
    if status != "READY":
        precompile_stack(None)
        invalidate_asset_cache_memory()
    _CACHE_READY = True
