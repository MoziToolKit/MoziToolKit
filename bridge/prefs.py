"""
MoziToolKit Bridge AddonPreferences Accessor.

Provides a self-contained, host-safe accessor for Blender AddonPreferences
without importing from DCC utility layers (utils/).
Strictly adheres to GEMINI.md Rule 2 and PROJECT.md unidirectional hierarchy.
"""

from __future__ import annotations

import logging
import sys
from typing import Any, Optional

logger = logging.getLogger("MoziToolKit.Bridge.Prefs")


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

    # 1. Type-based resolution (works regardless of extension namespace prefix)
    pref_cls = getattr(bpy.types, "MOZI_AddonPreferences", None)
    for addon in addons.values():
        pref = getattr(addon, "preferences", None)
        if pref is not None:
            if pref_cls and isinstance(pref, pref_cls):
                return pref
            if hasattr(pref, "resource_packs") or hasattr(pref, "enable_dna_acceleration"):
                return pref

    # 2. Known package idnames (e.g. bl_ext.vscode_development.MoziToolKit, MoziToolKit)
    candidates = ["bl_ext.vscode_development.MoziToolKit", "MoziToolKit"]
    pkg = __package__ or ""
    if pkg:
        parts = pkg.split(".")
        # Check ancestor packages (e.g., bl_ext.xxx.MoziToolKit from bl_ext.xxx.MoziToolKit.bridge)
        for i in range(len(parts), 0, -1):
            candidates.insert(0, ".".join(parts[:i]))

    for name in candidates:
        addon = addons.get(name)
        if addon and getattr(addon, "preferences", None) is not None:
            return addon.preferences

    # 3. Headless/test environment fallback: ensure registered in addons
    idname = getattr(pref_cls, "bl_idname", "MoziToolKit") if pref_cls else "MoziToolKit"
    try:
        if idname not in addons:
            addons.new(name=idname)
        addon = addons.get(idname)
        if addon and getattr(addon, "preferences", None) is not None:
            return addon.preferences
    except Exception:
        pass

    return None


__all__ = ["get_prefs"]
