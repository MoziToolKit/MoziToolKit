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
        # And the supporting block behind the painting is a solid block
        self.assertNotEqual(storage.get_block(-9, -59, 10), "minecraft:air")

        # 5. Point cloud extraction succeeds cleanly
        cloud = storage.to_point_cloud()
        self.assertGreater(cloud.len(), 0)

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
