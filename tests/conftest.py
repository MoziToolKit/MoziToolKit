"""
Pytest configuration for the MoziToolKit test suite.

STRICT INVARIANT (GEMINI.md Rule 9):
All tests MUST execute inside a real Blender 5.x LTS host environment.
MagicMock and headless mock bpy/bmesh/mathutils environments are strictly forbidden.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
import pytest

_SANDBOX_CONFIG_DIR = Path(tempfile.gettempdir()) / "mozi_test_sandbox_config"
_SANDBOX_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("MOZI_CONFIG_DIR", str(_SANDBOX_CONFIG_DIR))

# 1. Enforce real Blender host environment gate
try:
    import bpy
    import bmesh
    import mathutils

    if not hasattr(bpy, "data") or not hasattr(bpy.data, "meshes") or type(bpy).__name__ == "MagicMock":
        raise ImportError("bpy.data is not accessible or is mocked")
except (ImportError, AttributeError) as exc:
    raise RuntimeError(
        "\n===============================================================================\n"
        "[FATAL] MoziToolKit tests must be run inside the real Blender host environment!\n"
        "Standalone python / mock execution is strictly forbidden (see GEMINI.md Rule 9).\n\n"
        "Run the test suite using:\n"
        "  MTK_TEST_SITE_PACKAGES=\"$(uv run python -c 'import site; print(site.getsitepackages()[0])')\" \\\n"
        "    blender --background --python tests/run_tests.py -- -q\n"
        "==============================================================================="
    ) from exc

# 2. Setup project paths and dev direct-link bindings
PROJECT_DIR = Path(__file__).parent.parent.resolve()
TESTS_DIR = Path(__file__).parent.resolve()
PARENT_DIR = PROJECT_DIR.parent
site_pkgs = PROJECT_DIR / "site-packages"
dev_lib = PROJECT_DIR / "dev" / "lib"

for _path in (PROJECT_DIR, TESTS_DIR, PARENT_DIR, site_pkgs, dev_lib):
    if _path.exists() and str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

# Dev native module loader
try:
    from MoziToolKit.dev import loader as _dev_loader
    _dev_loader.setup_dev_environment()
except Exception:
    pass

try:
    from MoziToolKit.bridge.engine import get_libmtk
    get_libmtk()
except Exception:
    pass

# Ensure addon properties and operators are registered in Blender test runtime
try:
    import MoziToolKit
    MoziToolKit.register()
    from MoziToolKit.ui.preferences import MOZI_AddonPreferences
    _addons = getattr(getattr(bpy.context, "preferences", None), "addons", None)
    if _addons is not None and MOZI_AddonPreferences.bl_idname not in _addons:
        _addon_entry = _addons.new()
        _addon_entry.module = MOZI_AddonPreferences.bl_idname
    _system = getattr(getattr(bpy.context, "preferences", None), "system", None)
    if _system is not None and hasattr(_system, "use_online_access"):
        _system.use_online_access = True
    from MoziToolKit.bridge.prefs import get_prefs
    _prefs = get_prefs()
    if _prefs is not None:
        _prefs.resource_packs.clear()
        _prefs.cache_dir = ""
        _prefs.enable_dna_acceleration = True
        _prefs.enable_ctm = True
        _prefs.enable_animated_textures = True
        _prefs.smooth_biome_blend = True
        _prefs.unknown_block_strategy = "FALLBACK_CUBE"
    from MoziToolKit.bridge.assets import invalidate_asset_cache_memory
    invalidate_asset_cache_memory()
except Exception:
    pass


@pytest.fixture(autouse=True)
def clean_blender_state():
    """
    Ensure each test starts and ends with a clean Blender state.
    Purges temporary objects, meshes, and materials created during testing.
    """
    yield
    # Cleanup any orphan test meshes and objects
    try:
        if hasattr(bpy.data, "objects"):
            for obj in list(bpy.data.objects):
                if obj.name.startswith(("test_", "Test_", "Yefira_", "MTK_", "Mesh_", "Cloud_")):
                    bpy.data.objects.remove(obj, do_unlink=True)
    except Exception:
        pass
