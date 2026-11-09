"""
Test Anvil Save Import for Visual Paintings and Block Entities.
Executed strictly in real Blender 5.x LTS environment without mocks.
"""

import json
import sys
import unittest
from pathlib import Path

PROJECT_DIR = Path(__file__).parent.parent.resolve()
PARENT_DIR = PROJECT_DIR.parent
libmtk_release_path = PROJECT_DIR.parent / "libmozitoolkit" / "target" / "release"

for p in [str(libmtk_release_path), str(PROJECT_DIR), str(PARENT_DIR)]:
    if p not in sys.path:
        sys.path.insert(0, p)

from MoziToolKit.bridge.save import inspect_minecraft_save, load_minecraft_save_storage


class TestSavePaintingImport(unittest.TestCase):
    def test_user_save_detects_all_paintings(self):
        user_save = Path(
            "/Users/jaxlocke/Library/Application Support/PrismLauncher/instances/26.2/minecraft/saves/新的世界 (1)"
        )
        if not user_save.exists():
            self.skipTest(f"User save not found at: {user_save}")

        # 1. Metadata inspection
        meta = inspect_minecraft_save(user_save)
        self.assertIn("overworld", meta["dimensions"])

        # 2. Load bounded 3D selection (matching yefira_selection.json)
        min_block = (-17, -61, -17)
        max_block = (19, -48, 17)
        storage = load_minecraft_save_storage(
            user_save,
            dimension="overworld",
            min_block=min_block,
            max_block=max_block,
        )
        self.assertIsNotNone(storage)

        # 3. Verify all 4 paintings anchored on the wall
        # 3.1 Unpacked (4x4, facing east)
        block_unpacked = storage.get_block(-8, -59, 10)
        self.assertEqual(
            block_unpacked, "minecraft:painting[facing=east,variant=unpacked]"
        )
        entity_unpacked_json = storage.get_block_entity(-8, -59, 10)
        self.assertIsNotNone(entity_unpacked_json)
        ent_data = json.loads(entity_unpacked_json)
        self.assertEqual(ent_data.get("variant"), "unpacked")
        self.assertEqual(ent_data.get("width"), 4)
        self.assertEqual(ent_data.get("height"), 4)

        # 3.2 Humble (2x2, facing east)
        block_humble = storage.get_block(-8, -59, 7)
        self.assertEqual(
            block_humble, "minecraft:painting[facing=east,variant=humble]"
        )

        # 3.3 Pool (2x1, facing east)
        block_pool = storage.get_block(-8, -57, 7)
        self.assertEqual(block_pool, "minecraft:painting[facing=east,variant=pool]")

        # 3.4 Sunset (2x1, facing east)
        block_sunset = storage.get_block(-8, -60, 7)
        self.assertEqual(
            block_sunset, "minecraft:painting[facing=east,variant=sunset]"
        )

        # 4. Point cloud extraction contains paintings
        cloud = storage.to_point_cloud()
        self.assertGreater(cloud.len(), 0)
        self.assertTrue(cloud.has_entities())
