import _mesh_arrays as ma
from _mtk_helpers import mesh_world
"""
Integration tests for Live Sync Atlas Material binding and internal addressing.
Tests that VoxelStorage / VoxelWorld with Atlas & BiomeResolver generates
the correct face attributes (UVs, tiling, biome tint, source texture key)
and that ensure_world_materials correctly assigns the Atlas Chunk materials.
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_DIR = Path(__file__).parent.parent.resolve()
PARENT_DIR = PROJECT_DIR.parent
libmtk_release_path = PROJECT_DIR.parent / "libmozitoolkit" / "target" / "release"

for p in [str(libmtk_release_path), str(PROJECT_DIR), str(PARENT_DIR)]:
    if p not in sys.path:
        sys.path.insert(0, p)

import bpy
import bmesh
import mathutils

try:
    import libmtk_py as mtk_py
    HAS_LIBMTK = True
except ImportError:
    mtk_py = None
    HAS_LIBMTK = False

from MoziToolKit.utils.materials.builder.world_binding import ensure_world_materials
from MoziToolKit.utils.world.sync_container import (
    get_or_create_world_mesh_object,
    update_world_mesh,
)
from MoziToolKit.bridge.mesh import inject_mesh_data


def _write_dummy_png(path: Path):
    """Helper to write a valid 1x1 PNG file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82")


class TestLiveSyncAtlasMaterials(unittest.TestCase):
    def setUp(self):
        if not HAS_LIBMTK:
            self.skipTest("libmtk_py is required for atlas materials test")

    def test_voxel_mesher_atlas_and_face_attributes(self):
        """
        Verify that VoxelWorld with AtlasAddressMap produces face attributes:
        - mtk_source_texture_key
        - mtk_material_slot
        - mtk_atlas_chunk_id
        - mtk_atlas_texture_id
        - mtk_uv_transform
        - mtk_biome_tint_data
        - mtk_biome_tint_color
        """
        # Create a mock atlas mapping JSON
        mapping_data = {
            "format_version": 1,
            "category": "blocks",
            "chunks": [
                {
                    "chunk_id": 0,
                    "category": "blocks",
                    "category_chunk_index": 1,
                    "width": 1024,
                    "height": 1024,
                    "is_animated": False,
                    "has_normal": False,
                    "has_specular": False,
                    "has_overlay": False,
                },
                {
                    "chunk_id": 1,
                    "category": "blocks",
                    "category_chunk_index": 2,
                    "width": 1024,
                    "height": 1024,
                    "is_animated": False,
                    "has_normal": False,
                    "has_specular": False,
                    "has_overlay": False,
                }
            ],
            "sprites": {
                "minecraft:block/grass_block_top": {
                    "chunk_id": 0,
                    "texture_id": 101,
                    "uv_bounds": [0.0, 0.0, 0.25, 0.25],
                    "frame_0_uv_bounds": [0.0, 0.0, 0.25, 0.25],
                    "pixel_rect": [0, 0, 256, 256],
                    "frame_size": [256, 256],
                    "frame_count": 1,
                    "is_animated": False,
                    "has_normal": False,
                    "has_specular": False,
                    "has_overlay": False,
                },
                "minecraft:block/grass_block_side": {
                    "chunk_id": 0,
                    "texture_id": 102,
                    "uv_bounds": [0.25, 0.0, 0.5, 0.25],
                    "frame_0_uv_bounds": [0.25, 0.0, 0.5, 0.25],
                    "pixel_rect": [256, 0, 256, 256],
                    "frame_size": [256, 256],
                    "frame_count": 1,
                    "is_animated": False,
                    "has_normal": False,
                    "has_specular": False,
                    "has_overlay": False,
                },
                "minecraft:block/dirt": {
                    "chunk_id": 0,
                    "texture_id": 103,
                    "uv_bounds": [0.5, 0.0, 0.75, 0.25],
                    "frame_0_uv_bounds": [0.5, 0.0, 0.75, 0.25],
                    "pixel_rect": [512, 0, 256, 256],
                    "frame_size": [256, 256],
                    "frame_count": 1,
                    "is_animated": False,
                    "has_normal": False,
                    "has_specular": False,
                    "has_overlay": False,
                },
                "minecraft:block/water_still": {
                    "chunk_id": 1,
                    "texture_id": 201,
                    "uv_bounds": [0.0, 0.0, 0.5, 0.5],
                    "frame_0_uv_bounds": [0.0, 0.0, 0.5, 0.5],
                    "pixel_rect": [0, 0, 512, 512],
                    "frame_size": [512, 512],
                    "frame_count": 1,
                    "is_animated": False,
                    "has_normal": False,
                    "has_specular": False,
                    "has_overlay": False,
                },
            }
        }

        atlas = mtk_py.BakedAtlas.from_mapping_json(json.dumps(mapping_data))
        self.assertIsNotNone(atlas)

        # Mesher config with Atlas and BiomeResolver
        config = mtk_py.MesherConfig(
            enable_ao=True,
            mesh_fluids=True,
            z_up_coordinates=True,
            atlas=atlas,
        )

        # Create storage with a grass block and a water block
        storage = mtk_py.VoxelStorage()
        storage.set_bounds(0, 0, 0, 16, 16, 16)
        storage.set_block(0, 0, 0, "minecraft:grass_block")
        storage.set_block(0, 2, 0, "minecraft:water")

        culler = mtk_py.FaceCuller() if hasattr(mtk_py, "FaceCuller") else None
        mesh = mesh_world(storage, config, culler)
        self.assertIsNotNone(mesh)
        self.assertGreater(mesh.face_count, 0)
        self.assertGreater(mesh.face_count, 0)

        # Check face attributes on mesh
        attr_names = set(mesh.attribute_names())
        self.assertNotIn("mtk_source_texture_key", attr_names)
        self.assertIn("mtk_source_texture_idx", attr_names)
        self.assertIn("mtk_source_textures", attr_names)
        self.assertNotIn("mtk_material_slot", attr_names)
        self.assertIn("mtk_atlas_chunk_id", attr_names)
        self.assertNotIn("mtk_atlas_texture_id", attr_names)
        self.assertIn("mtk_uv_transform", attr_names)
        self.assertIn("mtk_biome_tint_data", attr_names)
        self.assertIn("mtk_biome_tint_color", attr_names)
        self.assertNotIn("mtk_face_dir", attr_names)

        # Verify keys resolve from the compact palette + indices pair
        palette = mesh.get_string_attribute("mtk_source_textures") or []
        mv = mesh.attribute_memoryview("mtk_source_texture_idx")
        indices = list(mv) if mv is not None else []
        self.assertGreater(len(palette), 0)
        self.assertGreater(len(indices), 0)
        keys = [palette[i] for i in indices]

        # Directional candidate resolution check:
        # grass_block should resolve to top, side, and dirt for bottom!
        has_top = any("grass_block_top" in k for k in keys)
        has_side = any("grass_block_side" in k for k in keys)
        has_dirt = any("dirt" in k for k in keys)
        has_water = any("water_still" in k for k in keys)

        self.assertTrue(has_top, f"Expected grass_block_top in keys: {keys}")
        self.assertTrue(has_side, f"Expected grass_block_side in keys: {keys}")
        self.assertTrue(has_dirt, f"Expected dirt in keys: {keys}")
        self.assertTrue(has_water, f"Expected water_still in keys: {keys}")

        # Check material slots
        mat_slots = ma.face_materials(mesh)
        self.assertEqual(len(mat_slots), mesh.face_count)
        # Water was chunk 1, grass was chunk 0
        self.assertIn(0, mat_slots)
        self.assertIn(1, mat_slots)

    def test_ensure_world_materials_atlas_binding(self):
        """
        Verify that ensure_world_materials correctly builds and assigns
        atlas chunk materials to mesh.materials matching chunk indices.
        """
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            atlas_dir = tmp_path / "atlas"
            atlas_dir.mkdir(parents=True)

            _write_dummy_png(atlas_dir / "blocks_chunk_001.png")
            _write_dummy_png(atlas_dir / "blocks_chunk_002.png")

            mapping_data = {
                "format_version": 1,
                "category": "blocks",
                "chunks": [
                    {
                        "chunk_id": 0,
                        "category": "blocks",
                        "category_chunk_index": 1,
                        "width": 1024,
                        "height": 1024,
                        "is_animated": False,
                    },
                    {
                        "chunk_id": 1,
                        "category": "blocks",
                        "category_chunk_index": 2,
                        "width": 1024,
                        "height": 1024,
                        "is_animated": False,
                    }
                ],
                "sprites": {}
            }
            (atlas_dir / "atlas_mapping.json").write_text(json.dumps(mapping_data))

            with patch.dict(os.environ, {"MOZI_CACHE_DIR": str(tmp_path)}):
                # Create a test mesh object
                test_mesh = bpy.data.meshes.new("TestWorldMesh")
                test_obj = bpy.data.objects.new("TestWorldMesh", test_mesh)

                ensure_world_materials(test_obj)

                # Both chunk 0 and chunk 1 should be bound in order!
                self.assertGreaterEqual(len(test_mesh.materials), 2)
                mat0 = test_mesh.materials[0]
                mat1 = test_mesh.materials[1]
                self.assertIsNotNone(mat0)
                self.assertIsNotNone(mat1)
                self.assertIn("Atlas", mat0.name)
                self.assertIn("Atlas", mat1.name)

                # Clean up
                bpy.data.objects.remove(test_obj)
                bpy.data.meshes.remove(test_mesh)

    def test_update_world_mesh_atlas_injection_end_to_end(self):
        """
        Verify that update_world_mesh successfully injects real VoxelWorld geometry,
        palette-based provenance attributes, binds Atlas materials with
        use_attribute_node=False, and connects UVMap directly.
        """
        if not HAS_LIBMTK:
            self.skipTest("libmtk is required for end-to-end injection test")

        from _assets import blender_datafiles_cache

        cache_root = blender_datafiles_cache()
        if cache_root is None:
            self.skipTest("Blender asset cache not found (set MOZI_CACHE_DIR / MTK_TEST_CACHE)")
        packages = sorted(cache_root.glob("*.mtkcache"))
        if not packages:
            self.skipTest(f"No .mtkcache package found at {cache_root}")
        try:
            cache = mtk_py.AssetCache.open(str(packages[0]))
            atlas = cache.load_atlas()
            model_db = cache.load_models()
        except Exception as exc:
            self.skipTest(f"Failed to open .mtkcache: {exc}")

        config = mtk_py.MesherConfig(
            enable_ao=True,
            mesh_fluids=True,
            z_up_coordinates=True,
            atlas=atlas,
        )
        storage = mtk_py.VoxelStorage()
        storage.set_bounds(0, 0, 0, 16, 16, 16)
        storage.set_block(0, 0, 0, "minecraft:grass_block")
        culler = mtk_py.FaceCuller() if hasattr(mtk_py, "FaceCuller") else None
        mesh_data = mesh_world(storage, config, culler, model_db)

        test_mesh = bpy.data.meshes.new("E2EWorldMesh")
        test_obj = bpy.data.objects.new("E2EWorldMesh", test_mesh)

        try:
            v_count, f_count = update_world_mesh(test_obj, mesh_data)
            self.assertGreater(v_count, 0)
            self.assertGreater(f_count, 0)

            # 1. Verify palette-based provenance (per-face strings are retired)
            self.assertIsNone(test_mesh.attributes.get("mtk_source_texture_key"))
            idx_attr = test_mesh.attributes.get("mtk_source_texture_idx")
            self.assertIsNotNone(idx_attr)
            self.assertGreater(len(idx_attr.data), 0)
            self.assertIn("mtk_source_textures", test_mesh)

            # 2. Verify numeric custom attributes
            tint_data_attr = test_mesh.attributes.get("mtk_biome_tint_data")
            self.assertIsNotNone(tint_data_attr)

            chunk_id_attr = test_mesh.attributes.get("mtk_atlas_chunk_id")
            self.assertIsNotNone(chunk_id_attr)

            # 3. Verify materials assigned
            self.assertGreater(len(test_mesh.materials), 0)

            # 4. Verify shader tree: use_attribute_node=False (direct UVMap.UV -> Texture Vector)
            for mat in test_mesh.materials:
                if not mat or not mat.node_tree:
                    continue
                node_names = [n.name for n in mat.node_tree.nodes]
                self.assertNotIn("Atlas UV Tiling", node_names, "Shader should NOT include Atlas UV Tiling")
                albedo_node = mat.node_tree.nodes.get("Atlas Albedo Texture")
                if albedo_node and albedo_node.inputs["Vector"].links:
                    src = albedo_node.inputs["Vector"].links[0].from_node.name
                    self.assertEqual(src, "UV Map", "Albedo vector should connect directly to UV Map")
        finally:
            bpy.data.objects.remove(test_obj)
            bpy.data.meshes.remove(test_mesh)

    def test_live_sync_incremental_materials_addressing_stability(self):
        """
        Verify that when new materials are introduced during Live Sync updates,
        existing faces stay accurately addressed to their respective materials
        and do NOT accidentally get remapped to newly introduced materials.
        """
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            atlas_dir = tmp_path / "atlas"
            atlas_dir.mkdir(parents=True)

            _write_dummy_png(atlas_dir / "blocks_chunk_001.png")
            _write_dummy_png(atlas_dir / "blocks_chunk_002.png")
            _write_dummy_png(atlas_dir / "blocks_chunk_003.png")

            mapping_data = {
                "format_version": 1,
                "category": "blocks",
                "chunks": [
                    {"chunk_id": 0, "category": "blocks", "category_chunk_index": 1, "width": 1024, "height": 1024, "is_animated": False},
                    {"chunk_id": 1, "category": "blocks", "category_chunk_index": 2, "width": 1024, "height": 1024, "is_animated": False},
                    {"chunk_id": 2, "category": "blocks", "category_chunk_index": 3, "width": 1024, "height": 1024, "is_animated": False},
                ],
                "sprites": {}
            }
            (atlas_dir / "atlas_mapping.json").write_text(json.dumps(mapping_data))

            with patch.dict(os.environ, {"MOZI_CACHE_DIR": str(tmp_path)}):
                # Frame 1: Mesh with 2 faces, both from Chunk 0
                test_mesh = bpy.data.meshes.new("SyncAddressingMesh")
                verts = [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0), (2, 0, 0), (2, 1, 0)]
                faces = [(0, 1, 2, 3), (1, 4, 5, 2)]
                test_mesh.from_pydata(verts, [], faces)
                test_mesh.update()

                attr_chunk = test_mesh.attributes.new(name="mtk_atlas_chunk_id", type="INT", domain="FACE")
                attr_chunk.data[0].value = 0
                attr_chunk.data[1].value = 0

                test_obj = bpy.data.objects.new("SyncAddressingObj", test_mesh)

                try:
                    ensure_world_materials(test_obj, used_chunk_ids=[0])

                    self.assertEqual(len(test_mesh.materials), 1)
                    self.assertIn("001", test_mesh.materials[0].name)
                    self.assertEqual(test_mesh.polygons[0].material_index, 0)
                    self.assertEqual(test_mesh.polygons[1].material_index, 0)

                    # Frame 2: New geometry arrives with 4 faces (Faces 0,1 from Chunk 0; Faces 2,3 from Chunk 1)
                    test_mesh.clear_geometry()
                    verts4 = [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0),
                              (2, 0, 0), (2, 1, 0), (3, 0, 0), (3, 1, 0), (4, 0, 0), (4, 1, 0)]
                    faces4 = [(0, 1, 2, 3), (1, 4, 5, 2), (4, 6, 7, 5), (6, 8, 9, 7)]
                    test_mesh.from_pydata(verts4, [], faces4)
                    test_mesh.update()

                    attr_chunk2 = test_mesh.attributes.new(name="mtk_atlas_chunk_id", type="INT", domain="FACE")
                    attr_chunk2.data[0].value = 0
                    attr_chunk2.data[1].value = 0
                    attr_chunk2.data[2].value = 1
                    attr_chunk2.data[3].value = 1

                    ensure_world_materials(test_obj, used_chunk_ids=[0, 1])

                    self.assertEqual(len(test_mesh.materials), 2)
                    self.assertIn("001", test_mesh.materials[0].name)
                    self.assertIn("002", test_mesh.materials[1].name)

                    # CRITICAL: Chunk 0 faces MUST point to Slot 0 (mat0), Chunk 1 faces MUST point to Slot 1 (mat1)
                    self.assertEqual(test_mesh.polygons[0].material_index, 0, "Chunk 0 face must remain at material slot 0")
                    self.assertEqual(test_mesh.polygons[1].material_index, 0, "Chunk 0 face must remain at material slot 0")
                    self.assertEqual(test_mesh.polygons[2].material_index, 1, "Chunk 1 face must point to material slot 1")
                    self.assertEqual(test_mesh.polygons[3].material_index, 1, "Chunk 1 face must point to material slot 1")

                    # Frame 3: Multi-chunk interleaved (Chunk 0, Chunk 1, Chunk 2)
                    attr_chunk2.data[0].value = 0
                    attr_chunk2.data[1].value = 1
                    attr_chunk2.data[2].value = 2
                    attr_chunk2.data[3].value = 0

                    ensure_world_materials(test_obj, used_chunk_ids=[0, 1, 2])

                    self.assertEqual(len(test_mesh.materials), 3)
                    self.assertEqual(test_mesh.polygons[0].material_index, 0)
                    self.assertEqual(test_mesh.polygons[1].material_index, 1)
                    self.assertEqual(test_mesh.polygons[2].material_index, 2)
                    self.assertEqual(test_mesh.polygons[3].material_index, 0)

                    # Frame 4: Incremental update fast-path
                    # When geometry changes but uses chunk materials already bound on test_obj,
                    # ensure_world_materials must completely bypass disk I/O, cache reloading, and colormap checks!
                    attr_chunk2.data[0].value = 1
                    attr_chunk2.data[1].value = 0
                    attr_chunk2.data[2].value = 1
                    attr_chunk2.data[3].value = 0

                    binding_mod = "MoziToolKit.utils.materials.builder.world_binding"
                    with patch(f"{binding_mod}.ensure_atlas_textures_extracted") as mock_extract, \
                         patch(f"{binding_mod}.load_baked_atlas_from_cache") as mock_load_atlas, \
                         patch(f"{binding_mod}.get_cache_fingerprint") as mock_fp:
                        ensure_world_materials(test_obj, used_chunk_ids=[0, 1])

                        # Fast-path assertion: ZERO cache reading or texture extraction during incremental update
                        self.assertFalse(mock_extract.called, "Fast-path must not extract atlas textures")
                        self.assertFalse(mock_load_atlas.called, "Fast-path must not load atlas from cache")
                        self.assertFalse(mock_fp.called, "Fast-path must not check manifest fingerprint")

                    self.assertEqual(len(test_mesh.materials), 3, "Material slots must be preserved")
                    self.assertIn(
                        "mtk_atlas_chunk_id",
                        test_mesh.attributes,
                        "Incremental updates must keep the face chunk attribute stable",
                    )
                    self.assertEqual(test_mesh.polygons[0].material_index, 1)
                    self.assertEqual(test_mesh.polygons[1].material_index, 0)
                    self.assertEqual(test_mesh.polygons[2].material_index, 1)
                    self.assertEqual(test_mesh.polygons[3].material_index, 0)

                finally:
                    bpy.data.objects.remove(test_obj)
                    bpy.data.meshes.remove(test_mesh)

    def _open_real_cache_assets(self):
        from _assets import blender_datafiles_cache

        cache_root = blender_datafiles_cache()
        if cache_root is None:
            self.skipTest("Blender asset cache not found (set MOZI_CACHE_DIR / MTK_TEST_CACHE)")
        packages = sorted(cache_root.glob("*.mtkcache"))
        if not packages:
            self.skipTest(f"No .mtkcache package found at {cache_root}")
        try:
            cache = mtk_py.AssetCache.open(str(packages[0]))
            atlas = cache.load_atlas()
            model_db = cache.load_models()
            return atlas, model_db
        except Exception as exc:
            self.skipTest(f"Failed to open .mtkcache: {exc}")

    def _assert_all_face_materials_match_chunk_ids(self, mesh, label: str):
        import numpy as np

        poly_count = len(mesh.polygons)
        self.assertGreater(poly_count, 0, f"{label}: mesh must have polygons")
        self.assertGreater(len(mesh.materials), 0, f"{label}: mesh must have bound material slots")

        mat_indices = np.empty(poly_count, dtype=np.int32)
        mesh.polygons.foreach_get("material_index", mat_indices)

        chunk_attr = mesh.attributes.get("mtk_atlas_chunk_id")
        self.assertIsNotNone(chunk_attr, f"{label}: mtk_atlas_chunk_id attribute must exist")
        face_chunks = np.empty(poly_count, dtype=np.int32)
        chunk_attr.data.foreach_get("value", face_chunks)

        slot_cids = [
            int(mat.get("mtk_atlas_chunk_id", -1)) if mat is not None else -1
            for mat in mesh.materials
        ]
        for poly_idx in range(poly_count):
            slot_idx = int(mat_indices[poly_idx])
            self.assertGreaterEqual(slot_idx, 0, f"{label}: negative material_index at poly {poly_idx}")
            self.assertLess(
                slot_idx,
                len(slot_cids),
                f"{label}: out-of-range material_index {slot_idx} >= {len(slot_cids)} at poly {poly_idx}",
            )
            self.assertEqual(
                slot_cids[slot_idx],
                int(face_chunks[poly_idx]),
                f"{label}: poly {poly_idx} points to chunk {slot_cids[slot_idx]} instead of {int(face_chunks[poly_idx])}",
            )

    def _cleanup_sync_objects(self, root_name: str, obj_names: list[str]):
        for name in list(obj_names) + [root_name, f"{root_name}_Mesh", f"{root_name}_VoxelCloud"]:
            obj = bpy.data.objects.get(name)
            if obj is not None:
                data = getattr(obj, "data", None)
                bpy.data.objects.remove(obj, do_unlink=True)
                if data is not None and getattr(data, "users", 0) == 0 and data.name in bpy.data.meshes:
                    bpy.data.meshes.remove(data)

    def test_live_sync_non_contiguous_chunks_repeated_update_addressing(self):
        """
        Regression test for Fast Path 0 + uncompacted face_materials:
        Repeated update_world_mesh calls with non-contiguous chunk IDs (blocks, water, chest,
        decorated_pot, banner, 0xFFF0 dynamic atlas) must keep every polygon mapped to its
        matching material slot instead of leaving raw chunk IDs in material_index.
        """
        atlas, model_db = self._open_real_cache_assets()

        def build_world_mesh(offset_x: int, blocks):
            storage = mtk_py.VoxelStorage()
            storage.set_bounds(offset_x, 0, 0, 8, 4, 8)
            for bx, by, bz, state in blocks:
                storage.set_block(offset_x + bx, by, bz, state, "minecraft:plains")
            cfg = mtk_py.MesherConfig(
                enable_ao=True,
                mesh_fluids=True,
                z_up_coordinates=True,
                origin_centered=True,
                weld_mode="manifold",
                atlas=atlas,
            )
            world = mtk_py.VoxelWorld.from_storage(storage, config=cfg, model_db=model_db)
            return world.rebuild_all(), storage

        blocks_full = [
            (0, 0, 0, "minecraft:stone"),
            (1, 0, 0, "minecraft:water[level=0]"),
            (2, 0, 0, "minecraft:chest[facing=north,type=single,waterlogged=false]"),
            (3, 0, 0, "minecraft:decorated_pot[cracked=false,facing=north,waterlogged=false]"),
            (4, 0, 0, "minecraft:white_banner[rotation=0]"),
            (5, 0, 0, "minecraft:redstone_wire[east=none,north=none,power=15,south=none,west=none]"),
        ]
        blocks_subset = [
            (0, 0, 0, "minecraft:stone"),
            (2, 0, 0, "minecraft:chest[facing=north,type=single,waterlogged=false]"),
        ]

        mesh_datablock = bpy.data.meshes.new("NonContigSyncMesh")
        world_obj = bpy.data.objects.new("NonContigSyncObj", mesh_datablock)
        try:
            mesh1, st1 = build_world_mesh(0, blocks_full)
            self.assertFalse(mesh1.materials_are_slots)
            used1 = sorted(mesh1.used_materials())
            self.assertGreaterEqual(len(used1), 4)
            self.assertIn(65520, used1)

            update_world_mesh(world_obj, mesh1, skip_string_attributes=True, storage=st1, sync_point_cloud=False, atlas=atlas)
            self._assert_all_face_materials_match_chunk_ids(world_obj.data, "Sync 1")

            # Sync 2: moved bounds, same non-contiguous chunk set (previously hit Fast Path 0 bug)
            mesh2, st2 = build_world_mesh(16, blocks_full)
            self.assertFalse(mesh2.materials_are_slots)
            self.assertEqual(sorted(mesh2.used_materials()), used1)
            update_world_mesh(world_obj, mesh2, skip_string_attributes=True, storage=st2, sync_point_cloud=False, atlas=atlas)
            self._assert_all_face_materials_match_chunk_ids(world_obj.data, "Sync 2")

            # Sync 3: subset of existing slots (stone + chest)
            mesh3, st3 = build_world_mesh(32, blocks_subset)
            update_world_mesh(world_obj, mesh3, skip_string_attributes=True, storage=st3, sync_point_cloud=False, atlas=atlas)
            self._assert_all_face_materials_match_chunk_ids(world_obj.data, "Sync 3")
        finally:
            self._cleanup_sync_objects("NonContigSyncObj", ["NonContigSyncObj", "NonContigSyncObj_VoxelCloud"])

    def test_live_sync_loopback_selection_move_and_hot_reload(self):
        """
        End-to-end LiveSyncServer -> LiveSyncSession -> Blender regression test:
        1. Initial baseline with non-contiguous block-entity & dynamic chunks (0xFFF0)
        2. Selection move (second baseline with same chunk set) preserves 100% valid slot addressing
        3. session.hot_reload preserves dynamic entity atlas registration (0xFFF0)
        """
        import time

        atlas, model_db = self._open_real_cache_assets()
        root_name = "TestLoopbackSyncRoot"
        root_obj = bpy.data.objects.new(root_name, None)
        bpy.context.scene.collection.objects.link(root_obj)
        world_obj = get_or_create_world_mesh_object(bpy.context, root_container=root_obj)

        server = mtk_py.LiveSyncServer(bind_address="127.0.0.1:0", allow_loopback_without_token=True)
        cfg = mtk_py.MesherConfig(
            enable_ao=True,
            mesh_fluids=True,
            z_up_coordinates=True,
            origin_centered=True,
            weld_mode="manifold",
            atlas=atlas,
        )
        session = mtk_py.LiveSyncSession(config=cfg, culler=mtk_py.FaceCuller(), model_db=model_db)

        def submit_and_wait_mesh(min_pos, palette, voxels):
            server.poll_events()
            server.submit_baseline(
                0,
                min_pos,
                (4, 2, 4),
                "minecraft:overworld",
                voxels,
                palette,
                [0],
                ["minecraft:plains"],
                [],
            )
            deadline = time.monotonic() + 10.0
            while time.monotonic() < deadline:
                for ev in session.poll_events(coalesce_meshes=True):
                    if ev.get("type") == "WORLD_MESH_READY":
                        return ev["mesh"]
                time.sleep(0.02)
            self.fail(f"Timed out waiting for WORLD_MESH_READY at {min_pos}")

        try:
            url = f"ws://{server.local_addr()}"
            session.start(url=url, auto_reconnect=False, max_reconnect_attempts=1)

            handshake_ok = False
            deadline = time.monotonic() + 10.0
            while time.monotonic() < deadline and not handshake_ok:
                for ev in session.poll_events(coalesce_meshes=True):
                    if ev.get("type") == "HANDSHAKE":
                        handshake_ok = True
                        break
                if not handshake_ok:
                    time.sleep(0.02)
            self.assertTrue(handshake_ok, "LiveSyncSession did not complete HANDSHAKE")

            pal = [
                "minecraft:air",
                "minecraft:stone",
                "minecraft:chest[facing=north,type=single,waterlogged=false]",
                "minecraft:decorated_pot[cracked=false,facing=north,waterlogged=false]",
                "minecraft:white_banner[rotation=0]",
                "minecraft:redstone_wire[east=none,north=none,power=15,south=none,west=none]",
                "minecraft:water[level=0]",
            ]
            vox = [0] * (4 * 2 * 4)
            vox[0] = 1
            vox[1] = 2
            vox[2] = 3
            vox[3] = 4
            vox[4] = 5
            vox[5] = 6

            mesh1 = submit_and_wait_mesh((0, 64, 0), pal, vox)
            self.assertFalse(mesh1.materials_are_slots)
            self.assertIn(65520, mesh1.used_materials())
            update_world_mesh(
                world_obj,
                mesh1,
                skip_string_attributes=True,
                storage=session.get_storage(),
                sync_point_cloud=True,
                atlas=atlas,
                session=session,
            )
            self._assert_all_face_materials_match_chunk_ids(world_obj.data, "Loopback Sync 1")

            # Selection moved in-game -> second baseline with same chunk set
            mesh2 = submit_and_wait_mesh((16, 64, 0), pal, vox)
            self.assertFalse(mesh2.materials_are_slots)
            self.assertIn(65520, mesh2.used_materials())
            update_world_mesh(
                world_obj,
                mesh2,
                skip_string_attributes=True,
                storage=session.get_storage(),
                sync_point_cloud=True,
                atlas=atlas,
                session=session,
            )
            self._assert_all_face_materials_match_chunk_ids(world_obj.data, "Loopback Sync 2 (moved selection)")

            # Hot-reload config (reconnect / cache reload) must preserve 0xFFF0 dynamic entity atlas
            session.hot_reload(cfg, model_db)
            mesh3 = submit_and_wait_mesh((32, 64, 0), pal, vox)
            self.assertIn(65520, mesh3.used_materials(), "Dynamic chunk 0xFFF0 lost after hot_reload")
            self.assertTrue(bool(mesh3.dynamic_entity_atlases), "dynamic_entity_atlases missing after hot_reload")
            update_world_mesh(
                world_obj,
                mesh3,
                skip_string_attributes=True,
                storage=session.get_storage(),
                sync_point_cloud=True,
                atlas=atlas,
                session=session,
            )
            self._assert_all_face_materials_match_chunk_ids(world_obj.data, "Loopback Sync 3 (after hot_reload)")
        finally:
            try:
                session.stop()
            except Exception:
                pass
            try:
                server.stop()
            except Exception:
                pass
            self._cleanup_sync_objects(root_name, [getattr(world_obj, "name", "")])


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0]])

