"""
Launchers and installation directory scanners (Prism, Official, Modrinth, CurseForge, Custom).
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set

from .detector import (
    _scan_directory_mods,
    _verify_client_jar,
    get_default_search_roots,
    resolve_instance_resource_packs,
)
from .models import (
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
    LOADER_QUILT,
    LOADER_UNKNOWN,
    LOADER_VANILLA,
    DiscoveredInstance,
)

logger = logging.getLogger("MoziToolKit.Utils.Launcher.Scanners")


# ---------------------------------------------------------------------------
# Prism Launcher / MultiMC Scanner
# ---------------------------------------------------------------------------


def _scan_single_prism_instance(
    inst_dir: Path,
    launcher_kind: str = LAUNCHER_PRISM,
    client_cache: Optional[Dict[str, Path]] = None,
    library_search_root: Optional[Path] = None,
) -> Optional[DiscoveredInstance]:
    """Scans a single directory as a Prism / MultiMC instance."""
    cfg_file = inst_dir / "instance.cfg"
    pack_file = inst_dir / "mmc-pack.json"
    if not cfg_file.is_file() and not pack_file.is_file():
        return None

    # Parse instance.cfg properties
    cfg_props: Dict[str, str] = {}
    if cfg_file.is_file():
        try:
            for line in cfg_file.read_text(encoding="utf-8", errors="replace").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    cfg_props[k.strip()] = v.strip()
        except OSError:
            pass

    inst_name = cfg_props.get("name", inst_dir.name)
    icon_key = cfg_props.get("iconKey")

    # Parse mmc-pack.json components
    mc_version = cfg_props.get("IntendedVersion", "")
    loader = LOADER_VANILLA

    if pack_file.is_file():
        try:
            data = json.loads(pack_file.read_text(encoding="utf-8", errors="replace"))
            components = data.get("components", [])
            for comp in components:
                uid = comp.get("uid", "")
                cached_ver = comp.get("cachedVersion") or comp.get("version") or ""
                if uid == "net.minecraft":
                    if cached_ver:
                        mc_version = cached_ver
                elif "fabric" in uid:
                    loader = LOADER_FABRIC
                elif "neoforge" in uid:
                    loader = LOADER_NEOFORGE
                elif "forge" in uid:
                    loader = LOADER_FORGE
                elif "quilt" in uid:
                    loader = LOADER_QUILT
        except (json.JSONDecodeError, OSError):
            pass

    if not mc_version:
        mc_version = inst_dir.name

    # Game directory inside Prism instance
    game_dir = inst_dir / "minecraft"
    if not game_dir.is_dir():
        alt_game_dir = inst_dir / ".minecraft"
        game_dir = alt_game_dir if alt_game_dir.is_dir() else inst_dir

    # Resource packs and mods
    packs = resolve_instance_resource_packs(game_dir)
    mods, skipped_mods = _scan_directory_mods(game_dir / "mods")

    # Resolve client JAR
    client_jar = None
    if client_cache:
        client_jar = client_cache.get(mc_version)
    if client_jar is None and library_search_root:
        candidate = (
            library_search_root
            / "libraries"
            / "com"
            / "mojang"
            / "minecraft"
            / mc_version
            / f"minecraft-{mc_version}-client.jar"
        )
        if candidate.is_file():
            client_jar = candidate

    return DiscoveredInstance(
        name=inst_name,
        launcher_kind=launcher_kind,
        isolation_mode=ISOLATION_ISOLATED,
        game_dir=game_dir,
        minecraft_version=mc_version,
        loader=loader,
        client_jar=client_jar,
        resource_packs=packs,
        mods=mods,
        skipped_functional_mods=skipped_mods,
        icon_key=icon_key,
        extra={"instance_root": str(inst_dir)},
    )


def scan_prism_instances(root: Path, launcher_kind: str = LAUNCHER_PRISM) -> List[DiscoveredInstance]:
    """Scans a Prism Launcher or MultiMC root directory for instances."""
    instances_dir = root / "instances"
    if not instances_dir.is_dir():
        return []

    # Map of cached vanilla client JARs: version -> jar_path
    client_cache: Dict[str, Path] = {}
    mojang_lib_dir = root / "libraries" / "com" / "mojang" / "minecraft"
    if mojang_lib_dir.is_dir():
        try:
            for ver_dir in mojang_lib_dir.iterdir():
                if ver_dir.is_dir():
                    c_jar = ver_dir / f"minecraft-{ver_dir.name}-client.jar"
                    if c_jar.is_file():
                        client_cache[ver_dir.name] = c_jar
        except OSError:
            pass

    results: List[DiscoveredInstance] = []

    for inst_dir in sorted(instances_dir.iterdir()):
        if not inst_dir.is_dir() or inst_dir.name.startswith("."):
            continue

        inst = _scan_single_prism_instance(
            inst_dir,
            launcher_kind=launcher_kind,
            client_cache=client_cache,
            library_search_root=root,
        )
        if inst is not None:
            results.append(inst)

    return results


# ---------------------------------------------------------------------------
# Official Launcher Scanner (and HMCL / PCL Shared & Isolated roots)
# ---------------------------------------------------------------------------


def scan_official_instances(root: Path) -> List[DiscoveredInstance]:
    """Scans the standard .minecraft directory for versions and profiles."""
    if not root.is_dir():
        return []

    versions_dir = root / "versions"
    profiles_file = root / "launcher_profiles.json"

    # Known profiles: version_id -> profile metadata
    profiles_by_version: Dict[str, Dict[str, Any]] = {}
    if profiles_file.is_file():
        try:
            data = json.loads(profiles_file.read_text(encoding="utf-8", errors="replace"))
            profiles = data.get("profiles", {})
            for p_id, p_info in profiles.items():
                last_ver = p_info.get("lastVersionId")
                if last_ver:
                    profiles_by_version[last_ver] = p_info
        except (json.JSONDecodeError, OSError):
            pass

    results: List[DiscoveredInstance] = []
    if not versions_dir.is_dir():
        return results

    # Global shared packs and mods
    global_packs = resolve_instance_resource_packs(root)
    global_mods, global_skipped_mods = _scan_directory_mods(root / "mods")

    for ver_dir in sorted(versions_dir.iterdir()):
        if not ver_dir.is_dir() or ver_dir.name.startswith("."):
            continue

        ver_id = ver_dir.name
        ver_json_file = ver_dir / f"{ver_id}.json"
        loader = LOADER_VANILLA
        mc_version = ver_id
        inherits_from: Optional[str] = None

        if ver_json_file.is_file():
            try:
                v_data = json.loads(ver_json_file.read_text(encoding="utf-8", errors="replace"))
                inherits_from = v_data.get("inheritsFrom")
                main_class = str(v_data.get("mainClass", "")).lower()
                if "fabric" in main_class:
                    loader = LOADER_FABRIC
                elif "neoforge" in main_class or "cpw.mods.bootstraplauncher" in main_class:
                    loader = LOADER_NEOFORGE
                elif "forge" in main_class:
                    loader = LOADER_FORGE
                elif "quilt" in main_class:
                    loader = LOADER_QUILT
            except (json.JSONDecodeError, OSError):
                pass

        # If inherits_from is present (e.g. 26.2-Fabric inherits from 26.2)
        if inherits_from:
            mc_version = inherits_from

        # Check for Version Isolation (HMCL, PCL2, or custom launcher)
        is_isolated = False
        launcher_kind = LAUNCHER_OFFICIAL
        game_dir = root

        # Check if version folder contains dedicated mods/resourcepacks/.hmcl
        has_isolated_mods = (ver_dir / "mods").is_dir()
        has_isolated_packs = (ver_dir / "resourcepacks").is_dir()
        has_hmcl_marker = (ver_dir / ".hmcl").exists()

        if has_hmcl_marker or has_isolated_mods or has_isolated_packs:
            is_isolated = True
            game_dir = ver_dir
            if has_hmcl_marker:
                launcher_kind = LAUNCHER_HMCL

        # Check profile custom gameDir
        profile_info = profiles_by_version.get(ver_id)
        inst_name = ver_id
        icon_key = None
        if profile_info:
            inst_name = profile_info.get("name", ver_id)
            icon_key = profile_info.get("icon")
            custom_dir = profile_info.get("gameDir")
            if custom_dir:
                custom_path = Path(custom_dir).expanduser()
                if custom_path.is_dir():
                    game_dir = custom_path
                    is_isolated = True

        # Scan resources for this instance
        if is_isolated:
            packs = resolve_instance_resource_packs(game_dir, fallback_roots=[root])
            mods, skipped_mods = _scan_directory_mods(game_dir / "mods")
            # If isolated version has no mods directory at all, but root does, fallback to root mods
            if not mods and not (game_dir / "mods").is_dir() and (root / "mods").is_dir():
                mods, skipped_mods = _scan_directory_mods(root / "mods")
            isolation_mode = ISOLATION_ISOLATED
        else:
            packs = list(global_packs)
            mods = list(global_mods)
            skipped_mods = list(global_skipped_mods)
            isolation_mode = ISOLATION_SHARED

        # Resolve client JAR
        client_jar: Optional[Path] = None
        direct_jar = ver_dir / f"{ver_id}.jar"
        if direct_jar.is_file() and _verify_client_jar(direct_jar):
            client_jar = direct_jar
        elif inherits_from:
            parent_jar = versions_dir / inherits_from / f"{inherits_from}.jar"
            if parent_jar.is_file() and _verify_client_jar(parent_jar):
                client_jar = parent_jar

        results.append(
            DiscoveredInstance(
                name=inst_name,
                launcher_kind=launcher_kind,
                isolation_mode=isolation_mode,
                game_dir=game_dir,
                minecraft_version=mc_version,
                loader=loader,
                client_jar=client_jar,
                resource_packs=packs,
                mods=mods,
                skipped_functional_mods=skipped_mods,
                icon_key=icon_key,
                extra={"version_id": ver_id, "versions_dir": str(versions_dir)},
            )
        )

    return results


# ---------------------------------------------------------------------------
# Modrinth App Scanner
# ---------------------------------------------------------------------------


def scan_modrinth_instances(root: Path) -> List[DiscoveredInstance]:
    """Scans Modrinth App profiles."""
    profiles_dir = root / "profiles"
    if not profiles_dir.is_dir():
        return []

    results: List[DiscoveredInstance] = []
    for p_dir in sorted(profiles_dir.iterdir()):
        if not p_dir.is_dir() or p_dir.name.startswith("."):
            continue

        p_file = p_dir / "profile.json"
        if not p_file.is_file():
            continue

        try:
            data = json.loads(p_file.read_text(encoding="utf-8", errors="replace"))
        except (json.JSONDecodeError, OSError):
            continue

        name = data.get("name", p_dir.name)
        mc_ver = data.get("game_version", "")
        raw_loader = str(data.get("loader", LOADER_VANILLA)).lower()
        icon = data.get("icon")

        loader = LOADER_VANILLA
        if "fabric" in raw_loader:
            loader = LOADER_FABRIC
        elif "neoforge" in raw_loader:
            loader = LOADER_NEOFORGE
        elif "forge" in raw_loader:
            loader = LOADER_FORGE
        elif "quilt" in raw_loader:
            loader = LOADER_QUILT

        # Check for disabled mods in profile.json
        disabled_mod_names: Set[str] = set()
        files_list = data.get("files", [])
        if isinstance(files_list, list):
            for f_entry in files_list:
                if isinstance(f_entry, dict) and f_entry.get("disabled") is True:
                    f_path = str(f_entry.get("path", ""))
                    if f_path:
                        disabled_mod_names.add(Path(f_path).name.lower())

        packs = resolve_instance_resource_packs(p_dir)
        mods, skipped_mods = _scan_directory_mods(p_dir / "mods", disabled_mod_names=disabled_mod_names)

        # Client jar lookup in modrinth meta
        client_jar: Optional[Path] = None
        candidate = root / "meta" / "versions" / mc_ver / f"{mc_ver}.jar"
        if candidate.is_file() and _verify_client_jar(candidate):
            client_jar = candidate

        results.append(
            DiscoveredInstance(
                name=name,
                launcher_kind=LAUNCHER_MODRINTH,
                isolation_mode=ISOLATION_ISOLATED,
                game_dir=p_dir,
                minecraft_version=mc_ver,
                loader=loader,
                client_jar=client_jar,
                resource_packs=packs,
                mods=mods,
                skipped_functional_mods=skipped_mods,
                icon_key=icon,
                extra={"profile_dir": str(p_dir)},
            )
        )

    return results


# ---------------------------------------------------------------------------
# CurseForge Scanner
# ---------------------------------------------------------------------------


def scan_curseforge_instances(instances_dir: Path) -> List[DiscoveredInstance]:
    """Scans CurseForge instances directory."""
    if not instances_dir.is_dir():
        return []

    results: List[DiscoveredInstance] = []
    for inst_dir in sorted(instances_dir.iterdir()):
        if not inst_dir.is_dir() or inst_dir.name.startswith("."):
            continue

        manifest_file = inst_dir / "minecraftinstance.json"
        if not manifest_file.is_file():
            continue

        try:
            data = json.loads(manifest_file.read_text(encoding="utf-8", errors="replace"))
        except (json.JSONDecodeError, OSError):
            continue

        name = data.get("name", inst_dir.name)
        mc_ver = data.get("gameVersion", "")
        base_loader = data.get("baseModLoader", {})
        raw_loader = str(base_loader.get("name", "")).lower()

        loader = LOADER_VANILLA
        if "fabric" in raw_loader:
            loader = LOADER_FABRIC
        elif "neoforge" in raw_loader:
            loader = LOADER_NEOFORGE
        elif "forge" in raw_loader:
            loader = LOADER_FORGE
        elif "quilt" in raw_loader:
            loader = LOADER_QUILT

        packs = resolve_instance_resource_packs(inst_dir)
        mods, skipped_mods = _scan_directory_mods(inst_dir / "mods")

        results.append(
            DiscoveredInstance(
                name=name,
                launcher_kind=LAUNCHER_CURSEFORGE,
                isolation_mode=ISOLATION_ISOLATED,
                game_dir=inst_dir,
                minecraft_version=mc_ver,
                loader=loader,
                client_jar=None,
                resource_packs=packs,
                mods=mods,
                skipped_functional_mods=skipped_mods,
                extra={"instance_dir": str(inst_dir)},
            )
        )

    return results
