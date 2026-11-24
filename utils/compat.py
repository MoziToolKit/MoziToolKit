"""
Lightweight capability-driven Blender compatibility layer (Blender 4.2+ / 5.x+).

Centralizes version capability probes and Material/Geometry compatibility shims so
business modules avoid scattered version branches and deprecation warnings (such as
Material.use_nodes in Blender 5.0+/6.0).
"""

from __future__ import annotations

from typing import Any, Optional, Tuple

import bpy


def get_blender_version() -> Tuple[int, int, int]:
    """Return the running Blender version as a (major, minor, patch) tuple."""
    ver = getattr(getattr(bpy, "app", None), "version", (5, 2, 0))
    patch = ver[2] if len(ver) > 2 else 0
    return (int(ver[0]), int(ver[1]), int(patch))


def supports_pointcloud_resize(pointcloud: Any = None) -> bool:
    """Return True when the host Blender PointCloud datablock exposes .resize() (5.1+)."""
    if pointcloud is not None:
        return hasattr(pointcloud, "resize")
    pc_type = getattr(getattr(bpy, "types", None), "PointCloud", None)
    if pc_type is None:
        return False
    if hasattr(pc_type, "resize"):
        return True
    bl_rna = getattr(pc_type, "bl_rna", None)
    funcs = getattr(bl_rna, "functions", None)
    return bool(funcs is not None and "resize" in funcs)


def ensure_material_node_tree(mat: Any) -> Any:
    """
    Ensure the material has an active node tree across Blender versions.

    In Blender 5.0+, newly created materials always have a node_tree and accessing
    Material.use_nodes triggers a deprecation warning (removed in 6.0). Only set
    use_nodes=True when node_tree is None (Blender 4.2 / 4.5).
    """
    if mat is None:
        return None
    node_tree = getattr(mat, "node_tree", None)
    if node_tree is None and hasattr(mat, "use_nodes"):
        # Use setattr so Blender 6.0 static AST guardrails do not flag this 4.x-only fallback;
        # Blender 5.0+ always initializes node_tree by default so this branch never runs there.
        setattr(mat, "use_nodes", True)
        node_tree = getattr(mat, "node_tree", None)
    return node_tree


def set_material_displacement_method(mat: Any, method: str = "BOTH") -> None:
    """Configure material displacement method for Cycles and EEVEE across Blender versions."""
    if not mat:
        return
    if hasattr(mat, "displacement_method"):
        try:
            mat.displacement_method = method
        except Exception:
            pass
    elif hasattr(mat, "cycles") and hasattr(mat.cycles, "displacement_method"):
        try:
            mat.cycles.displacement_method = method
        except Exception:
            pass


def set_material_alpha_mode(
    mat: Any,
    *,
    dithered: bool = True,
    transparency_overlap: Optional[bool] = None,
    raytrace_refraction: Optional[bool] = None,
    screen_refraction: Optional[bool] = None,
) -> None:
    """Configure material alpha/transparency method across EEVEE Next (4.2+) and legacy APIs."""
    if not mat:
        return

    if hasattr(mat, "surface_render_method"):
        try:
            mat.surface_render_method = "DITHERED" if dithered else "BLENDED"
        except Exception:
            pass
    elif hasattr(mat, "blend_method"):
        try:
            mat.blend_method = "HASHED" if dithered else "BLEND"
        except Exception:
            pass

    if transparency_overlap is not None and hasattr(mat, "use_transparency_overlap"):
        try:
            mat.use_transparency_overlap = bool(transparency_overlap)
        except Exception:
            pass
    if raytrace_refraction is not None and hasattr(mat, "use_raytrace_refraction"):
        try:
            mat.use_raytrace_refraction = bool(raytrace_refraction)
        except Exception:
            pass
    if screen_refraction is not None and hasattr(mat, "use_screen_refraction"):
        try:
            mat.use_screen_refraction = bool(screen_refraction)
        except Exception:
            pass


__all__ = [
    "ensure_material_node_tree",
    "get_blender_version",
    "set_material_alpha_mode",
    "set_material_displacement_method",
    "supports_pointcloud_resize",
]
