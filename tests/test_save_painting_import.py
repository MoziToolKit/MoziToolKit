"""
Test Anvil Save Import for Visual Paintings and Block Entities.
Executed strictly in real Blender 5.x LTS environment without mocks.
"""

import os
import sys
import unittest
from pathlib import Path

PROJECT_DIR = Path(__file__).parent.parent.resolve()
PARENT_DIR = PROJECT_DIR.parent
libmtk_release_path = PROJECT_DIR.parent / "libmozitoolkit" / "target" / "release"

for p in [str(libmtk_release_path), str(PROJECT_DIR), str(PARENT_DIR)]:
    if p not in sys.path:
        sys.path.insert(0, p)

import bpy

from MoziToolKit.bridge.save import inspect_minecraft_save, load_and_mesh_minecraft_save
from MoziToolKit.utils.save.hierarchy import apply_imported_save_to_blender
from tests._assets import save_world


class TestSavePaintingImport(unittest.TestCase):
    def test_user_save_detects_all_paintings(self):
        user_save = save_world()
        if user_save is None or not user_save.exists():
            self.skipTest("Test save world not found (set MTK_TEST_SAVE)")

        # 1. Metadata inspection
        meta = inspect_minecraft_save(user_save)
        self.assertIn("overworld", meta["dimensions"])

        # 2. Load bounded 3D selection and mesh end-to-end
        min_block = (-17, -61, -17)
        max_block = (19, -48, 17)
        mesh_data, level_meta, storage, elapsed_ms = load_and_mesh_minecraft_save(
            user_save,
            dimension="overworld",
            min_block=min_block,
            max_block=max_block,
            force=True,
        )
        self.assertIsNotNone(storage)
        self.assertIsNotNone(mesh_data)

        # 3. Verify mesh_data contains baked geometry with visual entity geometry
        self.assertGreater(mesh_data.face_count, 0)
        self.assertGreater(mesh_data.vertex_count, 0)
        self.assertGreater(mesh_data.triangle_count, 0)
        quad_mv = mesh_data.quad_indices_memoryview()
        self.assertIsNotNone(quad_mv)
        self.assertEqual(len(quad_mv), mesh_data.face_count * 4)

        # 4. Storage remains pristine without fake painting blockstates
        # The space occupied by the hanging entity remains true air, decoupled from discrete voxels
        self.assertEqual(storage.get_block(-8, -59, 10), "minecraft:air")
        # And the world foundation block is non-air
        self.assertNotEqual(storage.get_block(0, -61, 0), "minecraft:air")

        # 5. Point cloud extraction succeeds cleanly and carries visual entities
        cloud = storage.to_point_cloud()
        self.assertGreater(len(cloud), 0)
        self.assertTrue(cloud.has_visual_entities())
        self.assertGreater(cloud.visual_entities_count(), 0)

        # 6. Full Blender scene injection & material binding verification
        root_obj, stats = apply_imported_save_to_blender(
            mesh_data=mesh_data,
            meta=level_meta,
            storage=storage,
            elapsed_ms=elapsed_ms,
            world_dir=user_save,
            dimension="overworld",
            min_block=min_block,
            max_block=max_block,
            context=bpy.context,
        )
        self.assertIsNotNone(root_obj)
        self.assertEqual(stats["polygon_count"], mesh_data.face_count)
        mesh_obj = bpy.data.objects.get(stats["mesh_name"])
        self.assertIsNotNone(mesh_obj)
        self.assertEqual(len(mesh_obj.data.polygons), mesh_data.face_count)
        self.assertGreater(len(mesh_obj.data.materials), 0)

        # 7. Verify standalone visual entity objects exist under container hierarchy
        self.assertGreater(stats.get("entities_count", 0), 0)
        entity_objs = stats.get("entity_objs", [])
        self.assertGreater(len(entity_objs), 0)
        for ent_obj in entity_objs:
            self.assertEqual(ent_obj.type, "MESH")
            self.assertGreater(len(ent_obj.data.polygons), 0)
            self.assertGreater(len(ent_obj.data.materials), 0)
            self.assertTrue(ent_obj.get("mtk:is_visual_entity", False))
            mat_names = [m.name for m in ent_obj.data.materials if m]
            self.assertTrue(any("paintings" in name for name in mat_names), f"Missing paintings atlas in {mat_names}")
            for p in ent_obj.data.polygons:
                self.assertLess(p.material_index, len(ent_obj.data.materials))
            # Verify local mesh dimensions: X is width, Y is thickness (0.0625), Z is vertical height
            self.assertAlmostEqual(ent_obj.dimensions.x, float(ent_obj.get("mtk:width", 1)), places=2)
            self.assertAlmostEqual(ent_obj.dimensions.y, 0.0625, places=3)
            self.assertAlmostEqual(ent_obj.dimensions.z, float(ent_obj.get("mtk:height", 1)), places=2)

            # Verify world coordinates & orientation
            facing = str(ent_obj.get("mtk:facing", "")).lower()
            if facing == "east":
                # Aligned with wall at X=-8 relative to selection center (cx = 1.5)
                self.assertAlmostEqual(ent_obj.location.x, -7.96875 - 1.5, places=2)
            elif facing == "north":
                # Aligned with wall at Z=6 relative to selection center (cz = 0.5)
                self.assertAlmostEqual(ent_obj.location.y, -5.46875, places=2)

        # 8. Verify companion point cloud carries mtk_visual_entities (array palette format) and roundtrips extraction
        from MoziToolKit.bridge.point_cloud import extract_voxel_point_cloud
        cloud_obj = bpy.data.objects.get(stats["cloud_name"])
        self.assertIsNotNone(cloud_obj)
        self.assertIsInstance(cloud_obj.get("mtk_visual_entities"), list)
        extracted = extract_voxel_point_cloud(cloud_obj)
        self.assertIsNotNone(extracted)
        self.assertTrue(extracted.has_visual_entities())
        self.assertEqual(extracted.visual_entities_count(), cloud.visual_entities_count())

        # 9. Verify Rebuild Mesh preserves standalone visual entity objects
        import types
        from unittest.mock import patch
        from tests._assets import fabric_jar
        from MoziToolKit.bridge.assets.pack import invalidate_pack_stack_cache
        from MoziToolKit.operators.mesh.op_rebuild_mesh import MOZI_OT_rebuild_mesh

        invalidate_pack_stack_cache()
        jar = fabric_jar()

        rebuild_ctx = types.SimpleNamespace(
            active_object=root_obj,
            selected_objects=[root_obj],
            mode="OBJECT",
            collection=bpy.context.collection,
            scene=bpy.context.scene,
            view_layer=bpy.context.view_layer,
            preferences=bpy.context.preferences,
            window_manager=bpy.context.window_manager,
        )
        mock_op = type("MockOp", (), {
            "run_async": False,
            "force": True,
            "enable_ao": True,
            "mesh_fluids": True,
            "weld_vertices": True,
            "origin_centered": True,
            "report": lambda self, type_set, msg: None,
        })()

        mock_prefs = types.SimpleNamespace(
            resource_pack_path=str(jar) if jar else "",
            resource_packs=[],
            enable_ao=True,
            mesh_fluids=True,
            weld_vertices=True,
            origin_centered=True,
            enable_ctm=True,
            enable_animated_textures=True,
            unknown_block_strategy="FALLBACK_CUBE",
            thread_count=0,
        )

        with patch("MoziToolKit.operators.mesh.op_rebuild_mesh.get_prefs", return_value=mock_prefs):
            res = MOZI_OT_rebuild_mesh.execute(mock_op, rebuild_ctx)
            self.assertEqual(res, {"FINISHED"})

        for ent_obj in entity_objs:
            self.assertIn(ent_obj.name, bpy.data.objects)
            self.assertEqual(ent_obj.type, "MESH")
            self.assertGreater(len(ent_obj.data.polygons), 0)
            self.assertTrue(ent_obj.get("mtk:is_visual_entity", False))

        # 10. Verify exporting container to .mtkscene and re-importing preserves entities
        import tempfile
        from MoziToolKit.bridge.scene import export_scene_package, load_and_mesh_scene_package
        from MoziToolKit.operators.scene.op_export_scene import (
            _resolve_export_dynamic_entities,
            _resolve_export_dynamic_entity_slots,
        )
        from MoziToolKit.utils.scene import apply_imported_scene_to_blender

        with tempfile.TemporaryDirectory() as td:
            scene_pkg_path = Path(td) / "save_export_with_paintings.mtkscene"
            dyn_entities = _resolve_export_dynamic_entities(root_obj, cloud_obj)
            dyn_slots = _resolve_export_dynamic_entity_slots(root_obj, cloud_obj)
            scene_meta = export_scene_package(
                output_path=scene_pkg_path,
                point_cloud=extracted,
                dynamic_entity_atlases=dyn_entities,
                dynamic_entity_slots=dyn_slots,
            )
            self.assertEqual(scene_meta["visual_entities_count"], cloud.visual_entities_count())

            scene_data = load_and_mesh_scene_package(scene_pkg_path, force=True)
            self.assertIn("visual_entities", scene_data)
            scene_res = apply_imported_scene_to_blender(
                scene_data,
                scene_name="ReimportedPaintingScene",
                context=bpy.context,
            )
            self.assertEqual(scene_res.get("entities_count"), cloud.visual_entities_count())
            reimported_ent_objs = scene_res.get("entity_objs", [])
            self.assertEqual(len(reimported_ent_objs), cloud.visual_entities_count())
            for ent_obj in reimported_ent_objs:
                self.assertEqual(ent_obj.type, "MESH")
                self.assertGreater(len(ent_obj.data.polygons), 0)
                self.assertGreater(len(ent_obj.data.materials), 0)
                self.assertTrue(ent_obj.get("mtk:is_visual_entity", False))
