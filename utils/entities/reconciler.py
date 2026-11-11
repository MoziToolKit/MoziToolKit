# coding: utf-8
"""
Visual Entity Reconciler for MoziToolKit.

Provides declarative and incremental reconciliation of Blender scene graph entities
driven by canonical EntityRenderPlan contracts. Supports full-scene authoritative builds
and fine-grained delta updates (targeted add, modify, and delete) in O(K) time.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Union

try:
    import bpy
except ImportError:
    bpy = None

from ...bridge.scene import build_entity_render_plan
from ..materials.builder.world_binding import ensure_world_materials
from ..world.injector import inject_mesh_data

logger = logging.getLogger(__name__)


def safe_set_custom_prop(obj: Any, key: str, value: Any) -> None:
    """Safely assigns custom property onto Blender RNA object/data."""
    if obj is None:
        return
    try:
        obj[key] = value
        if hasattr(obj, "id_properties_ensure"):
            id_props = obj.id_properties_ensure()
            if id_props and hasattr(id_props, "property_overridable_library_set"):
                id_props.property_overridable_library_set(key, False)
    except Exception:
        try:
            setattr(obj, key, value)
        except Exception:
            pass


def destroy_entity_obj(obj: Any) -> bool:
    """
    Physically removes a visual entity object and its associated mesh data block
    from bpy.data, cleanly unlinking from collections and preventing memory leaks.
    """
    if obj is None or bpy is None or not hasattr(bpy, "data"):
        return False

    mesh_to_delete = getattr(obj, "data", None)
    deleted = False

    if hasattr(bpy.data, "objects") and obj.name in bpy.data.objects:
        try:
            bpy.data.objects.remove(obj, do_unlink=True)
            deleted = True
        except Exception as e:
            logger.debug("Failed unlinking entity object %s: %s", getattr(obj, "name", "unknown"), e)

    if mesh_to_delete is not None and hasattr(bpy.data, "meshes") and mesh_to_delete.name in bpy.data.meshes:
        try:
            bpy.data.meshes.remove(mesh_to_delete, do_unlink=True)
        except Exception:
            pass

    return deleted


@dataclass
class EntityReconcileResult:
    """Structured result of an entity reconciliation pass."""
    created: List[Any] = field(default_factory=list)
    updated: List[Any] = field(default_factory=list)
    deleted: List[str] = field(default_factory=list)
    all_entities: List[Any] = field(default_factory=list)
    warnings: List[Dict[str, str]] = field(default_factory=list)

    def __iter__(self):
        return iter(self.all_entities)

    def __len__(self):
        return len(self.all_entities)

    def __getitem__(self, idx):
        return self.all_entities[idx]


def reconcile_entity_render_plan(
    root_or_container: Any,
    plan: Optional[Any] = None,
    entities_palette: Optional[List[Union[str, dict]]] = None,
    atlas: Any = None,
    target_coll: Any = None,
    template_mesh_obj: Any = None,
    container_name: str = "World",
    origin_centered: bool = True,
    bounds: Optional[Any] = None,
    prefs: Optional[Any] = None,
    dynamic_entity_atlases: Optional[Any] = None,
    package_reader: Optional[Any] = None,
    authoritative: Optional[bool] = None,
    deleted_keys: Optional[List[str]] = None,
) -> EntityReconcileResult:
    """
    Reconciles visual entities (such as Paintings and Item Frames) in the Blender
    scene graph against a canonical EntityRenderPlan.

    Supports:
    1. Full-scene authoritative build (when plan.authoritative is True, purges stale entities).
    2. Targeted incremental delta (when plan.authoritative is False, adds/updates nodes and deletes plan.deleted_keys).
    3. Transform-only fast path (skips mesh reinjection when only location/rotation/scale changed).
    4. In-place mesh update (reinjects mesh when variant or dimensions change without destroying object hierarchy).
    """
    result = EntityReconcileResult()

    # 1. Resolve or compile plan if not provided
    active_plan = plan
    if active_plan is None:
        if entities_palette is None and not deleted_keys:
            return result

        norm_bounds = [int(x) for x in bounds] if bounds and len(bounds) == 6 else None
        atlas_mapping_json = None
        if atlas is not None:
            if hasattr(atlas, "get_mapping_json"):
                atlas_mapping_json = atlas.get_mapping_json()
            elif hasattr(atlas, "mapping_json"):
                atlas_mapping_json = atlas.mapping_json

        auth_flag = True if authoritative is None else bool(authoritative)
        try:
            active_plan = build_entity_render_plan(
                entities=entities_palette or [],
                atlas=atlas,
                atlas_mapping_json=atlas_mapping_json,
                bounds=norm_bounds,
                origin_centered=origin_centered,
                coordinate_system="z_up",
                revision=0,
                authoritative=auth_flag,
                deleted_keys=deleted_keys,
            )
        except Exception as e:
            logger.warning("Failed building entity render plan: %s", e)
            result.warnings.append({"code": "plan_build_failed", "message": str(e)})
            return result

    if active_plan is None:
        return result

    if hasattr(active_plan, "warnings"):
        for w in active_plan.warnings:
            result.warnings.append(dict(w) if isinstance(w, dict) else {"message": str(w)})

    # 2. Resolve root_obj and entities_parent container
    root_obj = root_or_container
    entities_parent = None

    if root_obj is not None and hasattr(root_obj, "get"):
        if root_obj.get("mtk:is_entities_container"):
            entities_parent = root_obj
            root_obj = getattr(root_obj, "parent", None)
        elif hasattr(root_obj, "children"):
            for child in root_obj.children:
                if (hasattr(child, "get") and child.get("mtk:is_entities_container")) or child.name.endswith("_Entities"):
                    entities_parent = child
                    break

    # Discover target collection from root or container
    if target_coll is None and bpy is not None:
        cand = entities_parent or root_obj
        if cand is not None and hasattr(cand, "users_collection") and cand.users_collection:
            target_coll = cand.users_collection[0]
        elif hasattr(bpy, "context") and hasattr(bpy.context, "collection"):
            target_coll = bpy.context.collection

    # Discover container bounds / origin_centered if not explicitly given
    if bounds is None:
        for cand in (entities_parent, root_obj):
            if cand is not None and hasattr(cand, "get") and cand.get("mtk_bounds"):
                bounds = cand.get("mtk_bounds")
                break
    if origin_centered is None:
        for cand in (entities_parent, root_obj):
            if cand is not None and hasattr(cand, "get") and "mtk:origin_centered" in cand:
                origin_centered = bool(cand.get("mtk:origin_centered"))
                break

    parent_name = f"{container_name}_Entities"

    # 3. Discover all existing visual entity objects
    def _collect_descendant_entities(parent: Any, found_set: Set[Any]) -> List[Any]:
        items = []
        for child in getattr(parent, "children", []):
            if child not in found_set:
                found_set.add(child)
                if hasattr(child, "get") and child.get("mtk:is_visual_entity"):
                    items.append(child)
                items.extend(_collect_descendant_entities(child, found_set))
        return items

    found_objs: Set[Any] = set()
    existing_entities: List[Any] = []
    if entities_parent is not None:
        existing_entities.extend(_collect_descendant_entities(entities_parent, found_objs))
    if root_obj is not None and root_obj != entities_parent:
        existing_entities.extend(_collect_descendant_entities(root_obj, found_objs))

    # 4. Index existing entities by key and uuid
    def _object_identity_key(obj: Any) -> str:
        if obj.get("mtk:entity_key"):
            return str(obj.get("mtk:entity_key"))
        if obj.get("mtk:entity_uuid"):
            return f"uuid:{obj.get('mtk:entity_uuid')}"
        ent_id = obj.get("mtk:entity_id", "minecraft:entity")
        var = obj.get("mtk:variant", "")
        payload = obj.get("mtk:entity_payload")
        if isinstance(payload, (list, tuple)) and len(payload) > 0:
            try:
                p_dict = json.loads(payload[0])
                pos = p_dict.get("position", [0, 0, 0])
                px = round(float(pos[0]), 3) if len(pos) > 0 else 0
                py = round(float(pos[1]), 3) if len(pos) > 1 else 0
                pz = round(float(pos[2]), 3) if len(pos) > 2 else 0
                return f"synth:{ent_id}|pos:{px:.3f},{py:.3f},{pz:.3f}|var:{var}"
            except Exception:
                pass
        return f"name:{obj.name}"

    existing_by_key: Dict[str, Any] = {}
    for obj in existing_entities:
        k = _object_identity_key(obj)
        existing_by_key[k] = obj
        uuid_val = obj.get("mtk:entity_uuid")
        if uuid_val:
            existing_by_key[f"uuid:{uuid_val}"] = obj
            existing_by_key[str(uuid_val)] = obj

    # 5. Process explicit deletions (from plan.deleted_keys or direct deleted_keys)
    effective_deleted_keys = list(getattr(active_plan, "deleted_keys", []) or [])
    if deleted_keys:
        for dk in deleted_keys:
            if dk not in effective_deleted_keys:
                effective_deleted_keys.append(dk)

    for del_key in effective_deleted_keys:
        # Match exact key, uuid:key, or raw uuid
        target_obj = existing_by_key.pop(del_key, None)
        if target_obj is None and not del_key.startswith("uuid:"):
            target_obj = existing_by_key.pop(f"uuid:{del_key}", None)
        if target_obj is None and del_key.startswith("uuid:"):
            target_obj = existing_by_key.pop(del_key[5:], None)

        if target_obj is not None:
            obj_name = target_obj.name
            destroy_entity_obj(target_obj)
            result.deleted.append(del_key)
            # Remove from all aliases in existing_by_key
            stale_keys = [k for k, v in existing_by_key.items() if v == target_obj]
            for sk in stale_keys:
                existing_by_key.pop(sk, None)
            logger.debug("Deleted entity object '%s' for key '%s'", obj_name, del_key)

    # If plan has no nodes:
    if active_plan.node_count == 0:
        if active_plan.authoritative:
            for old_obj in list(set(existing_by_key.values())):
                destroy_entity_obj(old_obj)
                result.deleted.append(old_obj.name)
            existing_by_key.clear()
        else:
            # Add surviving untouched entities to result
            for surviving_obj in set(existing_by_key.values()):
                result.all_entities.append(surviving_obj)
        return result

    # 6. Ensure entities_parent container exists if we have nodes to create
    if entities_parent is None and root_obj is not None and bpy is not None and hasattr(bpy, "data"):
        entities_parent = bpy.data.objects.new(parent_name, None)
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
        if target_coll is not None and hasattr(target_coll, "objects") and hasattr(target_coll.objects, "link"):
            try:
                target_coll.objects.link(entities_parent)
            except Exception:
                pass

    # 7. Upsert nodes: diff update existing or create new
    for raw_node in active_plan.nodes:
        node = raw_node
        transform = node.transform
        reused_obj = existing_by_key.pop(node.key, None)
        if reused_obj is None and node.uuid:
            reused_obj = existing_by_key.pop(f"uuid:{node.uuid}", None)
        if reused_obj is None and node.uuid:
            reused_obj = existing_by_key.pop(str(node.uuid), None)

        if reused_obj is not None and getattr(reused_obj, "data", None) is not None:
            # Clean up all alias references pointing to reused_obj
            stale_keys = [k for k, v in existing_by_key.items() if v == reused_obj]
            for sk in stale_keys:
                existing_by_key.pop(sk, None)

            # Update existing object in-place
            try:
                # 7a. Rigid transform update (Fast-path: direct assignment)
                reused_obj.location = transform.location
                reused_obj.rotation_euler = transform.rotation_euler
                reused_obj.scale = transform.scale

                # 7b. Topology / variant check: reinject mesh only when necessary
                old_var = reused_obj.get("mtk:variant", "")
                old_w = reused_obj.get("mtk:width", 0)
                old_h = reused_obj.get("mtk:height", 0)
                new_var = node.variant or ""
                new_dims = node.dimensions or (0, 0)

                needs_mesh_reinjection = (
                    old_var != new_var
                    or old_w != new_dims[0]
                    or old_h != new_dims[1]
                )

                if needs_mesh_reinjection:
                    inject_mesh_data(node.mesh, reused_obj.data, update_topology=True, update_normals=True)

                # 7c. Update metadata custom properties
                safe_set_custom_prop(reused_obj, "mtk:entity_key", node.key)
                safe_set_custom_prop(reused_obj, "mtk:is_visual_entity", True)
                safe_set_custom_prop(reused_obj, "mtk:entity_id", node.entity_id)
                safe_set_custom_prop(reused_obj, "mtk:entity_payload", [node.entity_json])
                if node.uuid:
                    safe_set_custom_prop(reused_obj, "mtk:entity_uuid", node.uuid)
                if node.custom_name:
                    safe_set_custom_prop(reused_obj, "mtk:custom_name", node.custom_name)
                if node.facing:
                    safe_set_custom_prop(reused_obj, "mtk:facing", node.facing)
                if node.variant:
                    safe_set_custom_prop(reused_obj, "mtk:variant", node.variant)
                if node.dimensions:
                    safe_set_custom_prop(reused_obj, "mtk:width", int(node.dimensions[0]))
                    safe_set_custom_prop(reused_obj, "mtk:height", int(node.dimensions[1]))

                result.updated.append(reused_obj)
                result.all_entities.append(reused_obj)
                continue
            except Exception as e:
                logger.debug("Failed diff-updating existing entity %s: %s, recreating", node.display_name, e)
                destroy_entity_obj(reused_obj)

        # 7d. Create new entity object
        b_mesh = None
        ent_obj = None
        try:
            b_mesh = bpy.data.meshes.new(name=f"{node.display_name}_Mesh")
            ent_obj = bpy.data.objects.new(name=node.display_name, object_data=b_mesh)

            inject_mesh_data(node.mesh, b_mesh, update_topology=True, update_normals=True)

            ent_obj.location = transform.location
            ent_obj.rotation_euler = transform.rotation_euler
            ent_obj.scale = transform.scale

            try:
                ensure_world_materials(
                    ent_obj,
                    prefs=prefs,
                    atlas=atlas,
                    dynamic_entity_atlases=dynamic_entity_atlases,
                    package_reader=package_reader,
                    clean_unused_slots=True,
                )
            except Exception as e:
                logger.warning("Failed ensuring materials for entity %s: %s", node.display_name, e)
                if template_mesh_obj and hasattr(template_mesh_obj, "data") and hasattr(template_mesh_obj.data, "materials"):
                    for mat in template_mesh_obj.data.materials:
                        if mat is not None:
                            ent_obj.data.materials.append(mat)

            if entities_parent is not None:
                ent_obj.parent = entities_parent
            elif root_obj is not None:
                ent_obj.parent = root_obj
            if hasattr(ent_obj, "matrix_parent_inverse"):
                ent_obj.matrix_parent_inverse.identity()

            safe_set_custom_prop(ent_obj, "mtk:is_visual_entity", True)
            safe_set_custom_prop(ent_obj, "mtk:entity_key", node.key)
            safe_set_custom_prop(ent_obj, "mtk:entity_id", node.entity_id)
            safe_set_custom_prop(ent_obj, "mtk:entity_payload", [node.entity_json])
            if node.uuid:
                safe_set_custom_prop(ent_obj, "mtk:entity_uuid", node.uuid)
            if node.custom_name:
                safe_set_custom_prop(ent_obj, "mtk:custom_name", node.custom_name)
            if node.facing:
                safe_set_custom_prop(ent_obj, "mtk:facing", node.facing)
            if node.variant:
                safe_set_custom_prop(ent_obj, "mtk:variant", node.variant)
            if node.dimensions:
                safe_set_custom_prop(ent_obj, "mtk:width", int(node.dimensions[0]))
                safe_set_custom_prop(ent_obj, "mtk:height", int(node.dimensions[1]))

            if target_coll is not None and hasattr(target_coll, "objects") and hasattr(target_coll.objects, "link"):
                try:
                    target_coll.objects.link(ent_obj)
                except Exception:
                    pass

            result.created.append(ent_obj)
            result.all_entities.append(ent_obj)
        except Exception as e:
            logger.warning("Failed assembling visual entity '%s': %s", node.display_name, e, exc_info=True)
            if ent_obj is not None:
                destroy_entity_obj(ent_obj)
            elif b_mesh is not None and hasattr(bpy, "data") and hasattr(bpy.data, "meshes") and b_mesh.name in bpy.data.meshes:
                try:
                    bpy.data.meshes.remove(b_mesh, do_unlink=True)
                except Exception:
                    pass

    # 8. Authoritative diff cleanup: destroy stale entities when plan is authoritative
    if active_plan.authoritative:
        stale_unique = set(existing_by_key.values())
        for stale_obj in stale_unique:
            result.deleted.append(stale_obj.name)
            destroy_entity_obj(stale_obj)
    else:
        # Incremental mode: append untouched surviving entities to all_entities
        surviving = set(existing_by_key.values())
        for surviving_obj in surviving:
            result.all_entities.append(surviving_obj)

    return result


__all__ = [
    "safe_set_custom_prop",
    "destroy_entity_obj",
    "EntityReconcileResult",
    "reconcile_entity_render_plan",
]
