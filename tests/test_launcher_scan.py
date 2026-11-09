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
    apply_instance_to_preferences,
    auto_configure_initial_asset_stack,
    filter_functional_mods,
    find_latest_vanilla_instance,
    parse_minecraft_version,
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

    def test_parse_minecraft_version(self):
        self.assertEqual(parse_minecraft_version("1.21.4"), (1, 21, 4))
        self.assertEqual(parse_minecraft_version("1.21"), (1, 21, 0))
        self.assertEqual(parse_minecraft_version("1.7.10"), (1, 7, 10))
        self.assertEqual(parse_minecraft_version("24w45a"), (1, 24, 45))
        self.assertEqual(parse_minecraft_version("unknown_custom"), (0, 0, 0))

        # Comparison order
        versions = ["1.12.2", "1.21.4", "1.7.10", "1.20.4", "1.21.1", "1.21"]
        sorted_ver = sorted(versions, key=parse_minecraft_version)
        self.assertEqual(sorted_ver, ["1.7.10", "1.12.2", "1.20.4", "1.21", "1.21.1", "1.21.4"])

    def test_find_latest_vanilla_instance(self):
        dummy_jar = self.root / "client.jar"
        dummy_jar.write_bytes(b"dummy")

        i1 = DiscoveredInstance("Old", LAUNCHER_OFFICIAL, ISOLATION_SHARED, self.root, "1.12.2", client_jar=dummy_jar)
        i2 = DiscoveredInstance("Newer", LAUNCHER_PRISM, ISOLATION_ISOLATED, self.root, "1.20.1", client_jar=dummy_jar)
        i3 = DiscoveredInstance("LatestNoJar", LAUNCHER_MODRINTH, ISOLATION_ISOLATED, self.root, "1.21.4", client_jar=None)
        i4 = DiscoveredInstance("LatestValid", LAUNCHER_OFFICIAL, ISOLATION_SHARED, self.root, "1.21.1", client_jar=dummy_jar)

        # Must ignore i3 because it lacks client_jar, and pick i4 (1.21.1) over i2 (1.20.1) and i1 (1.12.2)
        best = find_latest_vanilla_instance([i1, i2, i3, i4])
        self.assertIsNotNone(best)
        self.assertEqual(best.name, "LatestValid")
        self.assertEqual(best.minecraft_version, "1.21.1")

    def test_auto_configure_initial_asset_stack(self):
        from unittest.mock import patch
        from MoziToolKit.utils.system import get_prefs

        dummy_jar = self.root / "client_latest.jar"
        dummy_jar.write_bytes(b"dummy")

        candidate = DiscoveredInstance(
            "AutoDetected",
            LAUNCHER_PRISM,
            ISOLATION_ISOLATED,
            self.root,
            "1.21.4",
            client_jar=dummy_jar,
        )

        prefs = get_prefs()
        if prefs is None:
            self.skipTest("AddonPreferences not available")

        # Backup existing prefs
        orig_packs = [(p.name, p.path, p.pack_type, p.pack_type_source, p.detected_kind, p.enabled) for p in prefs.resource_packs]
        orig_done = prefs.initial_auto_setup_done
        from MoziToolKit.ui.preferences_packs import _CACHED_DISCOVERED_INSTANCES
        orig_cached = list(_CACHED_DISCOVERED_INSTANCES)
        _CACHED_DISCOVERED_INSTANCES.clear()
        try:
            prefs.resource_packs.clear()
            prefs.initial_auto_setup_done = False
            with patch("MoziToolKit.utils.launcher_scan.scan_all_instances", return_value=[candidate]):
                best = auto_configure_initial_asset_stack(prefs)
                self.assertEqual(best, candidate)
                self.assertTrue(prefs.initial_auto_setup_done)
                self.assertEqual(len(prefs.resource_packs), 1)
                self.assertEqual(prefs.resource_packs[0].pack_type, "VANILLA")
                self.assertEqual(prefs.resource_packs[0].path, str(dummy_jar))

                # Second run: must skip because resource_packs already has valid entries
                best2 = auto_configure_initial_asset_stack(prefs)
                self.assertIsNone(best2)

                # Reset resource_packs simulating user deleting config.json:
                # Even if initial_auto_setup_done is True, zero-config state must trigger auto-setup!
                prefs.resource_packs.clear()
                self.assertTrue(prefs.initial_auto_setup_done)
                best3 = auto_configure_initial_asset_stack(prefs)
                self.assertEqual(best3, candidate)
                self.assertEqual(len(prefs.resource_packs), 1)
                self.assertEqual(prefs.resource_packs[0].path, str(dummy_jar))
        finally:
            _CACHED_DISCOVERED_INSTANCES.clear()
            _CACHED_DISCOVERED_INSTANCES.extend(orig_cached)
            # Restore original
            prefs.resource_packs.clear()
            for name, path, p_type, p_source, d_kind, enabled in orig_packs:
                elem = prefs.resource_packs.add()
                elem.name = name
                elem.path = path
                elem.pack_type = p_type
                elem.pack_type_source = p_source
                elem.detected_kind = d_kind
                elem.enabled = enabled
            prefs.initial_auto_setup_done = orig_done

    def test_detected_instance_changed_auto_updates_packs(self):
        from MoziToolKit.utils.system import get_prefs
        from MoziToolKit.ui.preferences_packs import (
            _CACHED_DISCOVERED_INSTANCES,
            wait_for_background_scan,
            on_detected_instance_changed,
        )
        import MoziToolKit.ui.preferences_packs as pref_packs

        wait_for_background_scan()

        jar_a = self.root / "client_1_20.jar"
        jar_a.write_bytes(b"dummy_a")
        jar_b = self.root / "client_1_21.jar"
        jar_b.write_bytes(b"dummy_b")

        pack_a = self.root / "pack_a.zip"
        pack_a.write_bytes(b"dummy_pack")

        inst_a = DiscoveredInstance(
            "Instance A",
            LAUNCHER_PRISM,
            ISOLATION_ISOLATED,
            self.root / "inst_a",
            "1.20.1",
            client_jar=jar_a,
            resource_packs=[pack_a],
        )

        inst_b = DiscoveredInstance(
            "Instance B",
            LAUNCHER_OFFICIAL,
            ISOLATION_SHARED,
            self.root / "inst_b",
            "1.21.1",
            client_jar=jar_b,
        )

        prefs = get_prefs()
        if prefs is None:
            self.skipTest("AddonPreferences not available")

        orig_packs = [(p.name, p.path, p.pack_type, p.pack_type_source, p.detected_kind, p.enabled) for p in prefs.resource_packs]
        orig_cached = list(_CACHED_DISCOVERED_INSTANCES)
        orig_state = pref_packs._INSTANCE_SCAN_STATE

        pref_packs._INSTANCE_SCAN_STATE = "DONE"
        _CACHED_DISCOVERED_INSTANCES.clear()
        _CACHED_DISCOVERED_INSTANCES.extend([inst_a, inst_b])

        try:
            prefs.resource_packs.clear()

            # 1. Select Instance A: should automatically populate with Instance A assets
            prefs.detected_instance_id = inst_a.instance_id
            if len(prefs.resource_packs) == 0:
                on_detected_instance_changed(prefs, None)

            self.assertEqual(len(prefs.resource_packs), 2)
            self.assertTrue(any(p.path == str(jar_a) for p in prefs.resource_packs))
            self.assertTrue(any(p.path == str(pack_a) for p in prefs.resource_packs))

            # 2. Switch to Instance B: should immediately update list to Instance B assets
            prefs.detected_instance_id = inst_b.instance_id
            if not any(p.path == str(jar_b) for p in prefs.resource_packs):
                on_detected_instance_changed(prefs, None)

            self.assertEqual(len(prefs.resource_packs), 1)
            self.assertEqual(prefs.resource_packs[0].path, str(jar_b))
        finally:
            pref_packs._INSTANCE_SCAN_STATE = orig_state
            _CACHED_DISCOVERED_INSTANCES.clear()
            _CACHED_DISCOVERED_INSTANCES.extend(orig_cached)
            pref_packs._set_is_reordering(True)
            try:
                prefs.resource_packs.clear()
                for name, path, p_type, p_source, d_kind, enabled in orig_packs:
                    elem = prefs.resource_packs.add()
                    elem.name = name
                    elem.path = path
                    elem.pack_type = p_type
                    elem.pack_type_source = p_source
                    elem.detected_kind = d_kind
                    elem.enabled = enabled
            finally:
                pref_packs._set_is_reordering(False)

    def test_options_txt_resource_pack_priority_and_enabled_states(self):
        """Tests that options.txt resourcePacks stack determines pack order and enabled status."""
        from MoziToolKit.utils.launcher_scan import (
            parse_options_resource_packs,
            resolve_instance_resource_packs,
        )

        game_dir = self.root / "game_opt_test"
        rp_dir = game_dir / "resourcepacks"
        rp_dir.mkdir(parents=True)

        (rp_dir / "pack_base.zip").write_bytes(b"dummy")
        (rp_dir / "pack_overlay.zip").write_bytes(b"dummy")
        (rp_dir / "pack_top.zip").write_bytes(b"dummy")
        (rp_dir / "pack_unused.zip").write_bytes(b"dummy")
        (rp_dir / "pack_explicit_disabled.zip.disabled").write_bytes(b"dummy")

        # In Minecraft options.txt, pack_top overrides pack_overlay, which overrides pack_base.
        # Order written in options.txt: ["vanilla", "file/pack_base.zip", "file/pack_overlay.zip", "file/pack_top.zip"]
        options_content = (
            'resourcePacks:["vanilla","file/pack_base.zip","file/pack_overlay.zip","file/pack_top.zip"]\n'
        )
        (game_dir / "options.txt").write_text(options_content, encoding="utf-8")

        # 1. Test raw parser
        parsed = parse_options_resource_packs(game_dir / "options.txt")
        self.assertEqual(
            parsed,
            ["vanilla", "file/pack_base.zip", "file/pack_overlay.zip", "file/pack_top.zip"],
        )

        # 2. Test instance pack resolution and priority ordering
        packs = resolve_instance_resource_packs(game_dir)
        self.assertEqual(len(packs), 5)

        # In MoziToolKit (resolution stack): index 0 must be the highest priority (pack_top)
        self.assertEqual(packs[0].name, "pack_top.zip")
        self.assertTrue(packs[0].enabled)
        self.assertEqual(packs[1].name, "pack_overlay.zip")
        self.assertTrue(packs[1].enabled)
        self.assertEqual(packs[2].name, "pack_base.zip")
        self.assertTrue(packs[2].enabled)

        # Unselected packs are appended with enabled=False
        self.assertEqual(packs[3].name, "pack_explicit_disabled.zip.disabled")
        self.assertFalse(packs[3].enabled)
        self.assertEqual(packs[4].name, "pack_unused.zip")
        self.assertFalse(packs[4].enabled)

    def test_version_isolated_inherits_and_merges_root_resource_packs(self):
        """Tests that isolated versions without their own resourcepacks directory inherit root packs."""
        mc_root = self.root / ".minecraft_merge_test"
        (mc_root / "resourcepacks").mkdir(parents=True)
        (mc_root / "resourcepacks" / "global_faithful.zip").write_bytes(b"dummy")

        versions_dir = mc_root / "versions"
        v_iso = versions_dir / "1.21.1-Iso"
        v_iso.mkdir(parents=True)
        (v_iso / ".hmcl").touch()
        (v_iso / "mods").mkdir()
        _make_block_mod_jar(v_iso / "mods" / "iso_mod.jar", "isomod")

        results = scan_official_instances(mc_root)
        self.assertEqual(len(results), 1)
        inst = results[0]
        self.assertEqual(inst.isolation_mode, ISOLATION_ISOLATED)
        # Even though 1.21.1-Iso has no local resourcepacks/, it inherits global_faithful.zip!
        self.assertEqual(len(inst.resource_packs), 1)
        self.assertEqual(inst.resource_packs[0].stem, "global_faithful")

    def test_mod_disabled_states_and_modrinth_profile(self):
        """Tests detection of enabled vs disabled mods via filenames and Modrinth profile.json."""
        modrinth_root = self.root / "modrinth_disabled_test"
        profiles_dir = modrinth_root / "profiles"
        p_dir = profiles_dir / "test_prof"
        p_dir.mkdir(parents=True)

        mods_dir = p_dir / "mods"
        mods_dir.mkdir(parents=True)
        _make_block_mod_jar(mods_dir / "enabled_mod.jar", "enabled_mod")
        _make_block_mod_jar(mods_dir / "file_disabled_mod.jar.disabled", "disabled_mod1")
        _make_block_mod_jar(mods_dir / "profile_disabled_mod.jar", "disabled_mod2")

        p_data = {
            "name": "Modrinth Disable Test",
            "game_version": "1.21",
            "loader": "fabric",
            "files": [
                {"path": "mods/profile_disabled_mod.jar", "disabled": True},
            ],
        }
        (p_dir / "profile.json").write_text(json.dumps(p_data), encoding="utf-8")

        results = scan_modrinth_instances(modrinth_root)
        self.assertEqual(len(results), 1)
        inst = results[0]
        self.assertEqual(len(inst.mods), 3)

        mods_by_name = {m.name: m for m in inst.mods}
        self.assertTrue(mods_by_name["enabled_mod.jar"].enabled)
        self.assertFalse(mods_by_name["file_disabled_mod.jar.disabled"].enabled)
        self.assertFalse(mods_by_name["profile_disabled_mod.jar"].enabled)

    def test_directory_packs_and_nested_folders(self):
        """Tests standard directory packs and single-layer nested folders."""
        game_dir = self.root / "dir_packs_test"
        rp_dir = game_dir / "resourcepacks"
        rp_dir.mkdir(parents=True)

        # 1. Standard directory pack
        p1 = rp_dir / "StandardPack"
        p1.mkdir()
        (p1 / "pack.mcmeta").write_text('{"pack": {"pack_format": 15, "description": "Test"}}', encoding="utf-8")

        # 2. Single-layer nested pack directory (Faithful/Faithful/pack.mcmeta)
        p2_outer = rp_dir / "NestedOuter"
        p2_inner = p2_outer / "NestedInner"
        p2_inner.mkdir(parents=True)
        (p2_inner / "pack.mcmeta").write_text('{"pack": {"pack_format": 15, "description": "Nested"}}', encoding="utf-8")

        # 3. Assets-only directory pack
        p3 = rp_dir / "AssetsOnlyPack"
        (p3 / "assets" / "minecraft" / "textures").mkdir(parents=True)

        inst_list = scan_custom_directory(game_dir)
        self.assertEqual(len(inst_list), 1)
        inst = inst_list[0]
        self.assertEqual(len(inst.resource_packs), 3)

        pack_names = {p.stem for p in inst.resource_packs}
        self.assertIn("StandardPack", pack_names)
        self.assertIn("NestedInner", pack_names)
        self.assertIn("AssetsOnlyPack", pack_names)

    def test_apply_instance_to_preferences_full_tier_stack_integration(self):
        """Tests that applying an instance to preferences populates enabled/disabled states and tier priority."""
        from MoziToolKit.utils.system import get_prefs
        from MoziToolKit.utils.launcher_scan import DiscoveredResourcePack, DiscoveredMod

        prefs = get_prefs()
        if prefs is None:
            self.skipTest("AddonPreferences not available")

        client_jar = self.root / "mc_client.jar"
        client_jar.write_bytes(b"dummy_client")

        rp_top = DiscoveredResourcePack(path=self.root / "top_pack.zip", name="Top Pack", enabled=True, priority=0)
        rp_off = DiscoveredResourcePack(path=self.root / "off_pack.zip", name="Off Pack", enabled=False, priority=1)

        mod_on_path = self.root / "mod_on.jar"
        mod_off_path = self.root / "mod_off.jar.disabled"
        _make_block_mod_jar(mod_on_path, "mod_on")
        _make_block_mod_jar(mod_off_path, "mod_off")

        mod_on = DiscoveredMod(path=mod_on_path, name="Mod On", enabled=True)
        mod_off = DiscoveredMod(path=mod_off_path, name="Mod Off", enabled=False)

        inst = DiscoveredInstance(
            name="IntegrationTestInstance",
            launcher_kind=LAUNCHER_CUSTOM,
            isolation_mode=ISOLATION_ISOLATED,
            game_dir=self.root,
            minecraft_version="1.21.1",
            client_jar=client_jar,
            resource_packs=[rp_top, rp_off],
            mods=[mod_on, mod_off],
        )

        orig_packs = [(p.name, p.path, p.pack_type, p.pack_type_source, p.detected_kind, p.enabled) for p in prefs.resource_packs]
        try:
            apply_instance_to_preferences(inst, prefs)

            self.assertEqual(len(prefs.resource_packs), 5)

            p0 = prefs.resource_packs[0]
            self.assertEqual(p0.pack_type, "RESOURCE_PACK")
            self.assertEqual(p0.name, "top_pack")
            self.assertTrue(p0.enabled)

            p1 = prefs.resource_packs[1]
            self.assertEqual(p1.pack_type, "RESOURCE_PACK")
            self.assertEqual(p1.name, "off_pack")
            self.assertFalse(p1.enabled)

            p2 = prefs.resource_packs[2]
            self.assertEqual(p2.pack_type, "MOD_JAR")
            self.assertEqual(p2.name, "mod_on")
            self.assertTrue(p2.enabled)

            p3 = prefs.resource_packs[3]
            self.assertEqual(p3.pack_type, "MOD_JAR")
            self.assertEqual(p3.name, "mod_off")
            self.assertFalse(p3.enabled)

            p4 = prefs.resource_packs[4]
            self.assertEqual(p4.pack_type, "VANILLA")
            self.assertTrue(p4.enabled)
        finally:
            prefs.resource_packs.clear()
            for name, path, p_type, p_source, d_kind, enabled in orig_packs:
                elem = prefs.resource_packs.add()
                elem.name = name
                elem.path = path
                elem.pack_type = p_type
                elem.pack_type_source = p_source
                elem.detected_kind = d_kind
                elem.enabled = enabled


if __name__ == "__main__":
    unittest.main()
