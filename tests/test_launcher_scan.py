"""
Tests for launcher and game instance discovery (``utils.launcher_scan``).

Tests synthetic directory structures for Prism Launcher, Official Launcher, HMCL (isolated),
Modrinth App, CurseForge, and custom directories.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from MoziToolKit.utils.launcher_scan import (
    ISOLATION_ISOLATED,
    ISOLATION_SHARED,
    LAUNCHER_CURSEFORGE,
    LAUNCHER_CUSTOM,
    LAUNCHER_HMCL,
    LAUNCHER_MODRINTH,
    LAUNCHER_MULTIMC,
    LAUNCHER_OFFICIAL,
    LAUNCHER_PRISM,
    LOADER_FABRIC,
    LOADER_FORGE,
    LOADER_NEOFORGE,
    LOADER_VANILLA,
    DiscoveredInstance,
    filter_functional_mods,
    scan_all_instances,
    scan_curseforge_instances,
    scan_custom_directory,
    scan_modrinth_instances,
    scan_official_instances,
    scan_prism_instances,
)


def _make_block_mod_jar(path: Path, mod_id: str = "testmod") -> None:
    import zipfile
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("fabric.mod.json", f'{{"id": "{mod_id}"}}')
        z.writestr(f"assets/{mod_id}/blockstates/{mod_id}.json", "{}")
        z.writestr(f"assets/{mod_id}/models/block/{mod_id}.json", "{}")
        z.writestr(f"assets/{mod_id}/textures/block/{mod_id}.png", b"\x89PNG\r\n\x1a\n")


class TestLauncherScan(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_scan_prism_launcher_instances(self):
        prism_root = self.root / "PrismLauncher"
        instances_dir = prism_root / "instances"
        instances_dir.mkdir(parents=True)

        # Instance 1: Fabric 1.21.1
        inst1 = instances_dir / "Fab121"
        inst1.mkdir()
        (inst1 / "instance.cfg").write_text("name=My Fabric World\niconKey=fabric\nIntendedVersion=1.21.1\n", encoding="utf-8")
        mmc_data = {
            "components": [
                {"uid": "net.minecraft", "cachedVersion": "1.21.1"},
                {"uid": "net.fabricmc.fabric-loader", "cachedVersion": "0.16.0"},
            ],
            "formatVersion": 1,
        }
        (inst1 / "mmc-pack.json").write_text(json.dumps(mmc_data), encoding="utf-8")
        mc_dir = inst1 / "minecraft"
        (mc_dir / "mods").mkdir(parents=True)
        (mc_dir / "resourcepacks").mkdir(parents=True)

        # 1. Functional mod (sodium): no block assets
        import zipfile
        with zipfile.ZipFile(mc_dir / "mods" / "sodium.jar", "w") as z:
            z.writestr("fabric.mod.json", '{"id": "sodium"}')
            z.writestr("assets/sodium/lang/en_us.json", "{}")

        # 2. Block asset mod (waystones): contains blockstates
        with zipfile.ZipFile(mc_dir / "mods" / "waystones.jar", "w") as z:
            z.writestr("fabric.mod.json", '{"id": "waystones"}')
            z.writestr("assets/waystones/blockstates/waystone.json", "{}")
            z.writestr("assets/waystones/models/block/waystone.json", "{}")
            z.writestr("assets/waystones/textures/block/waystone.png", b"\x89PNG\r\n\x1a\n")

        (mc_dir / "resourcepacks" / "faithful.zip").write_bytes(b"dummy")

        # Mock cached client JAR in Prism libraries
        lib_dir = prism_root / "libraries" / "com" / "mojang" / "minecraft" / "1.21.1"
        lib_dir.mkdir(parents=True)
        client_jar = lib_dir / "minecraft-1.21.1-client.jar"
        client_jar.write_bytes(b"dummy_jar")

        results = scan_prism_instances(prism_root)
        self.assertEqual(len(results), 1)
        inst = results[0]
        self.assertEqual(inst.name, "My Fabric World")
        self.assertEqual(inst.launcher_kind, LAUNCHER_PRISM)
        self.assertEqual(inst.isolation_mode, ISOLATION_ISOLATED)
        self.assertEqual(inst.minecraft_version, "1.21.1")
        self.assertEqual(inst.loader, LOADER_FABRIC)
        self.assertEqual(inst.client_jar, client_jar)
        # Fast initial scan captures all candidate JARs without deep unpack
        self.assertEqual(len(inst.mods), 2)
        # On-demand asset filtering separates block mods from functional mods
        asset_mods, functional_mods = filter_functional_mods(inst.mods)
        self.assertEqual(len(asset_mods), 1)
        self.assertEqual(asset_mods[0].name, "waystones.jar")
        self.assertEqual(len(functional_mods), 1)
        self.assertEqual(functional_mods[0].name, "sodium.jar")
        self.assertEqual(len(inst.resource_packs), 1)

    def test_scan_official_launcher_shared_and_hmcl_isolated(self):
        mc_root = self.root / ".minecraft"
        versions_dir = mc_root / "versions"
        versions_dir.mkdir(parents=True)

        # Global shared packs/mods
        (mc_root / "resourcepacks").mkdir()
        (mc_root / "mods").mkdir()
        (mc_root / "resourcepacks" / "global_pack.zip").write_bytes(b"dummy")
        (mc_root / "mods" / "global_mod.jar").write_bytes(b"dummy")

        # Version 1: Standard vanilla release (Shared mode)
        v1 = versions_dir / "1.21"
        v1.mkdir()
        (v1 / "1.21.json").write_text(json.dumps({"id": "1.21", "type": "release"}), encoding="utf-8")
        (v1 / "1.21.jar").write_bytes(b"dummy_jar")

        # Version 2: HMCL isolated version
        v2 = versions_dir / "1.21.4-Forge"
        v2.mkdir()
        (v2 / "1.21.4-Forge.json").write_text(
            json.dumps({"id": "1.21.4-Forge", "inheritsFrom": "1.21.4", "mainClass": "cpw.mods.bootstraplauncher.BootstrapLauncher"}),
            encoding="utf-8",
        )
        (v2 / ".hmcl").touch()
        (v2 / "mods").mkdir()
        _make_block_mod_jar(v2 / "mods" / "isolated_mod.jar", "isolated")

        results = scan_official_instances(mc_root)
        self.assertEqual(len(results), 2)

        v1_inst = next(i for i in results if i.name == "1.21")
        self.assertEqual(v1_inst.launcher_kind, LAUNCHER_OFFICIAL)
        self.assertEqual(v1_inst.isolation_mode, ISOLATION_SHARED)
        self.assertEqual(v1_inst.loader, LOADER_VANILLA)
        self.assertEqual(len(v1_inst.resource_packs), 1)

        v2_inst = next(i for i in results if i.name == "1.21.4-Forge")
        self.assertEqual(v2_inst.launcher_kind, LAUNCHER_HMCL)
        self.assertEqual(v2_inst.isolation_mode, ISOLATION_ISOLATED)
        self.assertEqual(v2_inst.loader, LOADER_NEOFORGE)
        self.assertEqual(len(v2_inst.mods), 1)
        self.assertEqual(v2_inst.game_dir, v2)

    def test_scan_modrinth_instances(self):
        modrinth_root = self.root / "com.modrinth.theseus"
        profiles_dir = modrinth_root / "profiles"
        profiles_dir.mkdir(parents=True)

        p1 = profiles_dir / "my-pack"
        p1.mkdir()
        p1_data = {
            "name": "Cobblemon Adventure",
            "game_version": "1.20.1",
            "loader": "fabric",
            "icon": "icon.png",
        }
        (p1 / "profile.json").write_text(json.dumps(p1_data), encoding="utf-8")
        (p1 / "mods").mkdir()
        _make_block_mod_jar(p1 / "mods" / "cobblemon.jar", "cobblemon")

        results = scan_modrinth_instances(modrinth_root)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].name, "Cobblemon Adventure")
        self.assertEqual(results[0].launcher_kind, LAUNCHER_MODRINTH)
        self.assertEqual(results[0].isolation_mode, ISOLATION_ISOLATED)
        self.assertEqual(results[0].minecraft_version, "1.20.1")
        self.assertEqual(results[0].loader, LOADER_FABRIC)
        self.assertEqual(len(results[0].mods), 1)

    def test_scan_curseforge_instances(self):
        cf_root = self.root / "curseforge" / "Instances"
        cf_root.mkdir(parents=True)

        p1 = cf_root / "RLCraft"
        p1.mkdir()
        cf_data = {
            "name": "RLCraft Dregora",
            "gameVersion": "1.12.2",
            "baseModLoader": {"name": "forge-14.23.5.2860"},
        }
        (p1 / "minecraftinstance.json").write_text(json.dumps(cf_data), encoding="utf-8")
        (p1 / "mods").mkdir()
        _make_block_mod_jar(p1 / "mods" / "toughasnails.jar", "toughasnails")

        results = scan_curseforge_instances(cf_root)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].name, "RLCraft Dregora")
        self.assertEqual(results[0].launcher_kind, LAUNCHER_CURSEFORGE)
        self.assertEqual(results[0].loader, LOADER_FORGE)
        self.assertEqual(len(results[0].mods), 1)

    def test_scan_custom_directory(self):
        custom_inst = self.root / "MyCustomGame"
        custom_inst.mkdir()
        (custom_inst / "instance.cfg").write_text("name=Custom Prism Export\n", encoding="utf-8")
        (custom_inst / "minecraft").mkdir()
        (custom_inst / "minecraft" / "mods").mkdir()
        _make_block_mod_jar(custom_inst / "minecraft" / "mods" / "test.jar", "test")

        results = scan_custom_directory(custom_inst)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].name, "Custom Prism Export")
        self.assertEqual(len(results[0].mods), 1)

    def test_scan_all_instances_executes_safely(self):
        # Executes safely with sandbox root override to avoid hitting host disks
        from unittest.mock import patch
        mock_roots = {
            LAUNCHER_OFFICIAL: [],
            LAUNCHER_PRISM: [self.root / "PrismLauncher"],
            LAUNCHER_MULTIMC: [],
            LAUNCHER_MODRINTH: [],
            LAUNCHER_CURSEFORGE: [],
        }
        with patch("MoziToolKit.utils.launcher_scan.get_default_search_roots", return_value=mock_roots):
            instances = scan_all_instances(custom_directories=[self.root / "MyCustomGame"])
            self.assertIsInstance(instances, list)
            for inst in instances:
                self.assertIsInstance(inst, DiscoveredInstance)
                self.assertTrue(inst.name)
                self.assertTrue(inst.launcher_kind)
                self.assertIn(inst.isolation_mode, (ISOLATION_ISOLATED, ISOLATION_SHARED))


if __name__ == "__main__":
    unittest.main()
