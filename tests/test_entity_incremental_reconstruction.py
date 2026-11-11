# coding: utf-8
"""
Tests for ERP (EntityRenderPlan) local and incremental reconstruction in MoziToolKit.

Verifies:
1. Single entity local update (Transform-Only fast path: direct coordinate assignment, zero mesh reinjection).
2. Single entity variant/dimension change (Mesh-Update path: in-place mesh data injection without destroying hierarchy).
3. Batch incremental upsert without full scene rebuild (O(K) additive additions and updates).
4. Targeted deletion by key/UUID (physical removal of Object & Mesh block without touching remaining entities).
5. Direct transform fast path (update_entity_transform).
6. Deletion-only delta plan via build_entity_render_plan(None, deleted_keys=[...], authoritative=False).
7. Automatic companion point cloud palette synchronization (mtk_visual_entities).
"""

import json
import unittest
import bpy

from MoziToolKit.bridge.engine import get_libmtk
from MoziToolKit.bridge.point_cloud.constants import PROP_VISUAL_ENTITIES
from MoziToolKit.utils.world.assembler import (
    VoxelWorldAssemblyRequest,
    assemble_voxel_world,
)
from MoziToolKit.utils.entities import (
    apply_entity_delta,
    delete_entities_by_keys,
    delete_single_entity,
    find_entity_object,
    get_or_create_entities_container,
    reconcile_entity_render_plan,
    update_entity_transform,
    update_single_entity,
)


class TestEntityIncrementalReconstruction(unittest.TestCase):
    def setUp(self):
        self.mtk = get_libmtk()
        if self.mtk is None:
            self.skipTest("libmtk_py is not available in test environment")

        # Clean scene objects and meshes before each test
        for obj in list(bpy.data.objects):
            try:
                bpy.data.objects.remove(obj, do_unlink=True)
            except Exception:
                pass
        for m in list(bpy.data.meshes):
            try:
                bpy.data.meshes.remove(m, do_unlink=True)
            except Exception:
                pass

    def _create_sample_world(self, container_name="TestWorld", paintings=None):
        """Helper to create an initial test container with terrain mesh and visual entities."""
        storage = self.mtk.VoxelStorage()
        storage.set_bounds(0, 0, 0, 16, 16, 16)
        storage.set_block(0, 0, 0, "minecraft:stone")

        palette = [json.dumps(p) for p in (paintings or [])]
        storage.set_visual_entities_palette(palette)

        cfg = self.mtk.MesherConfig(origin_centered=True)
        cloud = storage.to_point_cloud(cfg)
        mesh_data = self.mtk.MeshData()

        req = VoxelWorldAssemblyRequest(
            container_type="SAVE",
            root_name=container_name,
            mesh_data=mesh_data,
            storage=storage,
            point_cloud=cloud,
            origin_centered=True,
            bounds=[0, 0, 0, 16, 16, 16],
            visual_entities=storage.get_visual_entities_palette(),
            reuse_existing=False,
        )
        res = assemble_voxel_world(req)
        return res

    def test_01_single_entity_local_update_transform_only(self):
        """When an entity moves/rotates without changing variant, transform is updated in-place without mesh re-injection."""
        p1 = {
            "id": "minecraft:painting",
            "uuid": "11111111-1111-1111-1111-111111111111",
            "position": [0.5, 1.0, 0.0],
            "facing": "north",
            "rotation": [0.0, 0.0, 0.0],
            "kind": {"type": "minecraft:painting", "variant": "kebab", "width": 1, "height": 1},
        }
        res = self._create_sample_world("World_TransformOnly", paintings=[p1])
        root_obj = res["root_obj"]
        cloud_obj = res["cloud_obj"]
        ent_objs = res.get("entity_objs", [])
        self.assertEqual(len(ent_objs), 1)

        ent_obj = ent_objs[0]
        orig_mesh_name = ent_obj.data.name
        orig_location = tuple(ent_obj.location)

        # Move painting by shifting position to [2.5, 3.0, 0.0]
        updated_p1 = dict(p1)
        updated_p1["position"] = [2.5, 3.0, 0.0]

        updated_obj = update_single_entity(root_obj, updated_p1)
        self.assertIsNotNone(updated_obj)
        self.assertEqual(updated_obj.name, ent_obj.name)

        # 1. Transform was modified
        new_location = tuple(updated_obj.location)
        self.assertNotEqual(new_location, orig_location)

        # 2. Mesh data block was preserved (not reallocated)
        self.assertEqual(updated_obj.data.name, orig_mesh_name)

        # 3. Companion cloud palette is synchronized
        cloud_palette = cloud_obj.get(PROP_VISUAL_ENTITIES)
        self.assertIsInstance(cloud_palette, list)
        self.assertEqual(len(cloud_palette), 1)
        saved_ent = json.loads(cloud_palette[0])
        self.assertEqual(saved_ent["position"], [2.5, 3.0, 0.0])

    def test_02_single_entity_local_update_variant_mesh_reinjected(self):
        """When painting variant changes from kebab (1x1) to pointer (4x4), mesh is reinjected into existing object."""
        p1 = {
            "id": "minecraft:painting",
            "uuid": "22222222-2222-2222-2222-222222222222",
            "position": [2.0, 2.0, 0.0],
            "facing": "north",
            "rotation": [0.0, 0.0, 0.0],
            "kind": {"type": "minecraft:painting", "variant": "kebab", "width": 1, "height": 1},
        }
        res = self._create_sample_world("World_VariantChange", paintings=[p1])
        root_obj = res["root_obj"]
        ent_obj = res["entity_objs"][0]
        kebab_poly_count = len(ent_obj.data.polygons)

        # Change variant to pointer (4x4 tiles = multiple quads/faces)
        updated_p1 = dict(p1)
        updated_p1["kind"] = {"type": "minecraft:painting", "variant": "pointer", "width": 4, "height": 4}

        updated_obj = update_single_entity(root_obj, updated_p1)
        self.assertIsNotNone(updated_obj)
        self.assertEqual(updated_obj.name, ent_obj.name)
        self.assertEqual(updated_obj.get("mtk:variant"), "pointer")
        self.assertEqual(updated_obj.get("mtk:width"), 4)
        self.assertEqual(updated_obj.get("mtk:height"), 4)

        # Mesh data was reinjected with new topology
        pointer_poly_count = len(updated_obj.data.polygons)
        self.assertGreater(pointer_poly_count, 0)
        self.assertNotEqual(pointer_poly_count, kebab_poly_count)

    def test_03_batch_incremental_upsert_without_rebuilding_scene(self):
        """apply_entity_delta adds new entities and updates existing ones in O(K) without touching the terrain mesh."""
        p1 = {
            "id": "minecraft:painting",
            "uuid": "33333333-3333-3333-3333-333333333333",
            "position": [1.0, 1.0, 0.0],
            "facing": "north",
            "rotation": [0.0, 0.0, 0.0],
            "kind": {"type": "minecraft:painting", "variant": "kebab", "width": 1, "height": 1},
        }
        res = self._create_sample_world("World_BatchUpsert", paintings=[p1])
        root_obj = res["root_obj"]
        mesh_obj = res["mesh_obj"]
        mesh_obj_name = mesh_obj.name

        # Modify p1 and add p2
        mod_p1 = dict(p1)
        mod_p1["position"] = [1.5, 1.5, 0.0]

        new_p2 = {
            "id": "minecraft:painting",
            "uuid": "44444444-4444-4444-4444-444444444444",
            "position": [5.0, 2.0, 0.0],
            "facing": "north",
            "rotation": [0.0, 0.0, 0.0],
            "kind": {"type": "minecraft:painting", "variant": "alban", "width": 1, "height": 1},
        }

        delta_res = apply_entity_delta(root_obj, upsert_entities=[mod_p1, new_p2])
        self.assertEqual(len(delta_res.created), 1)
        self.assertEqual(len(delta_res.updated), 1)
        self.assertEqual(len(delta_res.deleted), 0)
        self.assertEqual(len(delta_res.all_entities), 2)

        # Ensure terrain mesh was untouched
        self.assertIn(mesh_obj_name, bpy.data.objects)
        self.assertEqual(bpy.data.objects[mesh_obj_name], mesh_obj)

        # Verify companion cloud has both entities
        cloud_obj = res["cloud_obj"]
        palette = cloud_obj.get(PROP_VISUAL_ENTITIES)
        self.assertEqual(len(palette), 2)

    def test_04_targeted_entity_deletion_by_key(self):
        """delete_single_entity and delete_entities_by_keys physically delete Object and Mesh without touching survivors."""
        p1 = {
            "id": "minecraft:painting",
            "uuid": "55555555-5555-5555-5555-555555555555",
            "position": [1.0, 1.0, 0.0],
            "facing": "north",
            "rotation": [0.0, 0.0, 0.0],
            "kind": {"type": "minecraft:painting", "variant": "kebab", "width": 1, "height": 1},
        }
        p2 = {
            "id": "minecraft:painting",
            "uuid": "66666666-6666-6666-6666-666666666666",
            "position": [3.0, 1.0, 0.0],
            "facing": "north",
            "rotation": [0.0, 0.0, 0.0],
            "kind": {"type": "minecraft:painting", "variant": "alban", "width": 1, "height": 1},
        }
        res = self._create_sample_world("World_TargetedDelete", paintings=[p1, p2])
        root_obj = res["root_obj"]
        ent_objs = res.get("entity_objs", [])
        self.assertEqual(len(ent_objs), 2)

        p1_obj = find_entity_object(root_obj, "55555555-5555-5555-5555-555555555555")
        p2_obj = find_entity_object(root_obj, "66666666-6666-6666-6666-666666666666")
        self.assertIsNotNone(p1_obj)
        self.assertIsNotNone(p2_obj)

        p1_obj_name = p1_obj.name
        p1_mesh_name = p1_obj.data.name
        p2_obj_name = p2_obj.name

        # Delete painting 1 only
        ok = delete_single_entity(root_obj, "uuid:55555555-5555-5555-5555-555555555555")
        self.assertTrue(ok)

        # 1. Painting 1 object and mesh are physically removed from bpy.data
        self.assertNotIn(p1_obj_name, bpy.data.objects)
        self.assertNotIn(p1_mesh_name, bpy.data.meshes)

        # 2. Painting 2 remains untouched
        self.assertIn(p2_obj_name, bpy.data.objects)

        # 3. Companion cloud palette only contains painting 2
        cloud_obj = res["cloud_obj"]
        palette = cloud_obj.get(PROP_VISUAL_ENTITIES)
        self.assertEqual(len(palette), 1)
        self.assertEqual(json.loads(palette[0])["uuid"], "66666666-6666-6666-6666-666666666666")

    def test_05_update_entity_transform_direct_fast_path(self):
        """update_entity_transform sets location, rotation, and scale directly in Blender space (O(1))."""
        p1 = {
            "id": "minecraft:painting",
            "uuid": "77777777-7777-7777-7777-777777777777",
            "position": [0.0, 0.0, 0.0],
            "facing": "north",
            "rotation": [0.0, 0.0, 0.0],
            "kind": {"type": "minecraft:painting", "variant": "kebab", "width": 1, "height": 1},
        }
        res = self._create_sample_world("World_DirectTransform", paintings=[p1])
        root_obj = res["root_obj"]
        ent_obj = res["entity_objs"][0]

        update_entity_transform(
            ent_obj,
            location=(10.0, 20.0, 30.0),
            rotation_euler=(0.1, 0.2, 0.3),
            scale=(2.0, 2.0, 2.0),
        )

        self.assertAlmostEqual(ent_obj.location.x, 10.0)
        self.assertAlmostEqual(ent_obj.location.y, 20.0)
        self.assertAlmostEqual(ent_obj.location.z, 30.0)
        self.assertAlmostEqual(ent_obj.rotation_euler.x, 0.1)
        self.assertAlmostEqual(ent_obj.rotation_euler.y, 0.2)
        self.assertAlmostEqual(ent_obj.rotation_euler.z, 0.3)
        self.assertAlmostEqual(ent_obj.scale.x, 2.0)

    def test_06_delta_plan_with_empty_entities_and_deleted_keys(self):
        """Directly compiling an ERP with entities=None and deleted_keys purges matching entities via reconcile_entity_render_plan."""
        p1 = {
            "id": "minecraft:painting",
            "uuid": "88888888-8888-8888-8888-888888888888",
            "position": [0.5, 1.0, 0.0],
            "facing": "north",
            "rotation": [0.0, 0.0, 0.0],
            "kind": {"type": "minecraft:painting", "variant": "kebab", "width": 1, "height": 1},
        }
        res = self._create_sample_world("World_DeltaPlanDel", paintings=[p1])
        root_obj = res["root_obj"]
        ent_obj = res["entity_objs"][0]
        ent_name = ent_obj.name

        # Build deletion-only delta plan
        from MoziToolKit.bridge.scene import build_entity_render_plan
        plan = build_entity_render_plan(
            entities=None,
            deleted_keys=["uuid:88888888-8888-8888-8888-888888888888"],
            authoritative=False,
            bounds=[0, 0, 0, 16, 16, 16],
            origin_centered=True,
        )
        self.assertEqual(plan.node_count, 0)
        self.assertEqual(plan.deleted_keys, ["uuid:88888888-8888-8888-8888-888888888888"])
        self.assertFalse(plan.authoritative)

        reconcile_res = reconcile_entity_render_plan(root_obj, plan=plan)
        self.assertEqual(len(reconcile_res.deleted), 1)
        self.assertNotIn(ent_name, bpy.data.objects)

    def test_07_find_entity_object_and_container_discovery(self):
        """find_entity_object resolves entities by raw UUID, uuid: prefix, and key; get_or_create_entities_container creates if absent."""
        p1 = {
            "id": "minecraft:painting",
            "uuid": "99999999-9999-9999-9999-999999999999",
            "position": [0.5, 1.0, 0.0],
            "facing": "north",
            "rotation": [0.0, 0.0, 0.0],
            "kind": {"type": "minecraft:painting", "variant": "kebab", "width": 1, "height": 1},
        }
        res = self._create_sample_world("World_Lookup", paintings=[p1])
        root_obj = res["root_obj"]

        # 1. Lookup by uuid
        o1 = find_entity_object(root_obj, "99999999-9999-9999-9999-999999999999")
        self.assertIsNotNone(o1)

        # 2. Lookup by uuid: prefix
        o2 = find_entity_object(root_obj, "uuid:99999999-9999-9999-9999-999999999999")
        self.assertEqual(o1, o2)

        # 3. Lookup non-existent returns None
        o_none = find_entity_object(root_obj, "uuid:non-existent")
        self.assertIsNone(o_none)

        # 4. get_or_create_entities_container returns existing container
        container = get_or_create_entities_container(root_obj, "World_Lookup")
        self.assertIsNotNone(container)
        self.assertTrue(container.get("mtk:is_entities_container"))


if __name__ == "__main__":
    unittest.main()
