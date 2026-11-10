"""
System environment and preference utilities for MoziToolKit.
Dependency isolation and wheel management is delegated entirely to Blender 4.2+ Extensions.
"""

from typing import Optional

import bpy


def has_libmtk() -> bool:
    """Check if LibMTK native Rust core is available."""
    try:
        from ...bridge import has_libmtk as _engine_has_libmtk
        return _engine_has_libmtk()
    except Exception:
        return False


from ...bridge.prefs import get_prefs

__all__ = ["has_libmtk", "get_prefs"]

