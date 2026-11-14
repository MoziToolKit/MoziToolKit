"""
Integration tests for 2D Extruded Item Model Baker and Prebaked Cache Bridge.
Executed strictly in real Blender 5.x LTS host environment without mocks.
"""

import unittest
import bpy

from MoziToolKit.bridge.engine import get_libmtk


class TestItemGeneratorBridge(unittest.TestCase):
    def setUp(self):
        self.mtk = get_libmtk()
        if self.mtk is None:
            self.skipTest("libmtk_py is not available in test environment")

        from MoziToolKit.dev import ops

        try:
            ops.register()
        except Exception:
            pass

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

    def test_01_debug_single_pixel_item_mesh(self):
        """Bakes a 1x1 solid pixel item mesh and verifies 6 faces (box) in Blender."""
        testing = self.mtk.unstable.testing
        # 1 pixel RGBA = (255, 0, 0, 255)
        pixels = bytes([255, 0, 0, 255])
        mesh_data = testing.bake_item_model_mesh(pixels, 1, 1, "minecraft:item/pixel", True)

        self.assertFalse(mesh_data.is_empty())
        self.assertEqual(mesh_data.quad_count, 6)
        self.assertEqual(mesh_data.vertex_count, 24)

        # Ingest into Blender Mesh Object via official batch channel
        blender_mesh = bpy.data.meshes.new("TestPixelItem")
        pos_mv = mesh_data.positions_memoryview()
        quad_mv = mesh_data.quad_indices_memoryview()

        num_verts = mesh_data.vertex_count
        num_faces = mesh_data.quad_count

        blender_mesh.vertices.add(num_verts)
        blender_mesh.vertices.foreach_set("co", pos_mv)

        blender_mesh.polygons.add(num_faces)
        loop_starts = [i * 4 for i in range(num_faces)]
        loop_totals = [4] * num_faces
        blender_mesh.polygons.foreach_set("loop_start", loop_starts)
        blender_mesh.polygons.foreach_set("loop_total", loop_totals)

        blender_mesh.loops.add(num_faces * 4)
        blender_mesh.loops.foreach_set("vertex_index", quad_mv)
        blender_mesh.update()

        obj = bpy.data.objects.new("TestPixelItem_Obj", blender_mesh)
        bpy.context.scene.collection.objects.link(obj)

        self.assertEqual(len(obj.data.vertices), 24)
        self.assertEqual(len(obj.data.polygons), 6)
        # Dimensions: 1x1x(1/16) -> X=1.0, Y=1.0, Z=0.0625
        self.assertAlmostEqual(obj.dimensions.x, 1.0, places=3)
        self.assertAlmostEqual(obj.dimensions.y, 1.0, places=3)
        self.assertAlmostEqual(obj.dimensions.z, 0.0625, places=4)

    def test_02_prebaked_items_via_baker_bake_all(self):
        """Verifies that ModelBaker.bake_all prebakes item models into BakedModelDatabase."""
        from _assets import fabric_jar

        jar = fabric_jar()
        if jar is None:
            self.skipTest("Vanilla JAR not found in test environment (set MTK_TEST_JAR)")

        testing = self.mtk.unstable.testing

        stack = self.mtk.ResourcePackStack()
        stack.add_zip_pack(str(jar))

        baker = testing.ModelBaker()
        db = baker.bake_all(stack)
        self.assertGreater(len(db), 1000)

        # Verify iconic items are resolved from BakedModelDatabase
        for item_key in [
            "minecraft:diamond_sword",
            "minecraft:golden_apple",
            "minecraft:iron_pickaxe",
            "minecraft:netherite_axe",
            "minecraft:bow",
            "minecraft:potion",
        ]:
            res = testing.model_database_get_mesh(db, item_key, False)
            self.assertIsNotNone(res, f"Item '{item_key}' failed to resolve from prebaked database")
            mesh_data, textures = res
            self.assertFalse(mesh_data.is_empty(), f"Item '{item_key}' produced empty mesh")
            self.assertGreater(mesh_data.quad_count, 0)
            self.assertGreater(mesh_data.vertex_count, 0)

        # Test vertex welding comparison on diamond_sword
        sword_unwelded, _ = testing.model_database_get_mesh(db, "minecraft:diamond_sword", False, False)
        sword_welded, _ = testing.model_database_get_mesh(db, "minecraft:diamond_sword", False, True)
        self.assertEqual(sword_unwelded.quad_count, sword_welded.quad_count)
        self.assertEqual(sword_unwelded.vertex_count, 408)
        self.assertEqual(sword_welded.vertex_count, 140)
        self.assertLess(sword_welded.vertex_count, sword_unwelded.vertex_count)

        # Test full items showcase gallery grid generation in Rust
        gallery_horizontal = testing.model_database_build_items_gallery(db, 36, 1.5, False)
        self.assertIsNotNone(gallery_horizontal)
        h_mesh, h_textures, h_count = gallery_horizontal
        self.assertGreater(h_count, 1000, "Expected >1000 prebaked items in vanilla assets")
        self.assertFalse(h_mesh.is_empty())
        self.assertGreater(h_mesh.quad_count, 10000)
        self.assertGreater(h_mesh.vertex_count, 40000)
        self.assertGreater(len(h_textures), 0)

        # Also verify vertical gallery wall orientation
        gallery_vertical = testing.model_database_build_items_gallery(db, 20, 2.0, True)
        self.assertIsNotNone(gallery_vertical)
        v_mesh, v_textures, v_count = gallery_vertical
        self.assertEqual(v_count, h_count)
        self.assertEqual(v_mesh.quad_count, h_mesh.quad_count)

    def test_03_dev_load_debug_item_operator(self):
        """Tests the dev operator mozi.dev_load_debug_item and dev.api.load_debug_item."""
        from MoziToolKit.utils.system.dependencies import get_prefs
        from MoziToolKit.utils.assets.guard import can_execute_asset_pipeline, check_asset_cache_status

        prefs = get_prefs(bpy.context)
        can_run, _ = can_execute_asset_pipeline(prefs)
        if not can_run:
            self.skipTest("Minecraft vanilla client jar base pack not configured")

        status = check_asset_cache_status(prefs)
        if status in ("NO_PACKS", "NO_VANILLA"):
            self.skipTest("No valid Minecraft base pack available")

        # 1. Test Single Item Mode
        ret_single = bpy.ops.mozi.dev_load_debug_item(
            generation_mode="SINGLE",
            item_preset="diamond_sword",
            scale=1.0,
        )
        if ret_single == {"CANCELLED"}:
            self.skipTest("Asset precompilation or item lookup cancelled in test environment")

        self.assertEqual(ret_single, {"FINISHED"})

        sword_obj = bpy.data.objects.get("MTK_Diamond_sword")
        self.assertIsNotNone(sword_obj)
        self.assertEqual(sword_obj.type, "MESH")
        self.assertEqual(len(sword_obj.data.vertices), 140)
        self.assertEqual(len(sword_obj.data.polygons), 102)
        self.assertTrue(sword_obj.get("mtk:is_debug_item", False))
        self.assertEqual(sword_obj.get("mtk:item_id"), "minecraft:diamond_sword")

        # Verify unwelded option produces 408 vertices
        bpy.data.objects.remove(sword_obj, do_unlink=True)
        ret_unwelded = bpy.ops.mozi.dev_load_debug_item(
            generation_mode="SINGLE",
            item_preset="diamond_sword",
            weld_vertices=False,
            scale=1.0,
        )
        self.assertEqual(ret_unwelded, {"FINISHED"})
        sword_unwelded_obj = bpy.data.objects.get("MTK_Diamond_sword")
        self.assertIsNotNone(sword_unwelded_obj)
        self.assertEqual(len(sword_unwelded_obj.data.vertices), 408)
        self.assertEqual(len(sword_unwelded_obj.data.polygons), 102)
        bpy.data.objects.remove(sword_unwelded_obj, do_unlink=True)

        # 2. Test Showcase Gallery Mode (ITEMS_ONLY by default)
        ret_items = bpy.ops.mozi.dev_load_debug_item(
            generation_mode="ALL",
            item_filter="ITEMS_ONLY",
            columns=36,
            spacing=1.5,
            layout_plane="HORIZONTAL",
            center_grid=True,
        )
        self.assertEqual(ret_items, {"FINISHED"})

        gallery_items = bpy.data.objects.get("MTK_Debug_Items_Gallery")
        self.assertIsNotNone(gallery_items)
        self.assertEqual(gallery_items.type, "MESH")
        self.assertTrue(gallery_items.get("mtk:is_debug_item", False))
        self.assertGreater(gallery_items.get("mtk:total_items", 0), 1000)
        self.assertGreater(len(gallery_items.data.vertices), 40000)
        self.assertGreater(len(gallery_items.data.polygons), 10000)
        # ITEMS_ONLY must contain strictly items atlas chunk (no blocks or blocks_anim)
        self.assertEqual(len(gallery_items.data.materials), 1)
        self.assertEqual(gallery_items.data.materials[0].get("mtk_atlas_chunk_id"), 2)
        self.assertTrue(gallery_items.data.materials[0].name.startswith("MTK:Atlas:items"))
        self.assertIn("mtk_atlas_chunk_id", gallery_items.data.attributes)

        # Clean up before testing ALL mode
        bpy.data.objects.remove(gallery_items, do_unlink=True)

        # 3. Test Full Showcase Gallery Mode (ALL items and block-items)
        ret_all = bpy.ops.mozi.dev_load_debug_item(
            generation_mode="ALL",
            item_filter="ALL",
            columns=36,
            spacing=1.5,
            layout_plane="HORIZONTAL",
            center_grid=True,
        )
        self.assertEqual(ret_all, {"FINISHED"})

        gallery_all = bpy.data.objects.get("MTK_Debug_Items_Gallery")
        self.assertIsNotNone(gallery_all)
        self.assertGreater(len(gallery_all.data.materials), 1)
        self.assertIn("mtk_atlas_chunk_id", gallery_all.data.attributes)
