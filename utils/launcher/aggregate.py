"""
Custom game directory inspection and multi-launcher aggregated discovery.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set

from .detector import (
    _scan_directory_mods,
    get_default_search_roots,
    resolve_instance_resource_packs,
)
from .models import (
    ISOLATION_ISOLATED,
    LAUNCHER_CURSEFORGE,
    LAUNCHER_CUSTOM,
    LAUNCHER_MODRINTH,
    LAUNCHER_MULTIMC,
    LAUNCHER_OFFICIAL,
    LAUNCHER_PRISM,
    LOADER_UNKNOWN,
    DiscoveredInstance,
)
from .scanners import (
    _scan_single_prism_instance,
    scan_curseforge_instances,
    scan_modrinth_instances,
    scan_official_instances,
    scan_prism_instances,
)

logger = logging.getLogger("MoziToolKit.Utils.Launcher.Aggregate")


def _get_launcher_delegate(name: str, default: Any) -> Any:
    for mod_name in (
        "bl_ext.vscode_development.MoziToolKit.utils.launcher_scan",
        "MoziToolKit.utils.launcher_scan",
    ):
        mod = sys.modules.get(mod_name)
        if mod is not None:
            val = getattr(mod, name, None)
            if val is not None:
                return val
    return default


def scan_custom_directory(target_path: Path) -> List[DiscoveredInstance]:
    """
    Intelligently inspects a user-supplied directory and identifies instances.
    Handles Prism instances, Modrinth profiles, CurseForge folders, and .minecraft roots.
    """
    path = target_path.expanduser().resolve()
    if not path.is_dir():
        return []

    # 1. Is it a Prism instance directory?
    if (path / "instance.cfg").is_file() or (path / "mmc-pack.json").is_file():
        parent_root = path.parent.parent if path.parent.name == "instances" else path.parent
        inst = _scan_single_prism_instance(path, launcher_kind=LAUNCHER_CUSTOM, library_search_root=parent_root)
        if inst is not None:
            return [inst]

    # 2. Is it a Modrinth profile directory?
    if (path / "profile.json").is_file():
        results = scan_modrinth_instances(path.parent.parent)
        matching = [inst for inst in results if inst.game_dir == path]
        if matching:
            return matching

    # 3. Is it a CurseForge instance directory?
    if (path / "minecraftinstance.json").is_file():
        results = scan_curseforge_instances(path.parent)
        return [inst for inst in results if inst.game_dir == path]

    # 4. Does it contain versions/ (or .minecraft/versions/)?
    if (path / "versions").is_dir():
        return scan_official_instances(path)
    if (path / ".minecraft" / "versions").is_dir():
        return scan_official_instances(path / ".minecraft")

    # 5. Is it a standalone game folder with mods/ or resourcepacks/?
    packs = resolve_instance_resource_packs(path)
    mods, skipped_mods = _scan_directory_mods(path / "mods")
    if packs or mods or (path / "saves").is_dir():
        # Generic isolated instance
        return [
            DiscoveredInstance(
                name=path.name,
                launcher_kind=LAUNCHER_CUSTOM,
                isolation_mode=ISOLATION_ISOLATED,
                game_dir=path,
                minecraft_version="custom",
                loader=LOADER_UNKNOWN,
                client_jar=None,
                resource_packs=packs,
                mods=mods,
                skipped_functional_mods=skipped_mods,
            )
        ]

    return []


def scan_all_instances(custom_directories: Optional[Sequence[Path]] = None) -> List[DiscoveredInstance]:
    """
    Scans all standard system launcher directories plus any optional custom paths.
    Deduplicates instances and returns a comprehensive sorted list.
    """
    get_roots_fn = _get_launcher_delegate("get_default_search_roots", get_default_search_roots)
    roots = get_roots_fn()
    found: List[DiscoveredInstance] = []

    # Official / .minecraft roots
    for official_root in roots.get(LAUNCHER_OFFICIAL, []):
        if official_root.is_dir():
            found.extend(scan_official_instances(official_root))

    # Prism Launcher
    for prism_root in roots.get(LAUNCHER_PRISM, []):
        if prism_root.is_dir():
            found.extend(scan_prism_instances(prism_root, launcher_kind=LAUNCHER_PRISM))

    # MultiMC
    for mmc_root in roots.get(LAUNCHER_MULTIMC, []):
        if mmc_root.is_dir():
            found.extend(scan_prism_instances(mmc_root, launcher_kind=LAUNCHER_MULTIMC))

    # Modrinth App
    for modrinth_root in roots.get(LAUNCHER_MODRINTH, []):
        if modrinth_root.is_dir():
            found.extend(scan_modrinth_instances(modrinth_root))

    # CurseForge
    for cf_root in roots.get(LAUNCHER_CURSEFORGE, []):
        if cf_root.is_dir():
            found.extend(scan_curseforge_instances(cf_root))

    # User-supplied custom paths
    if custom_directories:
        for c_path in custom_directories:
            if c_path and c_path.is_dir():
                found.extend(scan_custom_directory(c_path))

    # Cross-reference missing client JARs: if an instance lacks client_jar,
    # match against client_jars discovered in other instances with the same minecraft_version.
    version_jar_map: Dict[str, Path] = {}
    for inst in found:
        if inst.client_jar and inst.client_jar.is_file() and inst.minecraft_version:
            version_jar_map.setdefault(inst.minecraft_version, inst.client_jar)

    for inst in found:
        if inst.client_jar is None and inst.minecraft_version in version_jar_map:
            inst.client_jar = version_jar_map[inst.minecraft_version]

    # Deduplicate by (game_dir, name)
    unique: List[DiscoveredInstance] = []
    seen: Set[str] = set()
    for inst in found:
        key = f"{str(inst.game_dir.resolve())}:{inst.name}"
        if key not in seen:
            seen.add(key)
            unique.append(inst)

    return sorted(unique, key=lambda x: (x.launcher_kind, x.name.lower()))
