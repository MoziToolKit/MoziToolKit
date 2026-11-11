# coding: utf-8
"""
High-level local/incremental entity operations for MoziToolKit.

Provides O(K) batch add/modify/delete and single entity local update operations
without rebuilding the full scene, preserving scene graph hierarchy and synchronizing
companion point cloud visual entity palettes.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional, Union

try:
    import bpy
except ImportError:
    bpy = None

from ...bridge.point_cloud.constants import PROP_VISUAL_ENTITIES
from .reconciler import (
    EntityReconcileResult,
    destroy_entity_obj,
    reconcile_entity_render_plan,
    safe_set_custom_prop,
)

logger = logging.getLogger(__name__)


def find_entity_object(root_or_container: Any, key_or_uuid: str) -> Optional[Any]:
    """
    Finds a visual entity object under root or entities container by key or UUID.
    """
    if root_or_container is None:
        return None

    target_key = str(key_or_uuid)
    clean_uuid = target_key[5:] if target_key.startswith("uuid:") else target_key

    # Search candidates
    to_visit = [root_or_container]
    visited = set()

    while to_visit:
        curr = to_visit.pop(0)
        if curr in visited:
            continue
        visited.add(curr)

        if hasattr(curr, "get") and curr.get("mtk:is_visual_entity"):
            k = curr.get("mtk:entity_key")
            u = curr.get("mtk:entity_uuid")
            if k == target_key or k == f"uuid:{clean_uuid}" or u == clean_uuid or curr.name == target_key:
                return curr

        for child in getattr(curr, "children", []):
            if child not in visited:
                to_visit.append(child)

    return None


def get_or_create_entities_container(
    root_obj: Any,
    container_name: str = "World",
    target_coll: Any = None,
    bounds: Optional[List[int]] = None,
    origin_centered: bool = True,
) -> Any:
    """
    Resolves or creates the child entities container `{container_name}_Entities`
    under `root_obj`.
    """
    if root_obj is not None and hasattr(root_obj, "get") and root_obj.get("mtk:is_entities_container"):
        return root_obj

    parent_name = f"{container_name}_Entities"
    if root_obj is not None and hasattr(root_obj, "children"):
        for child in root_obj.children:
            if (hasattr(child, "get") and child.get("mtk:is_entities_container")) or child.name == parent_name:
                return child

    if bpy is None or not hasattr(bpy, "data"):
        return None

    entities_parent = bpy.data.objects.new(parent_name, None)
    if root_obj is not None:
        entities_parent.parent = root_obj
        if hasattr(entities_parent, "matrix_parent_inverse"):
            entities_parent.matrix_parent_inverse.identity()

    if hasattr(entities_parent, "empty_display_type"):
        entities_parent.empty_display_type = "PLAIN_AXES"
    if hasattr(entities_parent, "empty_display_size"):
        entities_parent.empty_display_size = 0.5

    safe_set_custom_prop(entities_parent, "mtk:is_entities_container", True)
    if bounds and len(bounds) == 6:
        safe_set_custom_prop(entities_parent, "mtk_bounds", [int(x) for x in bounds])
    safe_set_custom_prop(entities_parent, "mtk:origin_centered", bool(origin_centered))
    safe_set_custom_prop(entities_parent, "mtk_origin_centered", bool(origin_centered))

    if target_coll is None and root_obj is not None and hasattr(root_obj, "users_collection") and root_obj.users_collection:
        target_coll = root_obj.users_collection[0]
    elif target_coll is None and hasattr(bpy, "context") and hasattr(bpy.context, "collection"):
        target_coll = bpy.context.collection

    if target_coll is not None and hasattr(target_coll, "objects") and hasattr(target_coll.objects, "link"):
        try:
            target_coll.objects.link(entities_parent)
        except Exception:
            pass

    return entities_parent


def _sync_companion_cloud_palette(
    root_or_container: Any,
    upsert_entities: Optional[List[Union[str, dict]]] = None,
    delete_keys: Optional[List[str]] = None,
) -> None:
    """
    Incrementally synchronizes companion point cloud's `mtk_visual_entities`
    property array when entities are modified locally.
    """
    if root_or_container is None:
        return

    # Find root and cloud object
    root = root_or_container
    if hasattr(root, "get") and root.get("mtk:is_entities_container"):
        root = getattr(root, "parent", root)

    cloud_obj = None
    try:
        from ..point_cloud.hierarchy import get_associated_voxel_cloud
        cloud_obj = get_associated_voxel_cloud(root)
    except Exception:
        pass

    if cloud_obj is None and root is not None and hasattr(root, "children"):
        for child in root.children:
            if (
                child.get("mtk_is_voxel_cloud")
                or child.get("mtk:is_point_cloud")
                or child.name.endswith("_VoxelCloud")
                or child.name.endswith("_Cloud")
            ):
                cloud_obj = child
                break

    target_prop_holder = cloud_obj or root
    if target_prop_holder is None:
        return

    raw_palette = target_prop_holder.get(PROP_VISUAL_ENTITIES)
    palette_list: List[str] = list(raw_palette) if isinstance(raw_palette, (list, tuple)) else []

    # 1. Process deletes
    if delete_keys:
        del_set = set()
        for k in delete_keys:
            del_set.add(k)
            if k.startswith("uuid:"):
                del_set.add(k[5:])
            else:
                del_set.add(f"uuid:{k}")

        new_palette = []
        for item in palette_list:
            matched = False
            try:
                p_dict = json.loads(item) if isinstance(item, str) else item
                u = p_dict.get("uuid")
                if u and (u in del_set or f"uuid:{u}" in del_set):
                    matched = True
            except Exception:
                pass
            if not matched:
                new_palette.append(item)
        palette_list = new_palette

    # 2. Process upserts
    if upsert_entities:
        for ent in upsert_entities:
            ent_dict = json.loads(ent) if isinstance(ent, str) else dict(ent)
            ent_str = json.dumps(ent_dict) if not isinstance(ent, str) else ent
            u = ent_dict.get("uuid")

            replaced = False
            if u:
                for idx, existing_item in enumerate(palette_list):
                    try:
                        ex_dict = json.loads(existing_item) if isinstance(existing_item, str) else existing_item
                        if ex_dict.get("uuid") == u:
                            palette_list[idx] = ent_str
                            replaced = True
                            break
                    except Exception:
                        pass
            if not replaced:
                palette_list.append(ent_str)

    # 3. Store back as discrete array property on object and mesh data
    safe_set_custom_prop(target_prop_holder, PROP_VISUAL_ENTITIES, palette_list)
    if hasattr(target_prop_holder, "data") and target_prop_holder.data is not None:
        safe_set_custom_prop(target_prop_holder.data, PROP_VISUAL_ENTITIES, palette_list)
    if cloud_obj is not None and target_prop_holder != cloud_obj:
        safe_set_custom_prop(cloud_obj, PROP_VISUAL_ENTITIES, palette_list)
        if hasattr(cloud_obj, "data") and cloud_obj.data is not None:
            safe_set_custom_prop(cloud_obj.data, PROP_VISUAL_ENTITIES, palette_list)


def apply_entity_delta(
    root_or_container: Any,
    upsert_entities: Optional[List[Union[str, dict]]] = None,
    delete_keys: Optional[List[str]] = None,
    atlas: Any = None,
    prefs: Optional[Any] = None,
    bounds: Optional[Any] = None,
    origin_centered: Optional[bool] = None,
    container_name: str = "World",
    target_coll: Any = None,
    template_mesh_obj: Any = None,
    dynamic_entity_atlases: Optional[Any] = None,
    package_reader: Optional[Any] = None,
    sync_palette: bool = True,
) -> EntityReconcileResult:
    """
    Applies a targeted incremental delta (batch upsert and/or delete) to visual entities
    without rebuilding the rest of the world.
    """
    if root_or_container is None:
        return EntityReconcileResult()

    # Discover bounds and origin_centered from container if not explicitly provided
    eff_bounds = bounds
    if eff_bounds is None:
        for cand in (root_or_container, getattr(root_or_container, "parent", None)):
            if cand is not None and hasattr(cand, "get") and cand.get("mtk_bounds"):
                eff_bounds = cand.get("mtk_bounds")
                break

    eff_oc = origin_centered
    if eff_oc is None:
        for cand in (root_or_container, getattr(root_or_container, "parent", None)):
            if cand is not None and hasattr(cand, "get") and "mtk:origin_centered" in cand:
                eff_oc = bool(cand.get("mtk:origin_centered"))
                break
        if eff_oc is None:
            eff_oc = True

    # Call reconciler with authoritative=False (additive/delta mode)
    result = reconcile_entity_render_plan(
        root_or_container=root_or_container,
        entities_palette=upsert_entities,
        deleted_keys=delete_keys,
        atlas=atlas,
        target_coll=target_coll,
        template_mesh_obj=template_mesh_obj,
        container_name=container_name,
        origin_centered=eff_oc,
        bounds=eff_bounds,
        prefs=prefs,
        dynamic_entity_atlases=dynamic_entity_atlases,
        package_reader=package_reader,
        authoritative=False,
    )

    if sync_palette:
        _sync_companion_cloud_palette(
            root_or_container,
            upsert_entities=upsert_entities,
            delete_keys=delete_keys,
        )

    return result


def update_single_entity(
    root_or_container: Any,
    entity_data: Union[str, dict],
    atlas: Any = None,
    prefs: Optional[Any] = None,
    bounds: Optional[Any] = None,
    origin_centered: Optional[bool] = None,
    container_name: str = "World",
    sync_palette: bool = True,
    **kwargs,
) -> Optional[Any]:
    """
    Locally adds or updates a single visual entity in O(1) time without rebuilding the scene.
    Returns the created or updated Blender Object.
    """
    res = apply_entity_delta(
        root_or_container=root_or_container,
        upsert_entities=[entity_data],
        atlas=atlas,
        prefs=prefs,
        bounds=bounds,
        origin_centered=origin_centered,
        container_name=container_name,
        sync_palette=sync_palette,
        **kwargs,
    )
    if res.updated:
        return res.updated[0]
    if res.created:
        return res.created[0]
    return None


def delete_entities_by_keys(
    root_or_container: Any,
    keys_or_uuids: List[str],
    sync_palette: bool = True,
) -> List[str]:
    """
    Locally and physically deletes entities matching `keys_or_uuids` in O(K) time.
    """
    res = apply_entity_delta(
        root_or_container=root_or_container,
        delete_keys=keys_or_uuids,
        sync_palette=sync_palette,
    )
    return res.deleted


def delete_single_entity(
    root_or_container: Any,
    key_or_uuid: str,
    sync_palette: bool = True,
) -> bool:
    """
    Locally and physically deletes a single entity by key or UUID in O(1) time.
    Returns True if found and deleted, False otherwise.
    """
    deleted = delete_entities_by_keys(root_or_container, [key_or_uuid], sync_palette=sync_palette)
    return len(deleted) > 0


def update_entity_transform(
    entity_obj: Any,
    location: Optional[Tuple[float, float, float]] = None,
    rotation_euler: Optional[Tuple[float, float, float]] = None,
    scale: Optional[Tuple[float, float, float]] = None,
) -> Any:
    """
    Directly updates the rigid transform of an existing entity object in Blender space (O(1)).
    """
    if entity_obj is None:
        return None

    if location is not None:
        entity_obj.location = location
    if rotation_euler is not None:
        entity_obj.rotation_euler = rotation_euler
    if scale is not None:
        entity_obj.scale = scale

    return entity_obj


__all__ = [
    "find_entity_object",
    "get_or_create_entities_container",
    "apply_entity_delta",
    "update_single_entity",
    "delete_entities_by_keys",
    "delete_single_entity",
    "update_entity_transform",
]
