"""
Unified Voxel World Scene Assembler.

Single Source of Truth (SSOT) orchestrator for assembling Blender scene datablocks,
Empty container hierarchies, dual-channel mesh geometry injection, companion point clouds,
canonical PBR material binding, and dynamic entity slot persistence.
Unifies the assembly pipeline across Live Sync, Save Import, and .mtkscene Interchange.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

import bpy

from ...bridge.mesh import inject_mesh_data
from ...bridge.world import resolve_dynamic_entity_atlases
from ..materials.builder.world_binding import ensure_world_materials
from ..mesh.ownership import stamp_geometry_origin
from ..point_cloud.hierarchy import ensure_voxel_child_cloud
from ..materials.biome import updater as _biome_updater

logger = logging.getLogger("MoziToolKit.World.Assembler")


def safe_set_custom_prop(obj: Any, key: str, val: Any) -> None:
    """Sets a custom property or fallback attribute without raising exceptions."""
    if obj is None:
        return
    try:
        obj[key] = val
    except (TypeError, AttributeError, KeyError):
        try:
            setattr(obj, key, val)
        except Exception:
            pass


def safe_get_custom_prop(obj: Any, key: str, default: Any = None) -> Any:
    """Gets a custom property with fallback attribute check."""
    if obj is None:
        return default
    if hasattr(obj, "get"):
        val = obj.get(key)
        if val is not None:
            return val
    return getattr(obj, key, default)


def persist_dynamic_entity_slots(
    targets: List[Any],
    slot_map: Optional[Dict[str, Tuple[int, int]]],
) -> Optional[str]:
    """Serializes dynamic entity slot allocation and stamps across target Blender objects."""
    if not slot_map:
        return None
    try:
        slots_json = json.dumps(
            {str(k): [int(v[0]), int(v[1])] for k, v in slot_map.items()},
            separators=(",", ":"),
        )
    except Exception:
        return None

    if slots_json:
        for t in targets:
            if t is not None:
                safe_set_custom_prop(t, "mtk:dynamic_entity_slots", slots_json)
    return slots_json


@dataclass
class VoxelWorldAssemblyRequest:
    """Request DTO specifying everything needed to assemble a voxel world in Blender."""

    container_type: str  # "SAVE" | "SCENE" | "SYNC"
    root_name: str
    mesh_data: Any
    storage: Optional[Any] = None
    point_cloud: Optional[Any] = None
    container_id: Optional[str] = None
    atlas: Optional[Any] = None
    dynamic_entity_atlases: Optional[Any] = None
    dynamic_entity_slots: Optional[Dict[str, Tuple[int, int]]] = None
    colormaps: Optional[Any] = None
    origin_centered: bool = True
    reuse_existing: bool = True
    target_root: Optional[Any] = None
    target_mesh: Optional[Any] = None
    target_collection: Optional[Any] = None
    context: Optional[Any] = None
    prefs: Optional[Any] = None
    geometry_origin: Optional[str] = None
    skip_string_attributes: bool = False
    custom_injector: Optional[Callable[..., Any]] = None
    custom_material_binder: Optional[Callable[[Any], Any]] = None
    custom_cloud_ensurer: Optional[Callable[..., Any]] = None
    clean_unused_material_slots: bool = True
    sync_point_cloud: bool = True
    bounds: Optional[Any] = None
    visual_entities: Optional[List[str]] = None
    render_plan: Optional[Any] = None
    package_reader: Optional[Any] = None
    metadata_props: Dict[str, Any] = field(default_factory=dict)


def _resolve_world_bounds(
    req: VoxelWorldAssemblyRequest,
    cloud_obj: Optional[Any] = None,
) -> Optional[Any]:
    if req.bounds is not None:
        return req.bounds
    if req.point_cloud is not None and hasattr(req.point_cloud, "bounds") and req.point_cloud.bounds is not None:
        return req.point_cloud.bounds
    if req.storage is not None and hasattr(req.storage, "bounds") and req.storage.bounds is not None:
        return req.storage.bounds
    if cloud_obj is not None and hasattr(cloud_obj, "get"):
        return cloud_obj.get("mtk_bounds")
    return None


def assemble_voxel_world(req: VoxelWorldAssemblyRequest) -> Dict[str, Any]:
    """
    Core authoritative assembler for Blender voxel worlds:
    1. Resolves/creates Root Empty Container & child Mesh Object
    2. Injects geometry via Dual-Channel Safe-Path/DNA Fast-Path
    3. Binds PBR / Mini-Atlas materials
    4. Synchronizes unculled companion Voxel Point Cloud
    5. Persists dynamic entity slot JSON across objects
    6. Registers in-memory caches (storage, colormaps) and primes tint types
    7. Stamps metadata custom properties and activates in viewport
    """

    context = req.context or getattr(bpy, "context", None)
    target_coll = req.target_collection or (
        context.collection
        if context and getattr(context, "collection", None)
        else (getattr(getattr(context, "scene", None), "collection", None) if context else None)
    )

    container_id = req.container_id or uuid.uuid4().hex

    # 1. Acquire or create Root Empty Container
    root_obj = req.target_root
    if root_obj is None and req.reuse_existing and hasattr(bpy, "data") and hasattr(bpy.data, "objects") and req.root_name in bpy.data.objects:
        cand = bpy.data.objects[req.root_name]
        if getattr(cand, "type", "") == "EMPTY":
            root_obj = cand

    if root_obj is None and req.container_type != "SYNC":
        root_obj = bpy.data.objects.new(req.root_name, None)
        if hasattr(root_obj, "empty_display_type"):
            root_obj.empty_display_type = "PLAIN_AXES"
        if hasattr(root_obj, "empty_display_size"):
            root_obj.empty_display_size = 1.0
        if target_coll is not None and hasattr(target_coll, "objects") and hasattr(target_coll.objects, "link"):
            try:
                target_coll.objects.link(root_obj)
            except Exception:
                pass

    if root_obj is not None:
        safe_set_custom_prop(root_obj, "mtk:is_container", True)
        safe_set_custom_prop(root_obj, "mtk:container_type", req.container_type)
        safe_set_custom_prop(root_obj, "mtk:container_id", str(container_id))
        safe_set_custom_prop(root_obj, "mtk:last_name", getattr(root_obj, "name", req.root_name))

        # Backward compatibility properties
        if req.container_type == "SAVE":
            safe_set_custom_prop(root_obj, "mozi_save_uuid", str(container_id))
        elif req.container_type == "SCENE":
            safe_set_custom_prop(root_obj, "mozi_scene_uuid", str(container_id))
            safe_set_custom_prop(root_obj, "mtk:is_scene_container", True)

    actual_root_name = getattr(root_obj, "name", req.root_name) if root_obj else req.root_name

    # 2. Acquire or create Child Mesh Object
    mesh_obj = req.target_mesh
    mesh_child_name = f"{actual_root_name}_Mesh" if req.container_type != "SYNC" else actual_root_name

    if mesh_obj is None:
        if req.reuse_existing and hasattr(bpy, "data") and hasattr(bpy.data, "objects") and mesh_child_name in bpy.data.objects:
            cand = bpy.data.objects[mesh_child_name]
            if getattr(cand, "type", "") == "MESH":
                mesh_obj = cand

        if mesh_obj is None:
            b_mesh = bpy.data.meshes.new(mesh_child_name)
            mesh_obj = bpy.data.objects.new(mesh_child_name, b_mesh)
            if root_obj is not None and hasattr(mesh_obj, "parent"):
                mesh_obj.parent = root_obj
            if target_coll is not None and hasattr(target_coll, "objects") and hasattr(target_coll.objects, "link"):
                try:
                    target_coll.objects.link(mesh_obj)
                except Exception:
                    pass
        else:
            b_mesh = mesh_obj.data
            if root_obj is not None and hasattr(mesh_obj, "parent") and mesh_obj.parent != root_obj:
                mesh_obj.parent = root_obj
    else:
        b_mesh = mesh_obj.data
        if root_obj is not None and hasattr(mesh_obj, "parent") and mesh_obj.parent != root_obj and root_obj != mesh_obj:
            mesh_obj.parent = root_obj

    if req.geometry_origin:
        stamp_geometry_origin(mesh_obj, req.geometry_origin, overwrite=True)

    if req.container_type == "SAVE":
        safe_set_custom_prop(mesh_obj, "mtk:is_save_mesh", True)
    elif req.container_type == "SCENE":
        safe_set_custom_prop(mesh_obj, "mtk:is_scene_mesh", True)

    # 3. Inject mesh geometry (Dual-Channel Safe-Path / DNA Fast-Path)
    t_inject = time.perf_counter()
    injector_fn = req.custom_injector or inject_mesh_data
    if req.mesh_data is not None and b_mesh is not None:
        injector_fn(
            req.mesh_data,
            b_mesh,
            update_topology=True,
            update_normals=True,
            skip_string_attributes=req.skip_string_attributes,
        )
    inject_ms = (time.perf_counter() - t_inject) * 1000.0

    # 4. Material Binding
    t_mat = time.perf_counter()
    materials = None
    if req.custom_material_binder is not None:
        materials = req.custom_material_binder(mesh_obj)
    elif req.mesh_data is not None and mesh_obj is not None:
        used_chunk_ids = req.mesh_data.used_materials()
        dyn_atlases = req.dynamic_entity_atlases
        if dyn_atlases is None:
            dyn_atlases = resolve_dynamic_entity_atlases(req.mesh_data)
        ensure_world_materials(
            mesh_obj,
            prefs=req.prefs,
            atlas=req.atlas,
            used_chunk_ids=used_chunk_ids,
            dynamic_entity_atlases=dyn_atlases,
            clean_unused_slots=req.clean_unused_material_slots,
        )
    materials_ms = (time.perf_counter() - t_mat) * 1000.0

    # 5. Extract and link Companion Voxel Point Cloud
    t_cloud = time.perf_counter()
    cloud_obj = None
    cloud_ensurer = req.custom_cloud_ensurer or ensure_voxel_child_cloud
    if req.sync_point_cloud and (req.storage is not None or req.point_cloud is not None):
        parent_target = root_obj if (root_obj and getattr(root_obj, "type", "") == "EMPTY") else mesh_obj
        if parent_target is not None:
            try:
                cloud_obj = cloud_ensurer(
                    parent_obj=parent_target,
                    storage=req.storage,
                    cloud_data=req.point_cloud,
                    origin_centered=req.origin_centered,
                    initial_hidden=True,
                )
            except Exception as e:
                logger.warning("Failed ensuring companion voxel child cloud: %s", e)

    if cloud_obj is not None:
        cloud_name = getattr(cloud_obj, "name", "")
        mesh_name = getattr(mesh_obj, "name", "")
        if root_obj is not None:
            safe_set_custom_prop(root_obj, "mtk_voxel_cloud", cloud_name)
        if mesh_obj is not None:
            safe_set_custom_prop(mesh_obj, "mtk_voxel_cloud", cloud_name)
            safe_set_custom_prop(cloud_obj, "mtk_world_mesh", mesh_name)
        if req.geometry_origin:
            stamp_geometry_origin(cloud_obj, req.geometry_origin, overwrite=True)

    cloud_ms = (time.perf_counter() - t_cloud) * 1000.0

    # 6. Dynamic Entity Slot Persistence
    slot_map = req.dynamic_entity_slots
    if slot_map is None and req.mesh_data is not None:
        slot_map = getattr(req.mesh_data, "dynamic_entity_slots", None)
    persist_dynamic_entity_slots([root_obj, mesh_obj, cloud_obj], slot_map)

    bounds = _resolve_world_bounds(req, cloud_obj)

    # 7. Metadata Custom Properties
    now_ts = time.time()
    targets = [t for t in (root_obj, mesh_obj, cloud_obj) if t is not None]
    for target in targets:
        safe_set_custom_prop(target, "mtk:container_id", str(container_id))
        safe_set_custom_prop(target, "mtk:origin_centered", bool(req.origin_centered))
        safe_set_custom_prop(target, "mtk_origin_centered", bool(req.origin_centered))
        safe_set_custom_prop(target, "mtk:imported_at", float(now_ts))
        safe_set_custom_prop(target, "mtk:biome_preset", "WORLD")
        if bounds and len(bounds) == 6:
            safe_set_custom_prop(target, "mtk_bounds", [int(x) for x in bounds])
        for k, v in req.metadata_props.items():
            safe_set_custom_prop(target, k, v)

    # 8. Register in-memory caches by immutable container_id and prime tint types
    container_id_str = str(container_id)
    if req.storage is not None:
        reg_fn = getattr(_biome_updater, "register_object_storage", None)
        if reg_fn is not None:
            reg_fn(container_id_str, req.storage)

    if req.colormaps is not None:
        reg_col_fn = getattr(_biome_updater, "register_object_colormaps", None)
        if reg_col_fn is not None:
            reg_col_fn(container_id_str, req.colormaps)

    if b_mesh is not None and mesh_obj is not None:
        prime_fn = getattr(_biome_updater, "resolve_mesh_base_tint_types", None)
        if prime_fn is not None:
            try:
                prime_fn(b_mesh, mesh_obj)
            except Exception:
                pass

    # 9. Continuous visual entities assembly via canonical EntityRenderPlan
    entity_objs = []
    render_plan = req.render_plan
    ents_palette = req.visual_entities

    if render_plan is None and ents_palette is None:
        if req.storage is not None and hasattr(req.storage, "has_visual_entities"):
            ents_palette = req.storage.get_visual_entities_palette() if req.storage.has_visual_entities() else []
        elif req.point_cloud is not None and hasattr(req.point_cloud, "has_visual_entities"):
            ents_palette = req.point_cloud.get_visual_entities_palette() if req.point_cloud.has_visual_entities() else []
        elif cloud_obj is not None:
            from ...bridge.point_cloud.constants import PROP_VISUAL_ENTITIES
            val = cloud_obj.get(PROP_VISUAL_ENTITIES)
            if isinstance(val, (list, tuple)):
                ents_palette = list(val)

    if render_plan is not None or (ents_palette is not None and len(ents_palette) > 0) or req.reuse_existing:
        try:
            entity_objs = _assemble_visual_entities(
                root_obj=root_obj,
                render_plan=render_plan,
                entities_palette=ents_palette,
                atlas=req.atlas,
                target_coll=target_coll,
                template_mesh_obj=mesh_obj,
                container_name=req.root_name,
                origin_centered=req.origin_centered,
                bounds=bounds,
                prefs=req.prefs,
                dynamic_entity_atlases=req.dynamic_entity_atlases,
                package_reader=req.package_reader,
            )
        except Exception as e:
            logger.warning("Failed assembling visual entities for container '%s': %s", req.root_name, e, exc_info=True)
            entity_objs = []

    # 10. Viewport activation
    active_target = root_obj or mesh_obj
    if active_target is not None and context and hasattr(context, "view_layer"):
        try:
            if hasattr(active_target, "select_set"):
                active_target.select_set(True)
            if hasattr(context.view_layer, "objects"):
                context.view_layer.objects.active = active_target
        except (RuntimeError, Exception):
            pass

    host_ms = inject_ms + materials_ms + cloud_ms

    return {
        "root_obj": root_obj,
        "mesh_obj": mesh_obj,
        "cloud_obj": cloud_obj,
        "entity_objs": entity_objs,
        "container_id": container_id,
        "materials": materials,
        "inject_ms": inject_ms,
        "materials_ms": materials_ms,
        "cloud_ms": cloud_ms,
        "host_ms": host_ms,
    }


def _assemble_visual_entities(
    root_obj: Any,
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
    render_plan: Optional[Any] = None,
) -> List[Any]:
    """
    Assembles continuous visual entities (such as Paintings) as standalone Blender mesh objects
    using the canonical EntityRenderPlan contract. Performs pure declarative reconciliation.
    """
    plan = render_plan
    if plan is None:
        if entities_palette is None:
            return []

        from ...bridge.engine import get_libmtk
        mtk = get_libmtk()
        if mtk is None:
            return []

        norm_bounds = [int(x) for x in bounds] if bounds and len(bounds) == 6 else None
        atlas_mapping_json = None
        if atlas is not None:
            if hasattr(atlas, "get_mapping_json"):
                atlas_mapping_json = atlas.get_mapping_json()
            elif hasattr(atlas, "mapping_json"):
                atlas_mapping_json = atlas.mapping_json

        try:
            plan = mtk.build_entity_render_plan(
                entities_palette,
                atlas=atlas,
                atlas_mapping_json=atlas_mapping_json,
                bounds=norm_bounds,
                origin_centered=origin_centered,
                coordinate_system="z_up",
                revision=0,
                authoritative=True,
            )
        except Exception as e:
            logger.warning("Failed building entity render plan from palette: %s", e)
            return []

    # 1. Resolve existing entities parent container
    entities_parent = None
    parent_name = f"{container_name}_Entities"
    if root_obj is not None and hasattr(bpy, "data"):
        for child in getattr(root_obj, "children", []):
            if child.name == parent_name or (hasattr(child, "get") and child.get("mtk:is_entities_container")):
                entities_parent = child
                break

    # Discover any existing visual entity objects in the container or root recursively
    def _collect_descendant_entities(parent: Any, found_set: set) -> List[Any]:
        results = []
        for child in getattr(parent, "children", []):
            if child not in found_set:
                found_set.add(child)
                if hasattr(child, "get") and child.get("mtk:is_visual_entity"):
                    results.append(child)
                results.extend(_collect_descendant_entities(child, found_set))
        return results

    found_objs = set()
    existing_entities = []
    if entities_parent is not None:
        existing_entities.extend(_collect_descendant_entities(entities_parent, found_objs))
    if root_obj is not None:
        existing_entities.extend(_collect_descendant_entities(root_obj, found_objs))

    def _destroy_entity_obj(obj: Any):
        mesh_to_delete = getattr(obj, "data", None)
        if hasattr(bpy, "data") and hasattr(bpy.data, "objects") and obj.name in bpy.data.objects:
            try:
                bpy.data.objects.remove(obj, do_unlink=True)
            except Exception:
                pass
        if mesh_to_delete is not None and hasattr(bpy, "data") and hasattr(bpy.data, "meshes") and mesh_to_delete.name in bpy.data.meshes:
            try:
                bpy.data.meshes.remove(mesh_to_delete, do_unlink=True)
            except Exception:
                pass

    if plan is None:
        return existing_entities

    if plan.node_count == 0:
        if plan.authoritative:
            for old_obj in existing_entities:
                _destroy_entity_obj(old_obj)
        return []

    # Map existing entities by stable key or UUID
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
        existing_by_key[_object_identity_key(obj)] = obj

    # Ensure parent container exists
    if entities_parent is None and root_obj is not None and hasattr(bpy, "data"):
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

    baked_objs = []
    for node in plan.nodes:
        reused_obj = existing_by_key.pop(node.key, None)
        if reused_obj is None and node.uuid:
            reused_obj = existing_by_key.pop(f"uuid:{node.uuid}", None)

        if reused_obj is not None and getattr(reused_obj, "data", None) is not None:
            # Diff update existing object
            try:
                reused_obj.location = node.location
                reused_obj.rotation_euler = node.rotation_euler
                reused_obj.scale = node.scale

                old_var = reused_obj.get("mtk:variant", "")
                if old_var != (node.variant or ""):
                    inject_mesh_data(node.mesh, reused_obj.data, update_topology=True, update_normals=True)

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

                baked_objs.append(reused_obj)
                continue
            except Exception as e:
                logger.debug("Failed diff-updating existing entity %s: %s, recreating", node.display_name, e)
                _destroy_entity_obj(reused_obj)

        # Create new entity object
        b_mesh = None
        ent_obj = None
        try:
            b_mesh = bpy.data.meshes.new(name=f"{node.display_name}_Mesh")
            ent_obj = bpy.data.objects.new(name=node.display_name, object_data=b_mesh)

            inject_mesh_data(node.mesh, b_mesh, update_topology=True, update_normals=True)

            ent_obj.location = node.location
            ent_obj.rotation_euler = node.rotation_euler
            ent_obj.scale = node.scale

            from ..materials.builder.world_binding import ensure_world_materials
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

            baked_objs.append(ent_obj)
        except Exception as e:
            logger.warning("Failed assembling visual entity '%s': %s", node.display_name, e, exc_info=True)
            if ent_obj is not None:
                _destroy_entity_obj(ent_obj)
            elif b_mesh is not None and hasattr(bpy, "data") and hasattr(bpy.data, "meshes") and b_mesh.name in bpy.data.meshes:
                try:
                    bpy.data.meshes.remove(b_mesh, do_unlink=True)
                except Exception:
                    pass

    # Diff cleanup: destroy stale entities when plan is authoritative
    if plan.authoritative:
        for stale_obj in existing_by_key.values():
            _destroy_entity_obj(stale_obj)

    return baked_objs


__all__ = [
    "safe_set_custom_prop",
    "safe_get_custom_prop",
    "persist_dynamic_entity_slots",
    "VoxelWorldAssemblyRequest",
    "assemble_voxel_world",
]
