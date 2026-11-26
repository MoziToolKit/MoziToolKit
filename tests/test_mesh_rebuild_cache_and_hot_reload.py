"""
Tests for Mesh Reconstruction Dirty Cache Validation and Hot Reload.
Verifies cache manifest fingerprint checking, timestamp tracking,
dynamic VoxelWorld/LiveSyncSession cache purging, and Blender image reloading.
"""

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

PROJECT_DIR = Path(__file__).parent.parent.resolve()
PARENT_DIR = PROJECT_DIR.parent
libmtk_release_path = PROJECT_DIR.parent / "libmozitoolkit" / "target" / "release"

for p in [str(libmtk_release_path), str(PROJECT_DIR), str(PARENT_DIR)]:
    if p not in sys.path:
        sys.path.insert(0, p)

import bpy
import MoziToolKit.operators as operators

from MoziToolKit.bridge.assets import (
    check_cache_dirty,
    get_cache_fingerprint,
    get_cache_manifest,
    get_cache_timestamp,
    invalidate_asset_cache_memory,
)
from MoziToolKit.utils.materials.builder.atlas_refresh import reload_atlas_images
from MoziToolKit.utils.assets import get_cache_dir
from MoziToolKit.bridge.sync import (
    SyncBridgeSession,
    get_sync_bridge_session,
    is_sync_available,
    reset_sync_bridge_session,
)


class TestMeshRebuildCacheAndHotReload(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            operators.register()
        except Exception:
            pass

    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.cache_dir = Path(self.tmp_dir.name)
        self.old_env = os.environ.get("MOZI_CACHE_DIR")
        os.environ["MOZI_CACHE_DIR"] = str(self.cache_dir)

    def tearDown(self):
        if self.old_env is not None:
            os.environ["MOZI_CACHE_DIR"] = self.old_env
        else:
            os.environ.pop("MOZI_CACHE_DIR", None)
        try:
            invalidate_asset_cache_memory()
        except Exception:
            pass
        self.tmp_dir.cleanup()
        reset_sync_bridge_session()

    def _create_package(self, fingerprint: str, created_at: int = 0) -> Path:
        from MoziToolKit.bridge.engine import get_libmtk
        mtk = get_libmtk()
        pkg_path = self.cache_dir / f"{fingerprint}.mtkcache"
        testing = getattr(getattr(mtk, "unstable", None), "testing", None) if mtk else None
        if testing is not None and hasattr(testing, "create_test_cache_package"):
            testing.create_test_cache_package(str(pkg_path), fingerprint, created_at)
        return pkg_path

    def test_cache_fingerprint_and_timestamp_extraction(self):
        # Empty cache
        self.assertIsNone(get_cache_manifest())
        self.assertIsNone(get_cache_fingerprint())
        self.assertEqual(get_cache_timestamp(), 0.0)

        # Write package
        self._create_package("fp_test_12345", 1728000000)

        manifest = get_cache_manifest()
        self.assertIsNotNone(manifest)
        self.assertEqual(manifest["fingerprint"], "fp_test_12345")
        self.assertEqual(get_cache_fingerprint(), "fp_test_12345")
        self.assertEqual(get_cache_timestamp(), 1728000000.0)

    def test_check_cache_dirty_detection(self):
        self._create_package("fp_v1", 1000)

        # Initial clean match
        is_dirty, fp, ts = check_cache_dirty(cached_fingerprint="fp_v1", cached_timestamp=1000.0)
        self.assertFalse(is_dirty)
        self.assertEqual(fp, "fp_v1")
        self.assertEqual(ts, 1000.0)

        # Fingerprint mismatch
        is_dirty, fp, ts = check_cache_dirty(cached_fingerprint="fp_v0", cached_timestamp=1000.0)
        self.assertTrue(is_dirty)

        # Timestamp mismatch
        is_dirty, fp, ts = check_cache_dirty(cached_fingerprint="fp_v1", cached_timestamp=999.0)
        self.assertTrue(is_dirty)

        # Disk cache update to v2
        old_pkg = self.cache_dir / "fp_v1.mtkcache"
        if old_pkg.exists():
            old_pkg.unlink()
        self._create_package("fp_v2", 2000)

        is_dirty, fp, ts = check_cache_dirty(cached_fingerprint="fp_v1", cached_timestamp=1000.0)
        self.assertTrue(is_dirty)
        self.assertEqual(fp, "fp_v2")
        self.assertEqual(ts, 2000.0)

    def test_sync_bridge_session_dirty_cache_hot_reload(self):
        self._create_package("initial_fp", 5000)

        session = SyncBridgeSession()
        session._cached_manifest_fingerprint = "initial_fp"
        session._cached_manifest_timestamp = 5000.0

        # No changes: returns False
        self.assertFalse(session.check_and_reload_dirty_cache())

        # Disk cache updated
        old_pkg = self.cache_dir / "initial_fp.mtkcache"
        if old_pkg.exists():
            old_pkg.unlink()
        self._create_package("recompiled_fp", 6000)

        # Dirty cache detected: returns True and updates cached state
        reloaded = session.check_and_reload_dirty_cache()
        self.assertTrue(reloaded)
        self.assertEqual(session._cached_manifest_fingerprint, "recompiled_fp")
        self.assertEqual(session._cached_manifest_timestamp, 6000.0)

        # Subsequent check without modifications: returns False
        self.assertFalse(session.check_and_reload_dirty_cache())

    def test_sync_bridge_session_hot_reload_preserves_colormaps_and_smooth_biome_blend(self):
        """Hot-reload must reload colormaps and forward colormaps + smooth_biome_blend to MesherConfig."""
        from unittest.mock import patch
        from MoziToolKit.bridge.engine import get_libmtk

        mtk = get_libmtk()
        if mtk is None:
            self.skipTest("libmtk_py not loaded")

        import libmtk_py
        session = SyncBridgeSession()
        session._cached_manifest_fingerprint = "fp_old"
        session._cached_manifest_timestamp = 1.0
        session._config_params = {
            "enable_ao": True,
            "mesh_fluids": True,
            "num_threads": 2,
            "enable_animated_textures": False,
            "smooth_biome_blend": False,
            "unknown_block_strategy": "IGNORE_AS_AIR",
        }

        hot_reload_calls = []
        session._session = SimpleNamespace(
            hot_reload=lambda cfg, db: hot_reload_calls.append((cfg, db))
        )

        sentinel_colormaps = object()
        sentinel_mdb = object()
        sentinel_atlas = object()
        sentinel_biome = object()
        captured_cfg_kwargs = []

        class FakeConfig:
            def __init__(self, **kwargs):
                captured_cfg_kwargs.append(kwargs)

        fake_mtk = SimpleNamespace(MesherConfig=FakeConfig)

        with patch("MoziToolKit.bridge.sync.check_cache_dirty", return_value=(True, "fp_new", 2.0)), \
             patch("MoziToolKit.bridge.sync.load_baked_model_database", return_value=sentinel_mdb), \
             patch("MoziToolKit.bridge.sync.load_baked_atlas_from_cache", return_value=sentinel_atlas), \
             patch("MoziToolKit.bridge.sync.load_biome_resolver_from_cache", return_value=sentinel_biome), \
             patch("MoziToolKit.bridge.sync.load_colormaps_from_cache", return_value=sentinel_colormaps), \
             patch("MoziToolKit.bridge.sync.get_configured_ctm_solver", return_value=None), \
             patch("MoziToolKit.bridge.sync.get_libmtk", return_value=fake_mtk):
            reloaded = session.check_and_reload_dirty_cache()

        self.assertTrue(reloaded)
        self.assertIs(session._colormaps, sentinel_colormaps)
        self.assertEqual(len(hot_reload_calls), 1)
        self.assertEqual(len(captured_cfg_kwargs), 1)
        cfg_kwargs = captured_cfg_kwargs[0]
        self.assertIs(cfg_kwargs.get("colormaps"), sentinel_colormaps)
        self.assertIs(cfg_kwargs.get("smooth_biome_blend"), False)
        self.assertIs(cfg_kwargs.get("enable_animated_textures"), False)
        self.assertEqual(cfg_kwargs.get("unknown_block_strategy"), "IGNORE_AS_AIR")
        self.assertIsNotNone(mtk)
        self.assertTrue(hasattr(libmtk_py, "MesherConfig"))

    def test_native_voxel_world_clear_cache(self):
        if not is_sync_available():
            self.skipTest("libmtk_py is not loaded")

        from MoziToolKit.bridge.engine import get_libmtk
        mtk = get_libmtk()
        if not hasattr(mtk, "VoxelWorld"):
            self.skipTest("VoxelWorld not available in bindings")

        try:
            from _assets import require_assets
            require_assets()
        except Exception:
            pass

        try:
            world = mtk.VoxelWorld.create_debug_world(
                states=["minecraft:stone", "minecraft:granite", "minecraft:oak_stairs"]
            )
        except ValueError as e:
            self.skipTest(f"Debug world unavailable: {e}")
        mesh = world.rebuild_all()
        self.assertGreater(mesh.vertex_count, 0)
        self.assertGreater(world.get_world_mesh().vertex_count, 0)

        # Call clear_cache()
        world.clear_cache()
        # World mesh and section mesh cache must be purged
        self.assertEqual(world.get_world_mesh().vertex_count, 0)
        self.assertEqual(len(world.get_world_mesh().used_materials()), 0)

        # Re-mesh after cache purge
        re_mesh = world.rebuild_all()
        self.assertGreater(re_mesh.vertex_count, 0)

    def test_reload_atlas_images_helper(self):
        atlas_dir = self.cache_dir / "atlas"
        atlas_dir.mkdir(parents=True, exist_ok=True)
        img_path = atlas_dir / "blocks_chunk_001.png"

        # Create Blender image pointing to atlas path
        img = bpy.data.images.new("blocks_chunk_001.png", width=16, height=16)
        img.filepath_raw = str(img_path)
        img.file_format = "PNG"
        img.save()

        count = reload_atlas_images()
        self.assertGreaterEqual(count, 1)

        bpy.data.images.remove(img)

    def test_sync_rebuild_operator_with_dirty_cache(self):
        from MoziToolKit.bridge.engine import get_libmtk
        mtk = get_libmtk()
        if not mtk or not hasattr(mtk, "LiveSyncSession"):
            self.skipTest("LiveSyncSession not available")

        session = get_sync_bridge_session()
        try:
            world = mtk.VoxelWorld.create_debug_world(
                states=["minecraft:stone", "minecraft:granite", "minecraft:oak_stairs"]
            )
        except ValueError as e:
            self.skipTest(f"Debug world unavailable: {e}")
        real_mesh = world.rebuild_all()

        try:
            test_root = bpy.data.objects.new("Yefira_World_DirtyTest", None)
            test_root["mtk:is_container"] = True
            test_root["mtk:container_type"] = "SYNC"
            test_root["mtk:is_yefira_world"] = True
            bpy.context.scene.collection.objects.link(test_root)
            bpy.context.view_layer.objects.active = test_root

            session._session = SimpleNamespace(is_active=True, hot_reload=lambda *a, **kw: None)
            session._is_active = True
            session._cached_manifest_fingerprint = "old_fp"
            session._cached_manifest_timestamp = 100.0

            with patch.object(session, "get_world_mesh", return_value=real_mesh), \
                 patch.object(session, "get_storage", return_value=world.get_storage()), \
                 patch("MoziToolKit.bridge.sync.load_baked_model_database", return_value=object()), \
                 patch("MoziToolKit.bridge.sync.load_baked_atlas_from_cache", return_value=object()), \
                 patch("MoziToolKit.operators.mesh.op_rebuild_mesh.can_execute_asset_pipeline", return_value=(True, "")), \
                 patch("MoziToolKit.operators.mesh.op_rebuild_mesh.check_asset_cache_status", return_value="READY"), \
                 patch("MoziToolKit.utils.world.sync_container.update_world_mesh", return_value=(getattr(real_mesh, "vertex_count", 100), 50)):
                # Write new package to simulate rebaking
                self._create_package("new_recompiled_fp", 200)

                # Run unified rebuild operator synchronously
                res = bpy.ops.mozi.rebuild_mesh("EXEC_DEFAULT", run_async=False, target_container=test_root.name)
                self.assertEqual(res, {'FINISHED'})

                # Verify dirty cache was detected and reloaded
                self.assertEqual(session._cached_manifest_fingerprint, "new_recompiled_fp")
                self.assertEqual(session._cached_manifest_timestamp, 200.0)
        finally:
            if "Yefira_World_DirtyTest" in bpy.data.objects:
                bpy.data.objects.remove(bpy.data.objects["Yefira_World_DirtyTest"], do_unlink=True)
            reset_sync_bridge_session()

    def test_pack_stack_enable_disable_fingerprint_matching(self):
        """
        Verifies that when toggling enabled state on resource packs, the active cache
        strictly resolves to the package matching the active stack fingerprint,
        and never mistakenly picks an older or newer mtime package from a different configuration.
        """
        import types
        from MoziToolKit.bridge.assets import (
            get_active_cache_package_path,
            invalidate_asset_cache_memory,
        )

        pack_a_dir = self.cache_dir / "pack_a"
        pack_b_dir = self.cache_dir / "pack_b"
        pack_a_dir.mkdir(parents=True, exist_ok=True)
        pack_b_dir.mkdir(parents=True, exist_ok=True)

        entry_a = types.SimpleNamespace(path=str(pack_a_dir), name="PackVanilla", enabled=True, pack_type="VANILLA")
        entry_b = types.SimpleNamespace(path=str(pack_b_dir), name="PackAddon", enabled=False, pack_type="RESOURCE_PACK")

        mock_prefs = types.SimpleNamespace(
            resource_packs=[entry_a, entry_b],
            thread_count=0,
            enable_ctm=False,
        )

        # 1. With PackAddon disabled: fingerprint is vanilla only
        from MoziToolKit.bridge.assets.pack import get_configured_pack_stack
        stack_vanilla = get_configured_pack_stack(mock_prefs)
        fp_vanilla = stack_vanilla.compute_stack_fingerprint()

        # Bake / create package for vanilla
        pkg_vanilla = self._create_package(fp_vanilla, 1000)

        # 2. Enable PackAddon: fingerprint includes addon
        entry_b.enabled = True
        stack_full = get_configured_pack_stack(mock_prefs)
        fp_full = stack_full.compute_stack_fingerprint()
        self.assertNotEqual(fp_vanilla, fp_full)

        # Bake / create package for full stack with newer mtime
        pkg_full = self._create_package(fp_full, 2000)

        # Now disk has BOTH pkg_vanilla and pkg_full.
        # Current prefs has PackAddon ENABLED.
        invalidate_asset_cache_memory()
        active_pkg = get_active_cache_package_path(mock_prefs)
        self.assertIsNotNone(active_pkg)
        self.assertEqual(active_pkg.resolve(), pkg_full.resolve())
        self.assertEqual(get_cache_fingerprint(mock_prefs), fp_full)

        # 3. User switches back to disabling PackAddon (only Vanilla enabled).
        # Even though pkg_full has a NEWER mtime (2000 > 1000), it MUST match pkg_vanilla!
        entry_b.enabled = False
        invalidate_asset_cache_memory()

        active_pkg = get_active_cache_package_path(mock_prefs)
        self.assertIsNotNone(active_pkg)
        self.assertEqual(active_pkg.resolve(), pkg_vanilla.resolve())
        self.assertEqual(get_cache_fingerprint(mock_prefs), fp_vanilla)

        # 4. User re-enables PackAddon: must seamlessly switch back to pkg_full
        entry_b.enabled = True
        invalidate_asset_cache_memory()

        active_pkg = get_active_cache_package_path(mock_prefs)
        self.assertIsNotNone(active_pkg)
        self.assertEqual(active_pkg.resolve(), pkg_full.resolve())
        self.assertEqual(get_cache_fingerprint(mock_prefs), fp_full)

    def test_world_session_cache_invalidation_on_rebuild(self):
        """
        Verifies that clearing cache or invalidating memory drops cached VoxelWorld sessions
        so geometry and materials are reconstructed with fresh atlas data.
        """
        from MoziToolKit.bridge.world import _WORLD_SESSIONS, _WORLD_SESSION_META, clear_world_sessions
        from MoziToolKit.bridge.assets import invalidate_asset_cache_memory
        from MoziToolKit.utils.assets import clear_cache

        # Insert dummy mock session
        mock_world = SimpleNamespace()
        _WORLD_SESSIONS["test_session"] = mock_world
        _WORLD_SESSION_META["test_session"] = {"fingerprint": "fp1", "timestamp": 10.0}

        self.assertIn("test_session", _WORLD_SESSIONS)
        self.assertIn("test_session", _WORLD_SESSION_META)

        # Invalidation must clear sessions
        invalidate_asset_cache_memory()
        self.assertEqual(len(_WORLD_SESSIONS), 0)
        self.assertEqual(len(_WORLD_SESSION_META), 0)

        # Re-insert and test clear_cache
        _WORLD_SESSIONS["test_session_2"] = mock_world
        _WORLD_SESSION_META["test_session_2"] = {"fingerprint": "fp2", "timestamp": 20.0}
        clear_cache()
        self.assertEqual(len(_WORLD_SESSIONS), 0)
        self.assertEqual(len(_WORLD_SESSION_META), 0)


class _ActiveNativeSessionProxy:
    """Thin proxy forwarding all calls to a real Rust LiveSyncSession while keeping is_active=True."""

    def __init__(self, native_session):
        self._native = native_session
        self.is_active = True
        self.is_connected = True
        self.status = "CONNECTED"
        self.client_generation = 1
        self.current_sequence = 1
        self.current_epoch = 1
        self.state_hash = 12345
        self.server_uuid = "0" * 32

    def hot_reload(self, config=None, model_db=None):
        return self._native.hot_reload(config, model_db)

    def get_world_mesh(self):
        return self._native.get_world_mesh()

    def get_storage(self):
        return self._native.get_storage()

    def load_from_point_cloud(self, point_cloud):
        return self._native.load_from_point_cloud(point_cloud)

    def set_skin_cache_dir(self, cache_dir):
        return self._native.set_skin_cache_dir(cache_dir)

    def stop(self):
        pass


def _write_e2e_pack(pack_dir: Path, stone_rgba: tuple[int, int, int, int]) -> None:
    import struct
    import zlib

    def _png(color: tuple[int, int, int, int]) -> bytes:
        raw = bytearray()
        px = bytes(color)
        for _ in range(16):
            raw.append(0)
            raw.extend(px * 16)
        comp = zlib.compress(bytes(raw))

        def _chunk(tag: bytes, data: bytes) -> bytes:
            return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

        return (
            b"\x89PNG\r\n\x1a\n"
            + _chunk(b"IHDR", struct.pack(">IIBBBBB", 16, 16, 8, 6, 0, 0, 0))
            + _chunk(b"IDAT", comp)
            + _chunk(b"IEND", b"")
        )

    tex_dir = pack_dir / "assets" / "minecraft" / "textures" / "block"
    models_dir = pack_dir / "assets" / "minecraft" / "models" / "block"
    bs_dir = pack_dir / "assets" / "minecraft" / "blockstates"
    cmap_dir = pack_dir / "assets" / "minecraft" / "textures" / "colormap"
    for d in (tex_dir, models_dir, bs_dir, cmap_dir):
        d.mkdir(parents=True, exist_ok=True)

    for canonical in ("stone", "dirt", "oak_planks"):
        rgba = stone_rgba if canonical == "stone" else (128, 128, 128, 255)
        (tex_dir / f"{canonical}.png").write_bytes(_png(rgba))
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

    (cmap_dir / "grass.png").write_bytes(_png((120, 200, 80, 255)))
    (cmap_dir / "foliage.png").write_bytes(_png((120, 200, 80, 255)))
    (pack_dir / "pack.mcmeta").write_text(
        json.dumps({"pack": {"pack_format": 15, "description": "E2E Test Pack"}}),
        encoding="utf-8",
    )


class TestLiveSyncMaterialHotReloadE2E(unittest.TestCase):
    """Real Blender + libmtk end-to-end tests for pack switching & cache clearing during Live Sync."""

    def setUp(self):
        from MoziToolKit.bridge.engine import get_libmtk
        from MoziToolKit.utils.assets.guard import invalidate_guard_cache

        self.mtk = get_libmtk()
        if self.mtk is None or not hasattr(self.mtk, "LiveSyncSession"):
            self.skipTest("libmtk_py with LiveSyncSession required")

        self.tmp_dir = Path(tempfile.mkdtemp(prefix="mtk_hot_reload_e2e_"))
        self.cache_dir = self.tmp_dir / "cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.pack_a = self.tmp_dir / "pack_red"
        self.pack_b = self.tmp_dir / "pack_green"
        _write_e2e_pack(self.pack_a, (240, 20, 20, 255))
        _write_e2e_pack(self.pack_b, (20, 240, 20, 255))

        self.prefs_a = SimpleNamespace(
            resource_packs=[SimpleNamespace(path=str(self.pack_a), enabled=True, pack_type="VANILLA", name="PackA")],
            cache_dir=str(self.cache_dir),
            thread_count=1,
            enable_ctm=False,
            enable_animated_textures=False,
            smooth_biome_blend=True,
            unknown_block_strategy="FALLBACK_CUBE",
        )
        self.prefs_b = SimpleNamespace(
            resource_packs=[SimpleNamespace(path=str(self.pack_b), enabled=True, pack_type="VANILLA", name="PackB")],
            cache_dir=str(self.cache_dir),
            thread_count=1,
            enable_ctm=False,
            enable_animated_textures=False,
            smooth_biome_blend=True,
            unknown_block_strategy="FALLBACK_CUBE",
        )

        self._old_env = os.environ.get("MOZI_CACHE_DIR")
        os.environ["MOZI_CACHE_DIR"] = str(self.cache_dir)
        invalidate_asset_cache_memory()
        invalidate_guard_cache()
        reset_sync_bridge_session()

        self._created_objects = []
        self._created_meshes = []
        self._created_materials = set(bpy.data.materials.keys())
        self._created_images = set(bpy.data.images.keys())

    def tearDown(self):
        import shutil
        from MoziToolKit.utils.assets.guard import invalidate_guard_cache

        reset_sync_bridge_session()
        for obj in reversed(self._created_objects):
            try:
                if obj.name in bpy.data.objects:
                    bpy.data.objects.remove(obj, do_unlink=True)
            except Exception:
                pass
        for mesh in reversed(self._created_meshes):
            try:
                if mesh.name in bpy.data.meshes:
                    bpy.data.meshes.remove(mesh)
            except Exception:
                pass
        for m_name in list(bpy.data.materials.keys()):
            if m_name not in self._created_materials:
                try:
                    bpy.data.materials.remove(bpy.data.materials[m_name])
                except Exception:
                    pass
        for i_name in list(bpy.data.images.keys()):
            if i_name not in self._created_images:
                try:
                    bpy.data.images.remove(bpy.data.images[i_name])
                except Exception:
                    pass

        invalidate_asset_cache_memory()
        invalidate_guard_cache()
        if self._old_env is None:
            os.environ.pop("MOZI_CACHE_DIR", None)
        else:
            os.environ["MOZI_CACHE_DIR"] = self._old_env
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _setup_live_session_and_container(self, prefs):
        from MoziToolKit.bridge.assets import (
            load_baked_atlas_from_cache,
            load_baked_model_database,
            load_biome_resolver_from_cache,
            load_colormaps_from_cache,
            precompile_stack,
        )
        from MoziToolKit.bridge.world.mesher import build_mesher_config

        precompile_stack(prefs)
        model_db = load_baked_model_database(prefs)
        atlas = load_baked_atlas_from_cache(prefs)
        biome = load_biome_resolver_from_cache(prefs)
        colormaps = load_colormaps_from_cache(prefs)

        storage = self.mtk.VoxelStorage()
        storage.set_block(0, 0, 0, "minecraft:stone")
        cfg = build_mesher_config(
            self.mtk,
            atlas=atlas,
            biome_resolver=biome,
            colormaps=colormaps,
            enable_animated_textures=False,
        )
        cloud = storage.to_point_cloud(cfg)
        native_session = self.mtk.LiveSyncSession(
            config=cfg,
            culler=self.mtk.FaceCuller(),
            model_db=model_db,
        )
        native_session.load_from_point_cloud(cloud)

        session = get_sync_bridge_session()
        session._session = _ActiveNativeSessionProxy(native_session)
        session._is_active = True
        session._model_db = model_db
        session._atlas = atlas
        session._biome_resolver = biome
        session._colormaps = colormaps
        session._cached_manifest_fingerprint = get_cache_fingerprint(prefs)
        session._cached_manifest_timestamp = get_cache_timestamp(prefs)
        session._config_params = {
            "enable_ao": True,
            "mesh_fluids": True,
            "num_threads": 1,
            "enable_animated_textures": False,
            "smooth_biome_blend": True,
            "unknown_block_strategy": "FALLBACK_CUBE",
        }

        root = bpy.data.objects.new("Yefira_World_E2E", None)
        root["mtk:is_container"] = True
        root["mtk:container_type"] = "SYNC"
        root["mtk:is_yefira_world"] = True
        bpy.context.scene.collection.objects.link(root)
        self._created_objects.append(root)

        mesh_data_blk = bpy.data.meshes.new("Yefira_World_E2E_Mesh")
        self._created_meshes.append(mesh_data_blk)
        mesh_obj = bpy.data.objects.new("Yefira_World_E2E_Mesh", mesh_data_blk)
        mesh_obj["mtk:is_yefira_world"] = True
        mesh_obj["mtk:is_yefira_mesh"] = True
        mesh_obj.parent = root
        bpy.context.scene.collection.objects.link(mesh_obj)
        self._created_objects.append(mesh_obj)

        return session, root, mesh_obj

    def _run_rebuild(self, root, prefs):
        from MoziToolKit.operators.mesh.op_rebuild_mesh import MOZI_OT_rebuild_mesh

        class _Op:
            def __init__(self, target_name):
                self.target_container = target_name
                self.run_async = False
                self.enable_ao = True
                self.mesh_fluids = True
                self.weld_mode = "manifold"
                self.origin_centered = True
                self.force = False
                self._runner = None
                self.reports = []
                self.report = lambda *args: self.reports.append(args)

            def execute(self, ctx):
                return MOZI_OT_rebuild_mesh.execute(self, ctx)

        ctx = SimpleNamespace(
            active_object=root,
            selected_objects=[root],
            mode="OBJECT",
            collection=bpy.context.scene.collection,
            scene=SimpleNamespace(mozi_active_sync_container_name=root.name),
        )
        with patch("MoziToolKit.operators.mesh.op_rebuild_mesh.get_prefs", return_value=prefs):
            res = _Op(root.name).execute(ctx)
        self.assertEqual(res, {"FINISHED"})

    def _sample_stone_image_rgb(self, mesh_obj) -> tuple[float, float, float]:
        mat = mesh_obj.data.materials[0]
        albedo_node = mat.node_tree.nodes.get("Atlas Albedo Texture")
        self.assertIsNotNone(albedo_node)
        img = albedo_node.image
        self.assertIsNotNone(img)
        uv_layer = mesh_obj.data.uv_layers[0]
        poly = mesh_obj.data.polygons[0]
        loop_indices = list(poly.loop_indices)
        u = sum(uv_layer.data[li].uv[0] for li in loop_indices) / len(loop_indices)
        v = sum(uv_layer.data[li].uv[1] for li in loop_indices) / len(loop_indices)
        w, h = img.size[0], img.size[1]
        px = min(w - 1, max(0, int(u * w)))
        py = min(h - 1, max(0, int(v * h)))
        pixels = list(img.pixels)
        idx = (py * w + px) * 4
        return (pixels[idx], pixels[idx + 1], pixels[idx + 2])

    def test_e2e_switch_pack_without_clear_cache_updates_materials_and_images(self):
        """Switching packs WITHOUT clearing cache must detect dirty state, auto-precompile, and update materials."""
        from MoziToolKit.bridge.assets import get_configured_stack_fingerprint

        fp_a = get_configured_stack_fingerprint(self.prefs_a)
        fp_b = get_configured_stack_fingerprint(self.prefs_b)
        self.assertNotEqual(fp_a, fp_b)

        session, root, mesh_obj = self._setup_live_session_and_container(self.prefs_a)
        self._run_rebuild(root, self.prefs_a)

        self.assertEqual(mesh_obj.data.materials[0].get("mtk_stack_fingerprint"), fp_a)
        r1, g1, _ = self._sample_stone_image_rgb(mesh_obj)
        self.assertGreater(r1, g1, "Initial pack A stone texture must be red")

        # Switch to Pack B WITHOUT clearing cache on disk
        is_dirty, fp_now, _ = check_cache_dirty(
            session.manifest_fingerprint, session._cached_manifest_timestamp, prefs=self.prefs_b
        )
        self.assertTrue(is_dirty)
        self.assertIsNone(fp_now)

        self._run_rebuild(root, self.prefs_b)

        self.assertEqual(session.manifest_fingerprint, fp_b)
        self.assertEqual(mesh_obj.data.materials[0].get("mtk_stack_fingerprint"), fp_b)
        r2, g2, _ = self._sample_stone_image_rgb(mesh_obj)
        self.assertGreater(g2, r2, "Rebuilt pack B stone texture must update to green without restarting Blender")

    def test_e2e_clear_cache_recompile_then_rebuild_updates_materials_and_images(self):
        """Clearing cache, switching packs, recompiling, then rebuilding must bypass stale material fast-path."""
        from MoziToolKit.bridge.assets import get_configured_stack_fingerprint, precompile_stack
        from MoziToolKit.utils.assets import clear_cache

        fp_a = get_configured_stack_fingerprint(self.prefs_a)
        fp_b = get_configured_stack_fingerprint(self.prefs_b)

        session, root, mesh_obj = self._setup_live_session_and_container(self.prefs_a)
        self._run_rebuild(root, self.prefs_a)
        self.assertEqual(mesh_obj.data.materials[0].get("mtk_stack_fingerprint"), fp_a)

        clear_cache(self.prefs_a)
        precompile_stack(self.prefs_b)

        self._run_rebuild(root, self.prefs_b)

        self.assertEqual(session.manifest_fingerprint, fp_b)
        self.assertEqual(mesh_obj.data.materials[0].get("mtk_stack_fingerprint"), fp_b)
        r2, g2, _ = self._sample_stone_image_rgb(mesh_obj)
        self.assertGreater(g2, r2, "Material fast-path must exit on fingerprint mismatch and refresh pixels")

    def test_e2e_clear_cache_without_manual_precompile_then_rebuild(self):
        """Clearing cache and rebuilding directly must not wipe LiveSyncSession with None assets."""
        from MoziToolKit.bridge.assets import get_configured_stack_fingerprint
        from MoziToolKit.utils.assets import clear_cache

        fp_b = get_configured_stack_fingerprint(self.prefs_b)
        session, root, mesh_obj = self._setup_live_session_and_container(self.prefs_a)
        self._run_rebuild(root, self.prefs_a)

        clear_cache(self.prefs_a)

        # Calling check_and_reload_dirty_cache while cache is missing must NOT wipe session._atlas
        self.assertFalse(session.check_and_reload_dirty_cache(prefs=self.prefs_b))
        self.assertIsNotNone(session.atlas)

        # Rebuilding directly must auto-precompile Pack B, hot-reload session, and update materials
        self._run_rebuild(root, self.prefs_b)

        self.assertEqual(session.manifest_fingerprint, fp_b)
        self.assertEqual(mesh_obj.data.materials[0].get("mtk_stack_fingerprint"), fp_b)
        r2, g2, _ = self._sample_stone_image_rgb(mesh_obj)
        self.assertGreater(g2, r2)


if __name__ == "__main__":
    unittest.main()
