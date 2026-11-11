# coding: utf-8
"""
Visual entities scene extraction utilities for MoziToolKit.

Discovers live visual entities in the Blender scene graph and extracts their
rigid transforms back into canonical Minecraft coordinates via native FFI (host_transform_to_mc).
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from ...bridge.scene import host_transform_to_mc

logger = logging.getLogger(__name__)

try:
    import bpy
except ImportError:
    bpy = None


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

    norm_bounds = [int(x) for x in bounds] if bounds and len(bounds) == 6 else None

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

        loc_xyz = (float(loc[0]), float(loc[1]), float(loc[2]))
        if rot_quat is not None and hasattr(rot_quat, "to_euler"):
            e = rot_quat.to_euler("XYZ")
            rot_xyz = (float(e.x), float(e.y), float(e.z))
        else:
            r = getattr(obj, "rotation_euler", (0.0, 0.0, 0.0))
            rot_xyz = (float(r[0]), float(r[1]), float(r[2]))
        scale_xyz = (float(scale[0]), float(scale[1]), float(scale[2]))

        # Invert target coordinate system via canonical Rust operator (Single Source of Truth)
        try:
            mc_pos, mc_rot, mc_scale = host_transform_to_mc(
                loc_xyz,
                rot_xyz,
                scale_xyz,
                bounds=norm_bounds,
                origin_centered=origin_centered,
                coordinate_system="z_up",
            )
            ent["position"] = [float(mc_pos[0]), float(mc_pos[1]), float(mc_pos[2])]
            ent["rotation"] = [float(mc_rot[0]), float(mc_rot[1]), float(mc_rot[2])]
            ent["scale"] = [float(mc_scale[0]), float(mc_scale[1]), float(mc_scale[2])]
        except Exception as e:
            logger.warning("Failed converting host transform to Minecraft coordinate system: %s", e)

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
