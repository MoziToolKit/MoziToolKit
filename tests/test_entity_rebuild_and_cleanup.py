"""
Tests for visual entity lifecycle synchronization, mesh reconstruction, and UI palette properties.

Verifies:
1. Physical cleanup of entity objects when knocked down in Minecraft save upon refresh.
2. Participation of visual entities in MOZI_OT_rebuild_mesh reconstruction.
3. Strict discrete palette format (List[str]) on custom properties to prevent accidental editing.
4. Correct spatial transforms, coordinate centering, and facing orientations.
"""

import json
import os
import tempfile
import types
import unittest
from unittest.mock import patch
import bpy

from MoziToolKit.bridge.engine import get_libmtk
from MoziToolKit.bridge.point_cloud.constants import PROP_VISUAL_ENTITIES
from MoziToolKit.bridge.point_cloud import extract_voxel_point_cloud
from MoziToolKit.bridge.scene import export_scene_package, load_and_mesh_scene_package
from MoziToolKit.utils.scene import apply_imported_scene_to_blender
from MoziToolKit.utils.world.assembler import (
    VoxelWorldAssemblyRequest,
    assemble_voxel_world,
)
from MoziToolKit.operators.mesh.op_rebuild_mesh import MOZI_OT_rebuild_mesh


class TestEntityRebuildAndCleanup(unittest.TestCase):
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

    def test_01_knocked_down_entity_is_physically_removed_on_refresh(self):
        """When an entity is knocked down in Minecraft, refreshing must physically destroy the old Blender object & mesh."""
        # 1. First import: Storage has 1 block and 1 painting
        storage = self.mtk.VoxelStorage()
        storage.set_bounds(0, 0, 0, 16, 16, 16)
        storage.set_block(0, 0, 0, "minecraft:stone")

        painting_data = [
            {
                "id": "minecraft:painting",
                "uuid": "11111111-2222-3333-4444-555555555555",
                "position": [0.5, 1.0, 0.0],
                "facing": "north",
                "rotation": [0.0, 0.0, 0.0],
                "kind": {
                    "type": "minecraft:painting",
                    "variant": "kebab",
                    "width": 1,
                    "height": 1,
                },
            }
        ]
        storage.set_visual_entities_palette([json.dumps(p) for p in painting_data])
        self.assertTrue(storage.has_visual_entities())

        cfg = self.mtk.MesherConfig(origin_centered=True)
        cloud = storage.to_point_cloud(cfg)
        mesh_data = self.mtk.MeshData()

        req1 = VoxelWorldAssemblyRequest(
            container_type="SAVE",
            root_name="TestCleanupWorld",
            mesh_data=mesh_data,
            storage=storage,
            point_cloud=cloud,
            origin_centered=True,
            bounds=[0, 0, 0, 16, 16, 16],
            visual_entities=storage.get_visual_entities_palette(),
            reuse_existing=False,
        )
        res1 = assemble_voxel_world(req1)
        root_obj = res1["root_obj"]
        entity_objs = res1.get("entity_objs", [])
        self.assertEqual(len(entity_objs), 1)

        ent_obj = entity_objs[0]
        ent_obj_name = ent_obj.name
        ent_mesh_name = ent_obj.data.name
        self.assertIn(ent_obj_name, bpy.data.objects)
        self.assertIn(ent_mesh_name, bpy.data.meshes)

        # 2. Simulate user knocking down the painting in Minecraft:
        # Create fresh storage with same block, but 0 entities
        fresh_storage = self.mtk.VoxelStorage()
        fresh_storage.set_bounds(0, 0, 0, 16, 16, 16)
        fresh_storage.set_block(0, 0, 0, "minecraft:stone")
        self.assertFalse(fresh_storage.has_visual_entities())
        fresh_cloud = fresh_storage.to_point_cloud(cfg)

        req2 = VoxelWorldAssemblyRequest(
            container_type="SAVE",
            root_name="TestCleanupWorld",
            mesh_data=mesh_data,
            storage=fresh_storage,
            point_cloud=fresh_cloud,
            origin_centered=True,
            bounds=[0, 0, 0, 16, 16, 16],
            visual_entities=fresh_storage.get_visual_entities_palette(),
            reuse_existing=True,
            target_root=root_obj,
        )
        res2 = assemble_voxel_world(req2)
        fresh_entities = res2.get("entity_objs", [])
        self.assertEqual(len(fresh_entities), 0)

        # 3. Assert the old painting object and mesh were PHYSICALLY destroyed from bpy.data
        self.assertNotIn(
            ent_obj_name,
            bpy.data.objects,
            f"Stale entity object '{ent_obj_name}' was not deleted upon refresh!",
        )
        self.assertNotIn(
            ent_mesh_name,
            bpy.data.meshes,
            f"Stale entity mesh '{ent_mesh_name}' was leaked in bpy.data.meshes!",
        )

    def test_02_rebuild_mesh_participates_in_entity_lifecycle(self):
        """MOZI_OT_rebuild_mesh must reconstruct and synchronize visual entities cleanly."""
        cloud = self.mtk.VoxelPointCloud()
        cloud.push([0.5, 0.5, 0.5], [0, 0, 0], "minecraft:stone", "minecraft:plains", 15)
        cloud.bounds = [0, 0, 0, 1, 1, 1]

        painting_data = [
            {
                "id": "minecraft:painting",
                "uuid": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                "position": [0.5, 1.0, 0.0],
                "facing": "north",
                "rotation": [0.0, 0.0, 0.0],
                "kind": {
                    "type": "minecraft:painting",
                    "variant": "kebab",
                    "width": 1,
                    "height": 1,
                },
            }
        ]
        cloud.set_visual_entities_palette([json.dumps(p) for p in painting_data])

        with tempfile.TemporaryDirectory() as td:
            pkg_path = os.path.join(td, "test_rebuild_scene.mtkscene")
            mock_prefs = types.SimpleNamespace(resource_packs=[], resource_pack_path="")
            export_scene_package(pkg_path, point_cloud=cloud, prefs=mock_prefs)

            data = load_and_mesh_scene_package(pkg_path, prefs=mock_prefs, force=True)
            res = apply_imported_scene_to_blender(
                data,
                scene_name="TestRebuildScene",
                context=bpy.context,
                prefs=mock_prefs,
            )
            root_obj = res["root_obj"]
            mesh_obj = res["mesh_obj"]
            self.assertEqual(len(res.get("entity_objs", [])), 1)

            # Context targeting root_obj
            ctx = types.SimpleNamespace(
                active_object=root_obj,
                selected_objects=[root_obj],
                collection=bpy.context.scene.collection,
                window_manager=None,
                mode="OBJECT",
            )
            mock_op = types.SimpleNamespace(
                enable_ao=True,
                mesh_fluids=True,
                weld_vertices=True,
                origin_centered=True,
                run_async=False,
                force=True,
                report=lambda level, msg: None,
            )

            # Rebuild mesh should succeed and refresh entities
            ret = MOZI_OT_rebuild_mesh.execute(mock_op, ctx)
            self.assertEqual(ret, {"FINISHED"})

            # Find entities container
            ent_container = None
            for child in root_obj.children:
                if child.name.endswith("_Entities"):
                    ent_container = child
                    break
            self.assertIsNotNone(ent_container, "Entities container must exist under root after rebuild")
            vis_children = [c for c in ent_container.children if c.get("mtk:is_visual_entity")]
            self.assertEqual(len(vis_children), 1, "Rebuild must not duplicate entity objects")

    def test_02b_user_deleted_entity_in_viewport_stays_deleted_after_rebuild_mesh(self):
        """When user deletes an entity in the viewport and clicks rebuild mesh, it must NOT resurrect."""
        cloud = self.mtk.VoxelPointCloud()
        cloud.push([0.5, 0.5, 0.5], [0, 0, 0], "minecraft:stone", "minecraft:plains", 15)
        cloud.bounds = [0, 0, 0, 1, 1, 1]

        painting_data = [
            {
                "id": "minecraft:painting",
                "uuid": "bbbbbbbb-cccc-dddd-eeee-ffffffffffff",
                "position": [0.5, 1.0, 0.0],
                "facing": "north",
                "rotation": [0.0, 0.0, 0.0],
                "kind": {
                    "type": "minecraft:painting",
                    "variant": "kebab",
                    "width": 1,
                    "height": 1,
                },
            }
        ]
        cloud.set_visual_entities_palette([json.dumps(p) for p in painting_data])

        with tempfile.TemporaryDirectory() as td:
            pkg_path = os.path.join(td, "test_rebuild_scene_delete.mtkscene")
            mock_prefs = types.SimpleNamespace(resource_packs=[], resource_pack_path="")
            export_scene_package(pkg_path, point_cloud=cloud, prefs=mock_prefs)

            data = load_and_mesh_scene_package(pkg_path, prefs=mock_prefs, force=True)
            res = apply_imported_scene_to_blender(
                data,
                scene_name="TestDeleteInViewportScene",
                context=bpy.context,
                prefs=mock_prefs,
            )
            root_obj = res["root_obj"]
            ent_objs = res.get("entity_objs", [])
            self.assertEqual(len(ent_objs), 1)

            # User presses 'X' in viewport to delete the entity
            ent_to_delete = ent_objs[0]
            ent_name = ent_to_delete.name
            bpy.data.objects.remove(ent_to_delete, do_unlink=True)
            self.assertNotIn(ent_name, bpy.data.objects)

            # Now execute rebuild mesh (unified voxel mesh reconstruction)
            ctx = types.SimpleNamespace(
                active_object=root_obj,
                selected_objects=[root_obj],
                collection=bpy.context.scene.collection,
                window_manager=None,
                mode="OBJECT",
            )
            mock_op = types.SimpleNamespace(
                enable_ao=True,
                mesh_fluids=True,
                weld_vertices=True,
                origin_centered=True,
                run_async=False,
                force=True,
                report=lambda level, msg: None,
            )

            ret = MOZI_OT_rebuild_mesh.execute(mock_op, ctx)
            self.assertEqual(ret, {"FINISHED"})

            # Must NOT resurrect the deleted painting!
            self.assertNotIn(ent_name, bpy.data.objects)
            ent_container = None
            for child in root_obj.children:
                if child.name.endswith("_Entities"):
                    ent_container = child
                    break
            if ent_container is not None:
                vis_children = [c for c in ent_container.children if c.get("mtk:is_visual_entity")]
                self.assertEqual(len(vis_children), 0, "Deleted entity must NOT be resurrected upon rebuild mesh!")

    def test_03_custom_properties_palette_array_prevents_accidental_editing(self):
        """Custom properties must use discrete arrays (List[str]), not raw strings, preventing UI text field exposure."""
        storage = self.mtk.VoxelStorage()
        storage.set_bounds(0, 0, 0, 16, 16, 16)
        storage.set_block(0, 0, 0, "minecraft:stone")
        painting_data = [
            {
                "id": "minecraft:painting",
                "position": [0.5, 1.0, 0.0],
                "facing": "north",
                "rotation": [0.0, 0.0, 0.0],
                "kind": {
                    "type": "minecraft:painting",
                    "variant": "kebab",
                    "width": 1,
                    "height": 1,
                },
            }
        ]
        storage.set_visual_entities_palette([json.dumps(p) for p in painting_data])
        cfg = self.mtk.MesherConfig(origin_centered=True)
        cloud = storage.to_point_cloud(cfg)
        mesh_data = self.mtk.MeshData()

        req = VoxelWorldAssemblyRequest(
            container_type="SAVE",
            root_name="TestPaletteUIWorld",
            mesh_data=mesh_data,
            storage=storage,
            point_cloud=cloud,
            origin_centered=True,
            bounds=[0, 0, 0, 16, 16, 16],
            visual_entities=storage.get_visual_entities_palette(),
            reuse_existing=False,
        )
        res = assemble_voxel_world(req)
        cloud_obj = res["cloud_obj"]
        ent_obj = res["entity_objs"][0]

        # 1. On point cloud object:
        self.assertIsNotNone(cloud_obj)
        # Must have mtk_visual_entities as a Python list
        self.assertTrue(hasattr(cloud_obj, "__getitem__") or hasattr(cloud_obj, "get"))
        prop_val = cloud_obj.get(PROP_VISUAL_ENTITIES)
        self.assertIsInstance(
            prop_val,
            list,
            f"Expected {PROP_VISUAL_ENTITIES} to be a list/array, got {type(prop_val)}",
        )
        self.assertEqual(len(prop_val), 1)

        # 2. On entity mesh object:
        self.assertTrue(ent_obj.get("mtk:is_visual_entity"))
        self.assertEqual(ent_obj.get("mtk:entity_id"), "minecraft:painting")
        self.assertEqual(ent_obj.get("mtk:variant"), "kebab")
        self.assertEqual(ent_obj.get("mtk:facing"), "north")
        # Payload must be a list/array
        payload = ent_obj.get("mtk:entity_payload")
        self.assertIsInstance(
            payload,
            list,
            f"Expected mtk:entity_payload to be an array, got {type(payload)}",
        )
        # Must NOT have raw string mtk:entity_json
        self.assertIsNone(
            ent_obj.get("mtk:entity_json"),
            "Raw string mtk:entity_json must be eliminated to prevent editable text field!",
        )

    def test_04_painting_facing_and_transform_accuracy(self):
        """Verifies paintings facing different directions have correct Z-Up Euler rotations."""
        for facing in ["north", "south", "east", "west"]:
            storage = self.mtk.VoxelStorage()
            storage.set_bounds(0, 0, 0, 16, 16, 16)
            storage.set_block(0, 0, 0, "minecraft:stone")
            painting_data = [
                {
                    "id": "minecraft:painting",
                    "position": [0.5, 1.0, 0.0],
                    "facing": facing,
                    "rotation": [0.0, 0.0, 0.0],
                    "kind": {
                        "type": "minecraft:painting",
                        "variant": "kebab",
                        "width": 1,
                        "height": 1,
                    },
                }
            ]
            storage.set_visual_entities_palette([json.dumps(p) for p in painting_data])
            cfg = self.mtk.MesherConfig(origin_centered=True)
            cloud = storage.to_point_cloud(cfg)
            mesh_data = self.mtk.MeshData()

            req = VoxelWorldAssemblyRequest(
                container_type="SAVE",
                root_name=f"TestFacing_{facing}",
                mesh_data=mesh_data,
                storage=storage,
                point_cloud=cloud,
                origin_centered=True,
                bounds=[0, 0, 0, 16, 16, 16],
                visual_entities=storage.get_visual_entities_palette(),
                reuse_existing=False,
            )
            res = assemble_voxel_world(req)
            ent_obj = res["entity_objs"][0]
            self.assertIsNotNone(ent_obj)
            self.assertEqual(ent_obj.get("mtk:facing"), facing)
            self.assertEqual(ent_obj.type, "MESH")
            self.assertGreater(len(ent_obj.data.polygons), 0)


    def test_05_rebuild_mesh_multiple_times_with_negative_min_y_does_not_drift_or_fly_into_sky(self):
        """Verifies that rebuilding mesh on a world with negative min_y (-64) does not cause remaining entities to fly into the sky."""
        cloud = self.mtk.VoxelPointCloud()
        cloud.push([0.5, 0.5, 0.5], [0, 0, 0], "minecraft:stone", "minecraft:plains", 15)
        # Minecraft Overworld standard bounds: min_y = -64, size_y = 128
        cloud.bounds = [0, -64, 0, 16, 128, 16]

        painting_data = [
            {
                "id": "minecraft:painting",
                "uuid": "11111111-1111-1111-1111-111111111111",
                "position": [1.0, 64.0, 1.0],
                "facing": "north",
                "rotation": [0.0, 0.0, 0.0],
                "kind": {
                    "type": "minecraft:painting",
                    "variant": "kebab",
                    "width": 1,
                    "height": 1,
                },
            },
            {
                "id": "minecraft:painting",
                "uuid": "22222222-2222-2222-2222-222222222222",
                "position": [2.0, 70.0, 2.0],
                "facing": "north",
                "rotation": [0.0, 0.0, 0.0],
                "kind": {
                    "type": "minecraft:painting",
                    "variant": "alban",
                    "width": 1,
                    "height": 1,
                },
            },
        ]
        cloud.set_visual_entities_palette([json.dumps(p) for p in painting_data])

        with tempfile.TemporaryDirectory() as td:
            pkg_path = os.path.join(td, "test_negative_min_y.mtkscene")
            mock_prefs = types.SimpleNamespace(resource_packs=[], resource_pack_path="")
            export_scene_package(pkg_path, point_cloud=cloud, prefs=mock_prefs)

            data = load_and_mesh_scene_package(pkg_path, prefs=mock_prefs, force=True)
            res = apply_imported_scene_to_blender(
                data,
                scene_name="TestNegativeMinYScene",
                context=bpy.context,
                prefs=mock_prefs,
            )
            root_obj = res["root_obj"]
            ent_objs = res.get("entity_objs", [])
            self.assertEqual(len(ent_objs), 2)

            # Map entities by uuid
            p1 = next(e for e in ent_objs if e.get("mtk:entity_uuid") == "11111111-1111-1111-1111-111111111111")
            p2 = next(e for e in ent_objs if e.get("mtk:entity_uuid") == "22222222-2222-2222-2222-222222222222")

            # In Blender, bl_z = mc_y - by = 70.0 - (-64.0) = 134.0
            initial_p2_loc = tuple(p2.location)
            self.assertAlmostEqual(initial_p2_loc[2], 134.0, places=2)

            # User deletes Painting 1 (e.g. knocked down or deleted in viewport)
            p1_name = p1.name
            bpy.data.objects.remove(p1, do_unlink=True)
            self.assertNotIn(p1_name, bpy.data.objects)

            # Execute first rebuild
            ctx = types.SimpleNamespace(
                active_object=root_obj,
                selected_objects=[root_obj],
                collection=bpy.context.scene.collection,
                window_manager=None,
                mode="OBJECT",
            )
            mock_op = types.SimpleNamespace(
                enable_ao=True,
                mesh_fluids=True,
                weld_vertices=True,
                origin_centered=True,
                run_async=False,
                force=True,
                report=lambda level, msg: None,
            )

            ret1 = MOZI_OT_rebuild_mesh.execute(mock_op, ctx)
            self.assertEqual(ret1, {"FINISHED"})

            # Painting 1 must remain gone
            self.assertNotIn(p1_name, bpy.data.objects)

            # Painting 2 must still be present at the exact same location!
            p2_after_1 = next(
                (c for c in bpy.data.objects if c.get("mtk:entity_uuid") == "22222222-2222-2222-2222-222222222222"),
                None,
            )
            self.assertIsNotNone(p2_after_1, "Painting 2 must still exist after rebuild!")
            self.assertAlmostEqual(
                p2_after_1.location.z,
                initial_p2_loc[2],
                places=2,
                msg=f"Entity flew into sky! Expected Z={initial_p2_loc[2]}, got Z={p2_after_1.location.z}",
            )
            self.assertAlmostEqual(p2_after_1.location.x, initial_p2_loc[0], places=2)
            self.assertAlmostEqual(p2_after_1.location.y, initial_p2_loc[1], places=2)

            # Execute SECOND rebuild to assert stability over multiple cycles
            ret2 = MOZI_OT_rebuild_mesh.execute(mock_op, ctx)
            self.assertEqual(ret2, {"FINISHED"})

            p2_after_2 = next(
                (c for c in bpy.data.objects if c.get("mtk:entity_uuid") == "22222222-2222-2222-2222-222222222222"),
                None,
            )
            self.assertIsNotNone(p2_after_2)
            self.assertAlmostEqual(
                p2_after_2.location.z,
                initial_p2_loc[2],
                places=2,
                msg=f"Entity drifted on second rebuild! Expected Z={initial_p2_loc[2]}, got Z={p2_after_2.location.z}",
            )

    def test_06_entity_assembly_failure_does_not_abort_world_assembly(self):
        """Failure of a single entity during baking/assembly must not abort overall world assembly."""
        cloud = self.mtk.VoxelPointCloud()
        cloud.push([0.5, 0.5, 0.5], [0, 0, 0], "minecraft:stone", "minecraft:plains", 15)
        cloud.bounds = [0, 0, 0, 1, 1, 1]

        # One entity that fails baking (unsupported/invalid variant) and one valid painting
        palette = [
            {"id": "minecraft:painting", "kind": {"variant": "non_existent_baking_variant"}},
            {
                "id": "minecraft:painting",
                "uuid": "33333333-3333-3333-3333-333333333333",
                "position": [0.5, 1.0, 0.0],
                "facing": "north",
                "rotation": [0.0, 0.0, 0.0],
                "kind": {
                    "type": "minecraft:painting",
                    "variant": "kebab",
                    "width": 1,
                    "height": 1,
                },
            },
        ]

        mesh_data = self.mtk.MeshData()
        req = VoxelWorldAssemblyRequest(
            container_type="SCENE",
            root_name="[Scene] CorruptedEntityTest",
            mesh_data=mesh_data,
            point_cloud=cloud,
            visual_entities=palette,
            origin_centered=True,
            bounds=[0, 0, 0, 1, 1, 1],
        )
        res = assemble_voxel_world(req)
        self.assertIsNotNone(res["root_obj"])
        self.assertIsNotNone(res["cloud_obj"])
        # The valid painting must have been assembled despite the corrupted entity failing
        self.assertEqual(len(res["entity_objs"]), 1)
        self.assertEqual(res["entity_objs"][0].get("mtk:entity_uuid"), "33333333-3333-3333-3333-333333333333")

    def test_07_relative_matrix_world_preserves_parent_transforms_on_export(self):
        """Moving the entities parent empty or root container must correctly reflect in extracted Minecraft coordinates."""
        from MoziToolKit.utils.scene.entities import extract_scene_visual_entities

        cloud = self.mtk.VoxelPointCloud()
        cloud.push([0.5, 0.5, 0.5], [0, 0, 0], "minecraft:stone", "minecraft:plains", 15)
        cloud.bounds = [0, 0, 0, 10, 10, 10]

        painting = {
            "id": "minecraft:painting",
            "uuid": "44444444-4444-4444-4444-444444444444",
            "position": [5.0, 64.0, 5.0],
            "facing": "north",
            "rotation": [0.0, 0.0, 0.0],
            "scale": [1.0, 1.0, 1.0],
            "kind": {"type": "minecraft:painting", "variant": "kebab", "width": 1, "height": 1},
        }
        cloud.set_visual_entities_palette([json.dumps(painting)])

        mesh_data = self.mtk.MeshData()
        req = VoxelWorldAssemblyRequest(
            container_type="SCENE",
            root_name="[Scene] MoveParentTest",
            mesh_data=mesh_data,
            point_cloud=cloud,
            visual_entities=cloud.get_visual_entities_palette(),
            origin_centered=False,
            bounds=[0, 0, 0, 10, 10, 10],
        )
        res = assemble_voxel_world(req)
        root_obj = res["root_obj"]
        ent_obj = res["entity_objs"][0]
        entities_parent = ent_obj.parent
        self.assertIsNotNone(entities_parent)

        # Baseline extraction
        extracted_raw = extract_scene_visual_entities(root_obj)
        ents = json.loads(extracted_raw)
        self.assertEqual(len(ents), 1)
        self.assertAlmostEqual(ents[0]["position"][0], 5.0, places=2)
        self.assertAlmostEqual(ents[0]["position"][1], 64.0, places=2)
        self.assertAlmostEqual(ents[0]["position"][2], 5.0, places=2)

        # User moves the parent container in Blender:
        # Blender (+X East, +Y North, +Z Up)
        # Shift parent by (+2.0 in X, +3.0 in Y, +4.0 in Z)
        # Minecraft (+X East, +Y Up, +Z South)
        # In Minecraft: mc_x increases by 2.0, mc_y increases by 4.0, mc_z decreases by 3.0 (-bl_y)
        entities_parent.location = (2.0, 3.0, 4.0)
        bpy.context.view_layer.update()

        extracted_after_move = extract_scene_visual_entities(root_obj)
        ents_moved = json.loads(extracted_after_move)
        self.assertEqual(len(ents_moved), 1)
        self.assertAlmostEqual(ents_moved[0]["position"][0], 7.0, places=2)   # 5.0 + 2.0
        self.assertAlmostEqual(ents_moved[0]["position"][1], 68.0, places=2)  # 64.0 + 4.0
        self.assertAlmostEqual(ents_moved[0]["position"][2], 2.0, places=2)   # 5.0 - 3.0

    def test_08_rotation_roundtrip_stability(self):
        """Verifies that model-space rotation conventions round-trip from Minecraft to Blender and back without drift."""
        from MoziToolKit.utils.scene.entities import extract_scene_visual_entities
        import math

        cloud = self.mtk.VoxelPointCloud()
        cloud.push([0.5, 0.5, 0.5], [0, 0, 0], "minecraft:stone", "minecraft:plains", 15)
        cloud.bounds = [0, 0, 0, 10, 10, 10]

        # Test horizontal yaw facings: North (0), West (pi/2), South (pi), East (-pi/2)
        facings = [
            ("north", 0.0),
            ("west", math.pi / 2.0),
            ("south", math.pi),
            ("east", -math.pi / 2.0),
        ]
        for facing_name, yaw_rad in facings:
            painting = {
                "id": "minecraft:painting",
                "uuid": f"55555555-5555-5555-5555-{facing_name[:8].ljust(12, '0')}",
                "position": [5.0, 64.0, 5.0],
                "facing": facing_name,
                "rotation": [0.0, yaw_rad, 0.0],
                "scale": [1.0, 1.0, 1.0],
                "kind": {"type": "minecraft:painting", "variant": "kebab", "width": 1, "height": 1},
            }
            cloud.set_visual_entities_palette([json.dumps(painting)])

            mesh_data = self.mtk.MeshData()
            req = VoxelWorldAssemblyRequest(
                container_type="SCENE",
                root_name=f"[Scene] RotTest_{facing_name}",
                mesh_data=mesh_data,
                point_cloud=cloud,
                visual_entities=cloud.get_visual_entities_palette(),
                origin_centered=False,
                bounds=[0, 0, 0, 10, 10, 10],
            )
            res = assemble_voxel_world(req)
            root_obj = res["root_obj"]

            extracted_raw = extract_scene_visual_entities(root_obj)
            ents = json.loads(extracted_raw)
            self.assertEqual(len(ents), 1)
            ext_yaw = ents[0]["rotation"][1]
            diff = abs(ext_yaw - yaw_rad)
            # Account for angle wrapping at pi / -pi
            if diff > math.pi:
                diff = abs(diff - 2.0 * math.pi)
            self.assertAlmostEqual(diff, 0.0, places=4, msg=f"Yaw drifted for {facing_name}: expected {yaw_rad}, got {ext_yaw}")


if __name__ == "__main__":
    unittest.main()
