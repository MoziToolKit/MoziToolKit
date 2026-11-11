# coding: utf-8
"""
Visual entities scene extraction and transform conversion utilities for MoziToolKit.

Provides mathematical conversion between Minecraft entity coordinates/rotations and
Blender Object Space / World Space using standard 3D change of basis:
- Minecraft: +X East, +Y Up, +Z South (Right-handed, local model front = -Z)
- Blender:   +X East, +Y North, +Z Up (Right-handed Z-Up, local model front = -Y)
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

try:
    import bpy
    from mathutils import Matrix, Euler, Quaternion
except ImportError:
    bpy = None
    Matrix = None
    Euler = None
    Quaternion = None

# Change of basis matrix: Minecraft to Blender (Z-Up)
# v_bl = M_M2B @ v_mc => (x, -z, y)
_M_M2B_DATA = (
    (1.0,  0.0,  0.0),
    (0.0,  0.0, -1.0),
    (0.0,  1.0,  0.0),
)

# Change of basis matrix: Blender to Minecraft
# v_mc = M_B2M @ v_bl => (x, z, -y)
_M_B2M_DATA = (
    (1.0,  0.0,  0.0),
    (0.0,  0.0,  1.0),
    (0.0, -1.0,  0.0),
)


def get_m2b_matrix() -> Matrix:
    return Matrix(_M_M2B_DATA)


def get_b2m_matrix() -> Matrix:
    return Matrix(_M_B2M_DATA)


def mc_rotation_to_blender_euler(pitch: float, yaw: float, roll: float) -> Euler:
    """
    Converts canonical Minecraft local-model Euler rotation (pitch, yaw, roll in radians)
    into a Blender Euler (XYZ) using change-of-basis matrix conjugation:
        R_bl = M_m2b @ R_mc @ M_b2m
    """
    if Matrix is None or Euler is None:
        return (-pitch, roll, yaw)
    m2b = get_m2b_matrix()
    b2m = get_b2m_matrix()
    r_mc = Euler((float(pitch), float(yaw), float(roll)), "XYZ").to_matrix()
    r_bl = m2b @ r_mc @ b2m
    return r_bl.to_euler("XYZ")


def blender_matrix_to_mc_euler(rot_matrix_or_quat: Any) -> Tuple[float, float, float]:
    """
    Converts a Blender rotation (Matrix or Quaternion) into canonical Minecraft
    local-model Euler XYZ angles (pitch, yaw, roll in radians) via inverse change of basis:
        R_mc = M_b2m @ R_bl @ M_m2b
    """
    if Matrix is None or Euler is None:
        if hasattr(rot_matrix_or_quat, "to_euler"):
            e = rot_matrix_or_quat.to_euler("XYZ")
            return (-float(e.x), float(e.z), float(e.y))
        return (0.0, 0.0, 0.0)

    b2m = get_b2m_matrix()
    m2b = get_m2b_matrix()
    if hasattr(rot_matrix_or_quat, "to_3x3"):
        r_bl = rot_matrix_or_quat.to_3x3()
    elif hasattr(rot_matrix_or_quat, "to_matrix"):
        r_bl = rot_matrix_or_quat.to_matrix().to_3x3()
    else:
        r_bl = Matrix(rot_matrix_or_quat).to_3x3()

    r_mc = b2m @ r_bl @ m2b
    e_mc = r_mc.to_euler("XYZ")
    return (float(e_mc.x), float(e_mc.y), float(e_mc.z))


def extract_scene_visual_entities(
    root_or_cloud_obj: Any,
    point_cloud: Any = None,
) -> Optional[str]:
    """
    Extracts visual entities currently present in the Blender scene under `root_or_cloud_obj`,
    updating their continuous coordinates, rotations, and scales relative to the container.
    """
    if root_or_cloud_obj is None:
        return None

    # Ensure Blender scene matrices are fully evaluated before extracting transforms
    try:
        if bpy is not None and hasattr(bpy, "context") and hasattr(bpy.context, "view_layer") and hasattr(bpy.context.view_layer, "update"):
            bpy.context.view_layer.update()
    except Exception:
        pass

    # Discover root object and entities container
    root = root_or_cloud_obj
    entities_parent = None
    has_entities_container = False
    is_known_container = False

    if hasattr(root, "get"):
        if root.get("mtk:is_entities_container"):
            entities_parent = root
            has_entities_container = True
            is_known_container = True
            root = getattr(root, "parent", root)
        elif root.get("mtk:is_scene_root") or root.get("mtk:is_world_root"):
            is_known_container = True

    if entities_parent is None and hasattr(root, "children"):
        for child in root.children:
            if hasattr(child, "get") and child.get("mtk:is_entities_container"):
                entities_parent = child
                has_entities_container = True
                break
            if child.name.endswith("_Entities"):
                entities_parent = child
                has_entities_container = True
                break

    # Discover all visual entities recursively under root and entities_parent
    ent_objs: List[Any] = []
    visited = set()

    def _collect(obj: Any):
        for child in getattr(obj, "children", []):
            if child not in visited:
                visited.add(child)
                if hasattr(child, "get") and child.get("mtk:is_visual_entity"):
                    ent_objs.append(child)
                _collect(child)

    if entities_parent is not None:
        _collect(entities_parent)
    if root is not None and root != entities_parent:
        _collect(root)

    if not ent_objs:
        if has_entities_container or is_known_container:
            # Explicit empty list: user removed entities from container
            return "[]"
        return None

    # Determine origin centering offset from companion cloud bounds if available
    bounds = None
    if point_cloud is not None and hasattr(point_cloud, "bounds") and point_cloud.bounds is not None:
        bounds = point_cloud.bounds
    elif hasattr(root_or_cloud_obj, "get") and root_or_cloud_obj.get("mtk_bounds"):
        bounds = root_or_cloud_obj.get("mtk_bounds")
    elif root is not None and hasattr(root, "get") and root.get("mtk_bounds"):
        bounds = root.get("mtk_bounds")

    origin_centered = True
    for cand in (root_or_cloud_obj, root):
        if cand is not None and hasattr(cand, "get") and "mtk:origin_centered" in cand:
            origin_centered = bool(cand.get("mtk:origin_centered"))
            break

    cx, by, cz = 0.0, 0.0, 0.0
    if origin_centered and bounds and len(bounds) == 6:
        cx = float(bounds[0]) + float(bounds[3]) / 2.0
        by = float(bounds[1])
        cz = float(bounds[2]) + float(bounds[5]) / 2.0

    updated_entities = []
    for obj in ent_objs:
        ent_json = None
        payload = obj.get("mtk:entity_payload")
        if isinstance(payload, (list, tuple)) and len(payload) > 0:
            ent_json = payload[0]
        if not ent_json:
            continue
        try:
            ent = json.loads(ent_json)
        except Exception:
            continue

        if root is not None and hasattr(root, "matrix_world") and hasattr(obj, "matrix_world"):
            try:
                relative = root.matrix_world.inverted() @ obj.matrix_world
                loc, rot_quat, scale = relative.decompose()
            except Exception:
                loc = obj.location
                rot_quat = obj.rotation_euler.to_quaternion() if hasattr(obj.rotation_euler, "to_quaternion") else None
                scale = obj.scale
        else:
            loc = obj.location
            rot_quat = obj.rotation_euler.to_quaternion() if hasattr(obj.rotation_euler, "to_quaternion") else None
            scale = obj.scale

        # Invert target coordinate system (Blender Z-Up -> Minecraft)
        # Matches Rust `mtk_core::mc_world_to_z_up(wx, wy, wz) = Vec3(wx, -wz, wy)`
        # blender_x = mc_x - cx   =>  mc_x = blender_x + cx
        # blender_y = -(mc_z - cz)=>  mc_z = -blender_y + cz
        # blender_z = mc_y - by   =>  mc_y = blender_z + by
        mc_x = float(loc[0]) + cx
        mc_z = -float(loc[1]) + cz
        mc_y = float(loc[2]) + by
        ent["position"] = [mc_x, mc_y, mc_z]

        # Invert rotation mapping using authoritative matrix basis conjugation
        if rot_quat is not None:
            pitch, yaw, roll = blender_matrix_to_mc_euler(rot_quat)
            ent["rotation"] = [pitch, yaw, roll]
        else:
            r = getattr(obj, "rotation_euler", (0.0, 0.0, 0.0))
            ent["rotation"] = [-float(r[0]), float(r[2]), float(r[1])]

        # Scale mapping: (X_bl -> X_mc, Y_bl -> Z_mc, Z_bl -> Y_mc)
        ent["scale"] = [float(scale[0]), float(scale[2]), float(scale[1])]

        if obj.get("mtk:entity_uuid"):
            ent["uuid"] = str(obj.get("mtk:entity_uuid"))
        if obj.get("mtk:custom_name"):
            ent["custom_name"] = str(obj.get("mtk:custom_name"))
        if obj.get("mtk:facing"):
            ent["facing"] = str(obj.get("mtk:facing"))
        if obj.get("mtk:variant"):
            var_str = str(obj.get("mtk:variant"))
            if "kind" in ent and isinstance(ent["kind"], dict):
                ent["kind"]["variant"] = var_str
            else:
                ent["kind"] = {"variant": var_str}

        updated_entities.append(ent)

    return json.dumps(updated_entities)
