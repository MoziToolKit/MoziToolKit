"""
Integration tests for Item Frame and Glow Item Frame procedural mesh generation & 6-DOF placement.
Executed strictly in real Blender 5.x LTS host environment without mocks.
"""

import math
import unittest
import bpy

from MoziToolKit.bridge.engine import get_libmtk
from MoziToolKit.bridge.scene import build_entity_render_plan
from MoziToolKit.operators.mesh.rebuild_targets import resolve_mesh_rebuild_targets
from MoziToolKit.utils.entities import reconcile_entity_render_plan
from MoziToolKit.utils.entities.local_ops import apply_entity_delta
from MoziToolKit.utils.world.sync_container import (
    get_or_create_world_container,
    get_or_create_world_mesh_object,
    is_yefira_root_object,
    is_yefira_world_object,
    resolve_world_root_object,
    update_world_mesh,
)


class TestItemFrameRenderPlan(unittest.TestCase):
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

    def test_01_item_frame_all_six_facings_render_plan(self):
        """Item frames support 6 orientations: North, South, West, East, Up (Floor), and Down (Ceiling)."""
        facings = ["north", "south", "west", "east", "up", "down"]
        entities = []
        for i, facing in enumerate(facings):
            entities.append({
                "id": "minecraft:item_frame",
                "uuid": f"00000000-0000-0000-0000-00000000000{i}",
                "position": [10.5, 64.0, 20.0 + i],
                "facing": facing,
                "kind": {
                    "type": "minecraft:item_frame",
                    "item_id": "minecraft:diamond_sword",
                    "rotation": i % 8,
                    "is_glowing": (facing == "up"),
                    "is_invisible": (facing == "down"),
                },
            })

        plan = build_entity_render_plan(
            entities,
            coordinate_system="z_up",
            origin_centered=False,
        )

        self.assertIsNotNone(plan)
        self.assertEqual(plan.node_count, 6)
        self.assertEqual(len(plan.warnings), 0)

        # Create root container in Blender
        root_obj = bpy.data.objects.new("TestWorld_Root", None)
        bpy.context.scene.collection.objects.link(root_obj)

        result = reconcile_entity_render_plan(root_obj, plan=plan)
        self.assertEqual(len(result.created), 6)

        # Inspect created Blender objects
        created_by_facing = {}
        for obj in result.created:
            self.assertTrue(obj.get("mtk:is_visual_entity", False))
            self.assertEqual(obj.get("mtk:entity_id"), "minecraft:item_frame")
            facing = obj.get("mtk:facing")
            self.assertIn(facing, facings)
            created_by_facing[facing] = obj

        # 1. Floor item frame (Up): points straight Up in Blender (+Z)
        floor_obj = created_by_facing["up"]
        self.assertEqual(floor_obj.type, "MESH")
        self.assertGreater(len(floor_obj.data.polygons), 0)
        self.assertGreater(len(floor_obj.data.vertices), 0)
        # Location in Blender Z-up: X=10.5, Y=-(20.0+4)= -24.0, Z=64.0
        self.assertAlmostEqual(floor_obj.location.x, 10.5, places=3)
        self.assertAlmostEqual(floor_obj.location.y, -24.0, places=3)
        self.assertAlmostEqual(floor_obj.location.z, 64.0, places=3)

        # 2. Ceiling item frame (Down): Invisible frame has 0 polygons, but retains Object transform
        ceiling_obj = created_by_facing["down"]
        self.assertEqual(ceiling_obj.type, "MESH")
        self.assertEqual(len(ceiling_obj.data.polygons), 0)
        self.assertAlmostEqual(ceiling_obj.location.x, 10.5, places=3)
        self.assertAlmostEqual(ceiling_obj.location.y, -25.0, places=3)
        self.assertAlmostEqual(ceiling_obj.location.z, 64.0, places=3)

        # 3. Horizontal wall frame (North)
        north_obj = created_by_facing["north"]
        self.assertEqual(north_obj.type, "MESH")
        self.assertGreater(len(north_obj.data.polygons), 0)
        self.assertGreater(len(north_obj.data.vertices), 0)

    def test_02_glow_item_frame_identity_and_reconciliation(self):
        """Glow item frames retain glowing state and procedural mesh topology."""
        entity = {
            "id": "minecraft:glow_item_frame",
            "uuid": "ffffffff-ffff-ffff-ffff-ffffffffffff",
            "position": [0.0, 64.0, 0.0],
            "facing": "south",
            "kind": {
                "type": "minecraft:item_frame",
                "item_id": "minecraft:clock",
                "rotation": 0,
                "is_glowing": True,
                "is_invisible": False,
            },
        }

        plan = build_entity_render_plan([entity], coordinate_system="z_up", origin_centered=False)
        self.assertEqual(plan.node_count, 1)

        root_obj = bpy.data.objects.new("TestGlow_Root", None)
        bpy.context.scene.collection.objects.link(root_obj)

        result = reconcile_entity_render_plan(root_obj, plan=plan)
        self.assertEqual(len(result.created), 1)
        glow_obj = result.created[0]
        self.assertEqual(glow_obj.get("mtk:entity_id"), "minecraft:glow_item_frame")
        self.assertGreater(len(glow_obj.data.polygons), 0)

    def test_03_active_item_frame_never_hijacked_as_world_mesh(self):
        """Active item frame selection must never be returned or hijacked as the sync world mesh."""
        root = get_or_create_world_container(bpy.context, target_name="Yefira_World")
        world_mesh = get_or_create_world_mesh_object(bpy.context, root_container=root)
        self.assertEqual(world_mesh.name, "Yefira_World_Mesh")

        # Create an item frame under the root container
        entity = {
            "id": "minecraft:item_frame",
            "uuid": "aaaaaaaa-1111-2222-3333-444444444444",
            "position": [0.0, 64.0, 0.0],
            "facing": "north",
            "kind": {
                "type": "minecraft:item_frame",
                "item_id": "minecraft:compass",
                "rotation": 2,
                "is_glowing": False,
                "is_invisible": False,
            },
        }
        plan = build_entity_render_plan([entity], coordinate_system="z_up", origin_centered=False)
        reconcile_res = reconcile_entity_render_plan(root, plan=plan)
        self.assertEqual(len(reconcile_res.created), 1)
        item_frame_obj = reconcile_res.created[0]
        entities_container = item_frame_obj.parent

        self.assertIsNotNone(entities_container)
        self.assertEqual(entities_container.name, "World_Entities")

        # Hierarchy & ownership assertions
        self.assertTrue(is_yefira_root_object(root))
        self.assertFalse(is_yefira_root_object(entities_container))
        self.assertFalse(is_yefira_root_object(item_frame_obj))
        self.assertFalse(is_yefira_world_object(item_frame_obj))
        self.assertEqual(resolve_world_root_object(item_frame_obj), root)
        self.assertEqual(resolve_world_root_object(entities_container), root)

        # Simulate user selecting the item frame object in Blender viewport
        bpy.context.view_layer.objects.active = item_frame_obj
        self.assertEqual(bpy.context.active_object, item_frame_obj)

        # 1. get_or_create_world_mesh_object with root_container MUST return world_mesh, not item_frame_obj
        resolved_mesh = get_or_create_world_mesh_object(bpy.context, root_container=root)
        self.assertEqual(resolved_mesh, world_mesh)
        self.assertNotEqual(resolved_mesh, item_frame_obj)

        # 2. resolve_mesh_rebuild_targets MUST resolve to root and world_mesh, not item_frame_obj
        r_root, r_mesh, r_cloud, r_type = resolve_mesh_rebuild_targets(bpy.context)
        self.assertEqual(r_root, root)
        self.assertEqual(r_mesh, world_mesh)
        self.assertNotEqual(r_mesh, item_frame_obj)
        self.assertEqual(r_type, "SYNC")

        # 3. Defensive check: update_world_mesh MUST refuse if called on an entity
        dummy_mesh_data = None
        v_count, f_count = update_world_mesh(item_frame_obj, dummy_mesh_data)
        self.assertEqual((v_count, f_count), (0, 0))

    def test_04_rapid_item_frame_mutation_does_not_corrupt_world_mesh(self):
        """Rapid rotations, item placements, removals, and replacements never hijack the terrain mesh."""
        root = get_or_create_world_container(bpy.context, target_name="Yefira_World")
        world_mesh = get_or_create_world_mesh_object(bpy.context, root_container=root)

        # Perform 8 rapid rotations of an item frame (simulating player spamming right click)
        for rot in range(8):
            ent = {
                "id": "minecraft:item_frame",
                "uuid": "bbbbbbbb-2222-3333-4444-555555555555",
                "position": [5.0, 64.0, 5.0],
                "facing": "east",
                "kind": {
                    "type": "minecraft:item_frame",
                    "item_id": "minecraft:iron_sword",
                    "rotation": rot,
                    "is_glowing": False,
                    "is_invisible": False,
                },
            }
            res = apply_entity_delta(root, upsert_entities=[ent])
            self.assertEqual(len(res.all_entities), 1)
            active_frame = res.all_entities[0]
            # Ensure the frame stays parented under World_Entities and world_mesh is untouched
            self.assertEqual(active_frame.parent.name, "World_Entities")
            self.assertNotEqual(active_frame, world_mesh)
            self.assertEqual(world_mesh.name, "Yefira_World_Mesh")
            self.assertEqual(world_mesh.parent, root)

        # Break item frame (delete)
        res_del = apply_entity_delta(root, delete_keys=["bbbbbbbb-2222-3333-4444-555555555555"])
        self.assertIn("bbbbbbbb-2222-3333-4444-555555555555", res_del.deleted)
        self.assertEqual(len(res_del.all_entities), 0)

        # Re-place item frame (new UUID)
        new_ent = {
            "id": "minecraft:glow_item_frame",
            "uuid": "cccccccc-3333-4444-5555-666666666666",
            "position": [5.0, 64.0, 5.0],
            "facing": "up",
            "kind": {
                "type": "minecraft:item_frame",
                "item_id": "minecraft:golden_apple",
                "rotation": 0,
                "is_glowing": True,
                "is_invisible": False,
            },
        }
        res_add = apply_entity_delta(root, upsert_entities=[new_ent])
        self.assertEqual(len(res_add.all_entities), 1)
        new_frame = res_add.all_entities[0]
        self.assertTrue(new_frame.get("mtk:is_glowing"))
        self.assertEqual(world_mesh.name, "Yefira_World_Mesh")
        self.assertEqual(world_mesh.parent, root)


if __name__ == "__main__":
    unittest.main()
