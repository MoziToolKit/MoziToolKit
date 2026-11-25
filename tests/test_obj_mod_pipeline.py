"""Blender host integration tests for Wavefront OBJ and Forge/NeoForge composite mod models."""

from __future__ import annotations

import json
import struct
import tempfile
import unittest
import zipfile
import zlib
from pathlib import Path

import bpy
import libmtk_py
from _assets import (
    _skip,
    activated_cache_package,
    assets_root,
    fabric_jar,
    require_mod_samples,
)
from MoziToolKit.bridge.assets.cache import (
    load_baked_atlas_from_cache,
    load_baked_model_database,
)
from MoziToolKit.bridge.debug import load_debug_world_storage
from MoziToolKit.bridge.mesh import inject_mesh_data
from MoziToolKit.bridge.world import mesh_voxel_storage
from MoziToolKit.utils.world.injector import apply_voxel_mesh_to_blender

SAMPLE_MOD_JARS = (
    "create-1.21.1-6.0.10.jar",
    "ImmersiveEngineering-1.21.1-12.4.2-194.jar",
    "Steam_Rails-1.7.3+forge-mc1.20.1.jar",
    "Mekanism-1.21.1-10.7.19.85.jar",
    "pneumaticcraft-repressurized-8.2.24+mc1.21.1.jar",
    "electrodynamics-1.21.1-1.1.1.jar",
)


def _make_solid_png(width: int, height: int, rgba: tuple[int, int, int, int]) -> bytes:
    row = b"\x00" + bytes(rgba) * width
    raw = row * height

    def _chunk(tag: bytes, payload: bytes) -> bytes:
        body = tag + payload
        return (
            struct.pack(">I", len(payload))
            + body
            + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", ihdr)
        + _chunk(b"IDAT", zlib.compress(raw))
        + _chunk(b"IEND", b"")
    )


class TestObjModPipeline(unittest.TestCase):
    def _validate_blender_mesh(self, mesh: bpy.types.Mesh) -> None:
        self.assertGreater(len(mesh.vertices), 0)
        self.assertGreater(len(mesh.polygons), 0)
        self.assertTrue(all(len(poly.vertices) == 3 for poly in mesh.polygons))
        has_errors = mesh.validate(verbose=True)
        self.assertFalse(has_errors, "Blender mesh.validate(verbose=True) reported errors")

    def test_synthetic_obj_and_composite_blender_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            pack_zip = tmp_path / "synthetic_obj_mod.zip"
            cache_dir = tmp_path / "cache"

            with zipfile.ZipFile(pack_zip, "w") as zf:
                zf.writestr(
                    "pack.mcmeta",
                    json.dumps({"pack": {"pack_format": 34, "description": "Synthetic OBJ"}}),
                )
                zf.writestr(
                    "assets/minecraft/textures/block/stone.png",
                    _make_solid_png(16, 16, (128, 128, 128, 255)),
                )
                zf.writestr(
                    "assets/minecraft/models/block/stone.json",
                    json.dumps(
                        {
                            "textures": {"all": "minecraft:block/stone"},
                            "elements": [
                                {
                                    "from": [0, 0, 0],
                                    "to": [16, 16, 16],
                                    "faces": {
                                        d: {"texture": "#all", "cullface": d}
                                        for d in ("north", "south", "east", "west", "up", "down")
                                    },
                                }
                            ],
                        }
                    ),
                )
                zf.writestr(
                    "assets/minecraft/blockstates/stone.json",
                    json.dumps({"variants": {"": {"model": "minecraft:block/stone"}}}),
                )

                zf.writestr(
                    "assets/testmod/textures/entity/widget.png",
                    _make_solid_png(16, 16, (210, 120, 40, 255)),
                )
                zf.writestr(
                    "assets/testmod/textures/block/core.png",
                    _make_solid_png(16, 16, (40, 180, 220, 255)),
                )
                zf.writestr(
                    "assets/testmod/models/block/widget.obj.ie",
                    "\n".join(
                        [
                            "mtllib widget.mtl",
                            "usemtl body",
                            "v 0.1 0.1 0.1",
                            "v 0.9 0.1 0.1",
                            "v 0.5 0.9 0.1",
                            "vt 0.0 0.0",
                            "vt 1.0 0.0",
                            "vt 0.5 1.0",
                            "f 1/1 2/2 3/3",
                        ]
                    ),
                )
                zf.writestr(
                    "assets/testmod/models/block/widget.mtl",
                    "newmtl body\nmap_Kd testmod:entity/widget\n",
                )
                zf.writestr(
                    "assets/testmod/models/block/composite_device.json",
                    json.dumps(
                        {
                            "loader": "neoforge:composite",
                            "children": {
                                "core": {
                                    "textures": {"core": "testmod:block/core"},
                                    "elements": [
                                        {
                                            "from": [4, 0, 4],
                                            "to": [12, 8, 12],
                                            "faces": {
                                                "up": {"texture": "#core"},
                                                "north": {"texture": "#core"},
                                            },
                                        }
                                    ],
                                },
                                "blade": {
                                    "loader": "immersiveengineering:ie_obj",
                                    "model": "testmod:models/block/widget.obj.ie",
                                    "textures": {"particle": "testmod:entity/widget"},
                                },
                            },
                        }
                    ),
                )
                zf.writestr(
                    "assets/testmod/blockstates/composite_device.json",
                    json.dumps({"variants": {"": {"model": "testmod:block/composite_device"}}}),
                )

            stack = libmtk_py.ResourcePackStack()
            stack.add_zip_pack(str(pack_zip))
            res = libmtk_py.precompile_all_assets(
                stack,
                str(cache_dir),
                atlas_category="blocks",
                max_atlas_width=1024,
                max_atlas_height=1024,
                compile_atlas=True,
                compile_models=True,
            )
            self.assertTrue(res.success)

            storage = load_debug_world_storage(
                states=["minecraft:stone", "testmod:composite_device"]
            )
            with activated_cache_package(res.package_path):
                model_db = load_baked_model_database(None)
                atlas = load_baked_atlas_from_cache(None)
                baked = model_db.get_mesh("testmod:composite_device")
                self.assertIsNotNone(baked)
                _, model_textures = baked
                self.assertIn("testmod:entity/widget", model_textures)

                mesh_data, _ = mesh_voxel_storage(
                    storage=storage,
                    model_db=model_db,
                    atlas=atlas,
                    weld_mode="manifold",
                )

            b_mesh = bpy.data.meshes.new("MTK_TestSyntheticObj")
            obj = bpy.data.objects.new("MTK_TestSyntheticObj", b_mesh)
            bpy.context.scene.collection.objects.link(obj)
            inject_mesh_data(mesh_data, b_mesh, update_topology=True, update_normals=True)
            self._validate_blender_mesh(b_mesh)

    def test_real_obj_mod_jars_precompile_and_blender_validation(self):
        mod_dir = require_mod_samples()
        vanilla_source = fabric_jar() or assets_root()
        if vanilla_source is None:
            _skip("Vanilla client JAR or unpacked assets required for real mod OBJ pipeline test")

        jar_paths = [mod_dir / name for name in SAMPLE_MOD_JARS]
        missing = [p.name for p in jar_paths if not p.is_file()]
        if missing:
            _skip(f"Missing sample mod JAR(s) in {mod_dir}: {missing}")

        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp) / "cache"
            stack = libmtk_py.ResourcePackStack()
            for jar_path in jar_paths:
                stack.add_zip_pack(str(jar_path))
            if vanilla_source.is_file():
                stack.add_zip_pack(str(vanilla_source))
            else:
                stack.add_directory_pack(str(vanilla_source), "VanillaAssets")

            res = libmtk_py.precompile_all_assets(
                stack,
                str(cache_dir),
                atlas_category="blocks",
                max_atlas_width=4096,
                max_atlas_height=4096,
                compile_atlas=True,
                compile_models=True,
            )
            self.assertTrue(res.success)

            showcase_states = [
                "minecraft:stone",
                "minecraft:grass_block",
                "create:crushing_wheel[axis=y]",
                "create:lit_blaze_burner[flame_type=regular]",
                "immersiveengineering:razor_wire[facing=east]",
                "railways:buffer_wide[diagonal=false,facing=east]",
                "mekanism:quantum_entangloporter[facing=down]",
                "pneumaticcraft:etching_tank",
                "electrodynamics:combustionchamber[facing=south]",
            ]
            storage = load_debug_world_storage(states=showcase_states)

            with activated_cache_package(res.package_path):
                model_db = load_baked_model_database(None)
                atlas = load_baked_atlas_from_cache(None)
                for st in showcase_states:
                    self.assertIsNotNone(model_db.get_mesh(st), f"Missing baked model for {st}")

                mesh_data, elapsed_ms = mesh_voxel_storage(
                    storage=storage,
                    model_db=model_db,
                    atlas=atlas,
                    weld_mode="manifold",
                )
                obj, stats = apply_voxel_mesh_to_blender(
                    mesh_data=mesh_data,
                    storage=storage,
                    elapsed_ms=elapsed_ms,
                    name="MTK_TestRealObjMods",
                    atlas=atlas,
                )

            self.assertGreater(stats["materials_count"], 0)
            self.assertGreater(len(obj.data.uv_layers), 0)
            self._validate_blender_mesh(obj.data)
