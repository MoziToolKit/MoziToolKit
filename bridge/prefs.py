"""
MoziToolKit Bridge AddonPreferences Accessor.

Provides a self-contained, host-safe accessor for Blender AddonPreferences
without importing from DCC utility layers (utils/).
Strictly adheres to GEMINI.md Rule 2 and PROJECT.md unidirectional hierarchy.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("MoziToolKit.Bridge.Prefs")


ROOT_PACKAGE = (__package__ or "MoziToolKit").rpartition(".")[0] or "MoziToolKit"


def get_root_package() -> str:
    """Return the active top-level add-on/extension package name."""
    return ROOT_PACKAGE


def resolve_extension_module(subpath: str) -> Optional[Any]:
    """Look up a loaded submodule under the active extension root package."""
    for mod_name in dict.fromkeys((f"{ROOT_PACKAGE}.{subpath}", f"MoziToolKit.{subpath}")):
        mod = sys.modules.get(mod_name)
        if mod is not None:
            return mod
    return None


def get_extension_user_dir(subdir: str = "", legacy_resource: str = "CONFIG") -> Path:
    """Resolve the extension's persistent user directory via Blender's extension API.

    Prefers ``bpy.utils.extension_path_user`` when running as a Blender 4.2+
    extension, falling back to ``bpy.utils.user_resource`` in development mode.
    """
    base_dir: Optional[Path] = None
    if "bpy" in sys.modules:
        import bpy

        utils_mod = getattr(bpy, "utils", None)
        if utils_mod is not None:
            ext_path_fn = getattr(utils_mod, "extension_path_user", None)
            if callable(ext_path_fn):
                try:
                    base_dir = Path(ext_path_fn(ROOT_PACKAGE, path=subdir, create=True))
                except Exception:
                    base_dir = None

            if base_dir is None:
                user_res_fn = getattr(utils_mod, "user_resource", None)
                if callable(user_res_fn):
                    try:
                        root = Path(user_res_fn(legacy_resource)) / "MoziToolKit"
                        base_dir = root / subdir if subdir else root
                    except Exception:
                        base_dir = None

    if base_dir is None:
        root = Path.home() / ".config" / "blender" / "MoziToolKit"
        base_dir = root / subdir if subdir else root

    base_dir.mkdir(parents=True, exist_ok=True)
    return base_dir


def get_prefs(context: Optional[Any] = None) -> Optional[Any]:
    """
    Retrieve MoziToolKit AddonPreferences safely across legacy add-on
    and Blender 4.2+ extensions packaging environments.
    Returns None if running outside Blender, headless, or before registration.
    """
    if "bpy" not in sys.modules:
        return None

    import bpy

    if context is None:
        context = getattr(bpy, "context", None)

    if not hasattr(context, "preferences") or not context.preferences:
        return None

    addons = getattr(context.preferences, "addons", None)
    if addons is None:
        return None

    # 1. Direct lookup by active extension root package
    addon = addons.get(ROOT_PACKAGE)
    if addon and getattr(addon, "preferences", None) is not None:
        return addon.preferences

    # 2. Type-based resolution (works regardless of extension namespace prefix)
    pref_cls = getattr(bpy.types, "MOZI_AddonPreferences", None)
    for addon in addons.values():
        pref = getattr(addon, "preferences", None)
        if pref is not None:
            if (pref_cls and isinstance(pref, pref_cls)) or type(pref).__name__ == "MOZI_AddonPreferences":
                return pref
            if hasattr(pref, "resource_packs") or hasattr(pref, "enable_dna_acceleration"):
                return pref

    return None


__all__ = [
    "ROOT_PACKAGE",
    "get_extension_user_dir",
    "get_prefs",
    "get_root_package",
    "resolve_extension_module",
]
