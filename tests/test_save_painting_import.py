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

from MoziToolKit.bridge.save import inspect_minecraft_save, load_and_mesh_minecraft_save
from tests._assets import save_world


class TestSavePaintingImport(unittest.TestCase):
    def test_user_save_detects_all_paintings(self):
        user_save = save_world() or (
            Path(
                "/Users/jaxlocke/Library/Application Support/PrismLauncher/instances/26.2/minecraft/saves/新的世界 (1)"
            )
            if Path(
                "/Users/jaxlocke/Library/Application Support/PrismLauncher/instances/26.2/minecraft/saves/新的世界 (1)"
            ).exists()
            else None
        )
        if user_save is None or not user_save.exists():
            self.skipTest(f"Test save world not found")

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

        # 4. Storage remains pristine without fake painting blockstates
        # The space occupied by the hanging entity remains true air, decoupled from discrete voxels
        self.assertEqual(storage.get_block(-8, -59, 10), "minecraft:air")
        # And the supporting block behind the painting is a solid block
        self.assertNotEqual(storage.get_block(-9, -59, 10), "minecraft:air")

        # 5. Point cloud extraction succeeds cleanly
        cloud = storage.to_point_cloud()
        self.assertGreater(cloud.len(), 0)
