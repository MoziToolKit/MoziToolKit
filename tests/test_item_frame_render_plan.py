"""
Integration tests for Item Frame and Glow Item Frame procedural mesh generation & 6-DOF placement.
Executed strictly in real Blender 5.x LTS host environment without mocks.
"""

import json
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

    def test_05_item_frame_with_held_item_procedural_mesh(self):
        """Item frames with held items fuse item models and bind item materials in Blender host."""
        import glob
        import os

        cache_files = glob.glob(os.path.join(os.getcwd(), ".ci-cache", "*.mtkcache"))
        if not cache_files:
            self.skipTest("No .ci-cache asset bundle found for full procedural item fusion")

        cache = self.mtk.AssetCache.open(cache_files[0])
        try:
            models = cache.load_models()
            atlas = cache.load_atlas()
        except Exception:
            self.skipTest("Failed loading models or atlas from cache")

        # 1. Build an empty item frame plan
        empty_frame_ent = {
            "id": "minecraft:item_frame",
            "uuid": "00000000-0000-0000-0000-000000000001",
            "position": [0.0, 64.0, 0.0],
            "facing": "north",
            "kind": {
                "type": "minecraft:item_frame",
                "item_id": "",
                "rotation": 0,
                "is_glowing": False,
                "is_invisible": False,
            },
        }
        empty_plan = build_entity_render_plan([empty_frame_ent], atlas=atlas, models=models)
        empty_node = empty_plan.nodes[0]

        # 2. Build an item frame with a diamond sword
        sword_frame_ent = {
            "id": "minecraft:item_frame",
            "uuid": "00000000-0000-0000-0000-000000000002",
            "position": [0.0, 64.0, 0.0],
            "facing": "north",
            "kind": {
                "type": "minecraft:item_frame",
                "item_id": "minecraft:diamond_sword",
                "rotation": 2,
                "is_glowing": False,
                "is_invisible": False,
            },
        }
        sword_plan = build_entity_render_plan([sword_frame_ent], atlas=atlas, models=models)
        self.assertEqual(len(sword_plan.warnings), 0)
        sword_node = sword_plan.nodes[0]

        # Mesh face count with sword must be strictly greater than empty frame
        self.assertGreater(sword_node.mesh.face_count, empty_node.mesh.face_count)

        # 3. Reconcile into real Blender scene
        root = bpy.data.objects.new("TestWorld_Root", None)
        bpy.context.scene.collection.objects.link(root)

        res = reconcile_entity_render_plan(root, plan=sword_plan, atlas=atlas)
        self.assertEqual(len(res.created), 1)

        sword_frame_obj = res.created[0]
        self.assertEqual(sword_frame_obj.type, "MESH")
        self.assertEqual(sword_frame_obj.get("mtk:item_id"), "minecraft:diamond_sword")
        self.assertEqual(sword_frame_obj.get("mtk:item_rotation"), 2)

        # Polygons in Blender Mesh must be greater than empty frame
        self.assertGreater(len(sword_frame_obj.data.polygons), empty_node.mesh.face_count)

        # Materials assigned must include item frame and diamond sword routes
        self.assertGreater(len(sword_frame_obj.material_slots), 0)

    def test_06_e2e_assemble_voxel_world_with_item_frame(self):
        """assemble_voxel_world correctly builds visual entities with items via auto-resolved model database."""
        from MoziToolKit.utils.world.assembler import VoxelWorldAssemblyRequest, assemble_voxel_world

        libmtk = get_libmtk()
        storage = libmtk.VoxelStorage()

        ent = {
            "id": "minecraft:item_frame",
            "uuid": "00000000-0000-0000-0000-000000000006",
            "position": [0.0, 64.0, 0.0],
            "facing": "north",
            "kind": {
                "type": "minecraft:item_frame",
                "item_id": "minecraft:diamond_sword",
                "count": 1,
                "rotation": 0,
                "is_glowing": False,
                "is_invisible": False,
            },
        }

        # Empty mesh data
        mesh_data = libmtk.MeshData()

        req = VoxelWorldAssemblyRequest(
            container_type="SAVE",
            root_name="TestE2E_Assembly",
            mesh_data=mesh_data,
            storage=storage,
            visual_entities=[json.dumps(ent)],
            bounds=[0, 0, 0, 16, 64, 16],
            origin_centered=True,
            reuse_existing=False,
        )

        res = assemble_voxel_world(req)
        entity_objs = res["entity_objs"]
        self.assertEqual(len(entity_objs), 1)

        ent_obj = entity_objs[0]
        self.assertEqual(ent_obj.get("mtk:item_id"), "minecraft:diamond_sword")
        # Empty frame is 22 polygons, frame + sword has > 100 polygons
        self.assertGreater(len(ent_obj.data.polygons), 22)
        self.assertEqual(len(ent_obj.data.polygons), 124)

    def test_07_e2e_save_import_constructs_item_frame_with_item(self):
        """apply_imported_save_to_blender populates item frames with items into Blender hierarchy."""
        from MoziToolKit.utils.save.hierarchy import apply_imported_save_to_blender

        libmtk = get_libmtk()
        storage = libmtk.VoxelStorage()

        ent = {
            "id": "minecraft:item_frame",
            "uuid": "00000000-0000-0000-0000-000000000007",
            "position": [2.0, 64.0, 2.0],
            "facing": "south",
            "kind": {
                "type": "minecraft:item_frame",
                "item_id": "minecraft:golden_apple",
                "count": 1,
                "rotation": 1,
                "is_glowing": False,
                "is_invisible": False,
            },
        }
        storage.set_visual_entities_palette([json.dumps(ent)])

        mesh_data = libmtk.MeshData()
        meta = {
            "level_name": "TestSaveWorld",
            "version_name": "1.21",
            "data_version": 3837,
            "spawn": [0, 64, 0],
            "time": 1000,
            "day_time": 1000,
        }

        root_obj, stats = apply_imported_save_to_blender(
            mesh_data=mesh_data,
            meta=meta,
            storage=storage,
            elapsed_ms=10.0,
            world_dir="/tmp/test_world",
            dimension="overworld",
            min_block=(0, 0, 0),
            max_block=(16, 64, 16),
            origin_centered=True,
            reuse_existing=False,
        )

        self.assertIsNotNone(root_obj)
        # Find visual entity child
        children = list(root_obj.children)
        entities_containers = [c for c in children if c.get("mtk:is_entities_container") or c.name.endswith("_Entities")]
        self.assertEqual(len(entities_containers), 1)
        ent_container = entities_containers[0]

        frame_objs = [c for c in ent_container.children if c.get("mtk:is_visual_entity")]
        self.assertEqual(len(frame_objs), 1)
        frame_obj = frame_objs[0]

        self.assertEqual(frame_obj.get("mtk:item_id"), "minecraft:golden_apple")
        # Must contain golden apple elements (faces > 22)
        self.assertGreater(len(frame_obj.data.polygons), 22)

    def test_08_e2e_live_sync_delta_constructs_item_frame_with_item(self):
        """apply_entity_delta adds and updates item frames with items into active container."""
        libmtk = get_libmtk()

        root_obj = bpy.data.objects.new("TestSyncWorld_Root", None)
        bpy.context.scene.collection.objects.link(root_obj)

        ent = {
            "id": "minecraft:glow_item_frame",
            "uuid": "00000000-0000-0000-0000-000000000008",
            "position": [0.0, 64.0, 0.0],
            "facing": "east",
            "kind": {
                "type": "minecraft:item_frame",
                "item_id": "minecraft:diamond_sword",
                "count": 1,
                "rotation": 3,
                "is_glowing": True,
                "is_invisible": False,
            },
        }

        res = apply_entity_delta(
            root_or_container=root_obj,
            upsert_entities=[ent],
            origin_centered=True,
            bounds=[0, 0, 0, 16, 64, 16],
        )

        self.assertEqual(len(res.created), 1)
        glow_frame_obj = res.created[0]
        self.assertEqual(glow_frame_obj.get("mtk:item_id"), "minecraft:diamond_sword")
        self.assertEqual(glow_frame_obj.get("mtk:is_glowing"), True)
        self.assertEqual(len(glow_frame_obj.data.polygons), 124)

    def test_09_item_frame_with_block_scaling_and_biome_tint(self):
        """Item frames holding block models (e.g. grass_block) have 0.25 total scale and active biome tint attributes."""
        import glob
        import os
        from MoziToolKit.bridge.mesh.attribute_schema import ATTR_BIOME_TINT_DATA, ATTR_BIOME_TINT_COLOR

        cache_files = glob.glob(os.path.join(os.getcwd(), ".ci-cache", "*.mtkcache"))
        if not cache_files:
            self.skipTest("No .ci-cache asset bundle found for full procedural item fusion")

        cache = self.mtk.AssetCache.open(cache_files[0])
        try:
            models = cache.load_models()
            atlas = cache.load_atlas()
        except Exception:
            self.skipTest("Failed loading models or atlas from cache")

        ent = {
            "id": "minecraft:item_frame",
            "uuid": "00000000-0000-0000-0000-000000000009",
            "position": [0.0, 64.0, 0.0],
            "facing": "north",
            "kind": {
                "type": "minecraft:item_frame",
                "item_id": "minecraft:grass_block",
                "count": 1,
                "rotation": 0,
                "is_glowing": False,
                "is_invisible": False,
            },
        }

        plan = build_entity_render_plan([ent], atlas=atlas, models=models)
        self.assertEqual(len(plan.warnings), 0)
        self.assertEqual(len(plan.nodes), 1)
        node = plan.nodes[0]

        root = bpy.data.objects.new("TestGrassFrame_Root", None)
        bpy.context.scene.collection.objects.link(root)

        res = reconcile_entity_render_plan(root, plan=plan, atlas=atlas)
        self.assertEqual(len(res.created), 1)
        frame_obj = res.created[0]
        mesh = frame_obj.data

        # 1. Mesh attributes for biome tinting must be present
        self.assertIn(ATTR_BIOME_TINT_DATA, mesh.attributes)
        self.assertIn(ATTR_BIOME_TINT_COLOR, mesh.attributes)

        tint_data_attr = mesh.attributes[ATTR_BIOME_TINT_DATA]
        tint_color_attr = mesh.attributes[ATTR_BIOME_TINT_COLOR]

        # 2. At least one face (grass_block_top / grass_block_side) must have tint_weight == 1.0 (channel 2)
        tinted_face_indices = []
        for face_idx, item in enumerate(tint_data_attr.data):
            col = list(item.color)
            if col[2] > 0.5:  # Tint Weight
                tinted_face_indices.append(face_idx)
        self.assertGreater(len(tinted_face_indices), 0, "Grass block elements must have active tint weight")

        # 3. Tinted faces must have grass-green tint color (G > R)
        tinted_face = tinted_face_indices[0]
        # Corner domain attribute for this face
        loop_start = mesh.polygons[tinted_face].loop_start
        corner_col = list(tint_color_attr.data[loop_start].color)
        # Plains grass color has G > R (green foliage)
        self.assertGreater(corner_col[1], corner_col[0], f"Tint color must be green, got: {corner_col}")

        # 4. Dimension verification: grass block item elements must have extent around 0.25 in X/Y (centered in [-0.125, 0.125])
        # Find vertices belonging to the grass block elements (inner recess)
        inner_verts_x = []
        inner_verts_y = []
        for poly_idx in tinted_face_indices:
            for v_idx in mesh.polygons[poly_idx].vertices:
                v = mesh.vertices[v_idx].co
                inner_verts_x.append(v.x)
                inner_verts_y.append(v.y)

        x_span = max(inner_verts_x) - min(inner_verts_x)
        y_span = max(inner_verts_y) - min(inner_verts_y)
        # Block scale must be ~0.25 (16 pixels * 0.5 * 0.5 / 16 = 0.25), NOT 0.5
        self.assertAlmostEqual(x_span, 0.25, delta=0.02)
        self.assertAlmostEqual(y_span, 0.25, delta=0.02)

    def test_10_item_frame_with_decorated_pot_sherds(self):
        """Item frames holding decorated pots with NBT sherds fuse custom sherd pattern textures into mesh."""
        import glob
        import os

        cache_files = glob.glob(os.path.join(os.getcwd(), ".ci-cache", "*.mtkcache"))
        if not cache_files:
            self.skipTest("No .ci-cache asset bundle found for decorated pot sherd fusion")

        cache = self.mtk.AssetCache.open(cache_files[0])
        try:
            models = cache.load_models()
            atlas = cache.load_atlas()
        except Exception:
            self.skipTest("Failed loading models or atlas from cache")

        ent = {
            "id": "minecraft:item_frame",
            "uuid": "00000000-0000-0000-0000-000000000010",
            "position": [0.0, 64.0, 0.0],
            "facing": "north",
            "kind": {
                "type": "minecraft:item_frame",
                "item_id": "minecraft:decorated_pot",
                "count": 1,
                "rotation": 0,
                "is_glowing": False,
                "is_invisible": False,
                "components": {
                    "block_entity": {
                        "id": "minecraft:decorated_pot",
                        "sherds": [
                            "minecraft:angler_pottery_sherd",
                            "minecraft:archer_pottery_sherd",
                            "minecraft:blade_pottery_sherd",
                            "minecraft:brewer_pottery_sherd",
                        ],
                    },
                },
            },
        }

        plan = build_entity_render_plan([ent], atlas=atlas, models=models)
        self.assertEqual(len(plan.warnings), 0)
        self.assertEqual(len(plan.nodes), 1)
        node = plan.nodes[0]

        # 1. Texture routes must contain the configured pottery patterns
        route_textures = [r["texture_key"] for r in node.material_routes]
        sherd_patterns = [
            "angler_pottery_pattern",
            "archer_pottery_pattern",
            "blade_pottery_pattern",
            "brewer_pottery_pattern",
        ]
        for pattern in sherd_patterns:
            matched = any(pattern in tex for tex in route_textures)
            self.assertTrue(
                matched,
                f"Expected sherd pattern '{pattern}' in node routes: {route_textures}",
            )

        # 2. Reconcile into real Blender scene
        root = bpy.data.objects.new("TestPotFrame_Root", None)
        bpy.context.scene.collection.objects.link(root)

        res = reconcile_entity_render_plan(root, plan=plan, atlas=atlas)
        self.assertEqual(len(res.created), 1)
        frame_obj = res.created[0]
        self.assertEqual(frame_obj.type, "MESH")
        self.assertEqual(frame_obj.get("mtk:item_id"), "minecraft:decorated_pot")

        # Frame alone is 22 polygons; frame + decorated pot has 38 polygons (22 + 16 pot faces)
        self.assertGreater(len(frame_obj.data.polygons), 22)
        self.assertEqual(len(frame_obj.data.polygons), 38)
        self.assertGreater(len(frame_obj.material_slots), 0)


if __name__ == "__main__":
    unittest.main()
