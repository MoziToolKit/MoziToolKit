"""
Tests for MoziToolKit Painting Procedural Geometry and Entity Bridge.
Executed strictly in real Blender 5.x LTS environment without mocks.
"""

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

from MoziToolKit.bridge.entity import (
    build_painting_mesh,
    calculate_painting_position,
    get_all_painting_variants,
    get_painting_dimension,
    get_painting_variant,
)
from MoziToolKit.bridge.mesh import inject_mesh_data


class TestPaintingBridge(unittest.TestCase):
    def test_painting_variants_metadata(self):
        all_variants = get_all_painting_variants()
        self.assertEqual(len(all_variants), 51)

        dim = get_painting_dimension("kebab")
        self.assertEqual(dim, (1, 1))

        dim_4x4 = get_painting_dimension("minecraft:pointer")
        self.assertEqual(dim_4x4, (4, 4))

        meta = get_painting_variant("kebab")
        self.assertIsNotNone(meta)
        self.assertEqual(meta["name"], "kebab")
        self.assertEqual(meta["width"], 1)
        self.assertEqual(meta["height"], 1)
        self.assertEqual(meta["asset_id"], "minecraft:painting/kebab")
        self.assertEqual(meta["title"], "Kebab med tre pepperoni")
        self.assertEqual(meta["author"], "Kristoffer Zetterstrand")

    def test_painting_position_calculation(self):
        support = [10, 64, 20]
        pos = calculate_painting_position(support, "south", variant="kebab")
        self.assertAlmostEqual(pos[0], 10.5, places=3)
        self.assertAlmostEqual(pos[1], 64.5, places=3)
        self.assertAlmostEqual(pos[2], 20.03125, places=3)

    def test_painting_mesh_injection_safe_path(self):
        mesh_data, textures = build_painting_mesh("kebab", "north")
        self.assertEqual(len(textures), 2)
        self.assertIn("minecraft:painting/kebab", textures)
        self.assertIn("minecraft:painting/back", textures)

        # Create real Blender mesh
        mesh = bpy.data.meshes.new("TestPainting_Kebab")
        inject_mesh_data(mesh_data, mesh, use_dna=False)

        # 6 quad faces: front, back, top, bottom, left, right
        self.assertEqual(len(mesh.polygons), 6)
        self.assertEqual(len(mesh.vertices), 24)

        # Verify UV loop data
        self.assertIn("UVMap", mesh.uv_layers)
        uv_layer = mesh.uv_layers["UVMap"]
        self.assertEqual(len(uv_layer.data), 24)

        bpy.data.meshes.remove(mesh)

    def test_painting_mesh_injection_dna_path(self):
        mesh_data, textures = build_painting_mesh("kebab", "north")
        mesh = bpy.data.meshes.new("TestPainting_Kebab_DNA")
        # Try injecting via DNA channel (falls back gracefully if unsupported)
        success = inject_mesh_data(mesh_data, mesh, use_dna=True)
        self.assertTrue(success)
        self.assertEqual(len(mesh.polygons), 6)
        self.assertEqual(len(mesh.vertices), 24)
        bpy.data.meshes.remove(mesh)

    def test_painting_mesh_injection_4x4_pointer(self):
        mesh_data, textures = build_painting_mesh("pointer", "south")
        self.assertIn("minecraft:painting/pointer", textures)
        self.assertIn("minecraft:painting/back", textures)

        mesh = bpy.data.meshes.new("TestPainting_Pointer")
        inject_mesh_data(mesh_data, mesh, use_dna=False)

        # 16 front quads + 16 back quads + 16 rim borders = 48 quads
        self.assertEqual(len(mesh.polygons), 48)
        self.assertEqual(len(mesh.vertices), 48 * 4)

        bpy.data.meshes.remove(mesh)


if __name__ == "__main__":
    unittest.main()
