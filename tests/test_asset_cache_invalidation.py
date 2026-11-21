"""
Regression tests for asset cache in-memory memoization and pack-stack invalidation (H-07).

Verifies:
1. Cold-start load populates active cache identity immediately and memoizes subsequent reads.
2. Switching configured pack stacks switches open_active_asset_cache and all load_* readers
   without requiring manual invalidate_asset_cache_memory().
3. World pipeline asset miss/hit memoization reacts to cache package additions and deletions.
"""

from __future__ import annotations

import json
import os
import shutil
import struct
import tempfile
import types
import unittest
import zlib
from pathlib import Path

from MoziToolKit.bridge.assets import (
    get_configured_stack_fingerprint,
    invalidate_asset_cache_memory,
    load_baked_atlas_from_cache,
    load_baked_model_database,
    load_colormaps_from_cache,
    open_active_asset_cache,
    precompile_stack,
)
from MoziToolKit.bridge.engine import has_libmtk
from MoziToolKit.bridge.world.assets import get_world_pipeline_assets


def _png_bytes(color: tuple[int, int, int, int]) -> bytes:
    raw = bytearray()
    pixel = bytes(color)
    for _ in range(16):
        raw.append(0)
        raw.extend(pixel * 16)
    compressed = zlib.compress(bytes(raw))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 16, 16, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", compressed)
        + chunk(b"IEND", b"")
    )


def _write_minimal_pack(pack_dir: Path, block_name: str, color: tuple[int, int, int, int]) -> None:
    tex_dir = pack_dir / "assets" / "minecraft" / "textures" / "block"
    models_dir = pack_dir / "assets" / "minecraft" / "models" / "block"
    bs_dir = pack_dir / "assets" / "minecraft" / "blockstates"
    cmap_dir = pack_dir / "assets" / "minecraft" / "textures" / "colormap"
    for d in (tex_dir, models_dir, bs_dir, cmap_dir):
        d.mkdir(parents=True, exist_ok=True)

    for canonical in ("stone", "dirt", "oak_planks", block_name):
        (tex_dir / f"{canonical}.png").write_bytes(_png_bytes(color if canonical == block_name else (128, 128, 128, 255)))
        model = {
            "textures": {"all": f"minecraft:block/{canonical}"},
            "elements": [
                {
                    "from": [0, 0, 0],
                    "to": [16, 16, 16],
                    "faces": {f: {"texture": "#all"} for f in ("up", "down", "north", "south", "west", "east")},
                }
            ],
        }
        (models_dir / f"{canonical}.json").write_text(json.dumps(model), encoding="utf-8")
        (bs_dir / f"{canonical}.json").write_text(
            json.dumps({"variants": {"": {"model": f"minecraft:block/{canonical}"}}}),
            encoding="utf-8",
        )

    (cmap_dir / "grass.png").write_bytes(_png_bytes(color))
    (cmap_dir / "foliage.png").write_bytes(_png_bytes(color))
    (pack_dir / "pack.mcmeta").write_text(
        json.dumps({"pack": {"pack_format": 15, "description": f"Pack {block_name}"}}),
        encoding="utf-8",
    )


class TestAssetCacheInvalidation(unittest.TestCase):
    def setUp(self):
        if not has_libmtk():
            self.skipTest("libmtk_py required")
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="mtk_cache_inv_"))
        self.cache_dir = self.tmp_dir / "cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.pack_a = self.tmp_dir / "pack_a"
        self.pack_b = self.tmp_dir / "pack_b"
        _write_minimal_pack(self.pack_a, "stone", (255, 10, 10, 255))
        _write_minimal_pack(self.pack_b, "dirt", (10, 255, 10, 255))

        self.prefs_a = types.SimpleNamespace(
            resource_packs=[types.SimpleNamespace(path=str(self.pack_a), enabled=True, name="pack_a")],
            cache_dir=str(self.cache_dir),
            thread_count=1,
        )
        self.prefs_b = types.SimpleNamespace(
            resource_packs=[types.SimpleNamespace(path=str(self.pack_b), enabled=True, name="pack_b")],
            cache_dir=str(self.cache_dir),
            thread_count=1,
        )

        self._prev_cache_dir = os.environ.get("MOZI_CACHE_DIR")
        os.environ["MOZI_CACHE_DIR"] = str(self.cache_dir)
        invalidate_asset_cache_memory()

    def tearDown(self):
        invalidate_asset_cache_memory()
        if self._prev_cache_dir is None:
            os.environ.pop("MOZI_CACHE_DIR", None)
        else:
            os.environ["MOZI_CACHE_DIR"] = self._prev_cache_dir
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_switching_pack_stack_without_manual_invalidation_switches_cached_readers(self):
        res_a = precompile_stack(self.prefs_a)
        res_b = precompile_stack(self.prefs_b)
        self.assertTrue(res_a["success"])
        self.assertTrue(res_b["success"])

        fp_a = get_configured_stack_fingerprint(self.prefs_a)
        fp_b = get_configured_stack_fingerprint(self.prefs_b)
        self.assertNotEqual(fp_a, fp_b)

        # Cold-start load for Pack A must populate identity immediately and hit on second call
        invalidate_asset_cache_memory()
        atlas_a1 = load_baked_atlas_from_cache(self.prefs_a)
        atlas_a2 = load_baked_atlas_from_cache(self.prefs_a)
        mdb_a = load_baked_model_database(self.prefs_a)
        cms_a = load_colormaps_from_cache(self.prefs_a)
        self.assertIsNotNone(atlas_a1)
        self.assertIs(atlas_a1, atlas_a2)
        self.assertIsNotNone(mdb_a)
        self.assertIsNotNone(cms_a)
        self.assertEqual(open_active_asset_cache(self.prefs_a).fingerprint, fp_a)

        # Switch to Pack B WITHOUT calling invalidate_asset_cache_memory()
        cache_b = open_active_asset_cache(self.prefs_b)
        self.assertIsNotNone(cache_b)
        self.assertEqual(cache_b.fingerprint, fp_b)

        atlas_b = load_baked_atlas_from_cache(self.prefs_b)
        mdb_b = load_baked_model_database(self.prefs_b)
        cms_b = load_colormaps_from_cache(self.prefs_b)
        self.assertIsNotNone(atlas_b)
        self.assertIsNotNone(mdb_b)
        self.assertIsNotNone(cms_b)
        self.assertIsNot(atlas_b, atlas_a1)
        self.assertIsNot(mdb_b, mdb_a)
        self.assertIsNot(cms_b, cms_a)

        # Switching back to Pack A loads Pack A again
        cache_a_again = open_active_asset_cache(self.prefs_a)
        self.assertIsNotNone(cache_a_again)
        self.assertEqual(cache_a_again.fingerprint, fp_a)
        atlas_a3 = load_baked_atlas_from_cache(self.prefs_a)
        self.assertIsNotNone(atlas_a3)
        self.assertIsNot(atlas_a3, atlas_b)

    def test_world_pipeline_assets_memo_reacts_to_package_deletion(self):
        res_a = precompile_stack(self.prefs_a)
        self.assertTrue(res_a["success"])
        pkg_path = Path(res_a["package_path"])
        self.assertTrue(pkg_path.exists())

        mdb1, atlas1, _ = get_world_pipeline_assets(self.prefs_a)
        self.assertIsNotNone(mdb1)
        self.assertIsNotNone(atlas1)

        # Removing the cache file must invalidate _WORLD_PIPELINE_ASSETS_CACHE
        pkg_path.unlink()
        mdb2, atlas2, _ = get_world_pipeline_assets(self.prefs_a)
        self.assertIsNone(mdb2)
        self.assertIsNone(atlas2)


if __name__ == "__main__":
    unittest.main()
