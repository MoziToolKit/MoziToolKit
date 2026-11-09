"""
Filesystem search root detection, resource pack resolution, and mod classification.
"""

from __future__ import annotations

import csv
import json
import logging
import os
import platform
import urllib.parse
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from .models import (
    ISOLATION_ISOLATED,
    ISOLATION_SHARED,
    LAUNCHER_CURSEFORGE,
    LAUNCHER_MODRINTH,
    LAUNCHER_MULTIMC,
    LAUNCHER_OFFICIAL,
    LAUNCHER_PRISM,
    RESOURCE_PACK_DIR_NAMES,
    DiscoveredMod,
    DiscoveredResourcePack,
)
from ..pack_detect import (
    KIND_VANILLA_CLIENT,
    detect_pack_kind,
    peek_cached_probe,
)

logger = logging.getLogger("MoziToolKit.Utils.Launcher.Detector")


def get_default_search_roots() -> Dict[str, List[Path]]:
    """Returns candidate root directories for supported launchers on the current OS."""
    system = platform.system()
    home = Path.home()
    roots: Dict[str, List[Path]] = {
        LAUNCHER_OFFICIAL: [],
        LAUNCHER_PRISM: [],
        LAUNCHER_MULTIMC: [],
        LAUNCHER_MODRINTH: [],
        LAUNCHER_CURSEFORGE: [],
    }

    if system == "Darwin":
        app_support = home / "Library" / "Application Support"
        roots[LAUNCHER_OFFICIAL].extend([
            app_support / "minecraft",
            home / ".minecraft",
        ])
        roots[LAUNCHER_PRISM].append(app_support / "PrismLauncher")
        roots[LAUNCHER_MULTIMC].append(app_support / "MultiMC")
        roots[LAUNCHER_MODRINTH].append(app_support / "com.modrinth.theseus")
        roots[LAUNCHER_CURSEFORGE].append(home / "Documents" / "curseforge" / "minecraft" / "Instances")
    elif system == "Windows":
        appdata = Path(os.environ.get("APPDATA", home / "AppData" / "Roaming"))
        userprofile = Path(os.environ.get("USERPROFILE", home))
        roots[LAUNCHER_OFFICIAL].append(appdata / ".minecraft")
        roots[LAUNCHER_PRISM].append(appdata / "PrismLauncher")
        roots[LAUNCHER_MULTIMC].append(appdata / "MultiMC")
        roots[LAUNCHER_MODRINTH].append(appdata / "com.modrinth.theseus")
        roots[LAUNCHER_CURSEFORGE].append(userprofile / "curseforge" / "minecraft" / "Instances")
    else:  # Linux / BSD
        roots[LAUNCHER_OFFICIAL].append(home / ".minecraft")
        roots[LAUNCHER_PRISM].extend([
            home / ".local" / "share" / "PrismLauncher",
            home / ".var" / "app" / "org.prismlauncher.PrismLauncher" / "data" / "PrismLauncher",
        ])
        roots[LAUNCHER_MULTIMC].append(home / ".local" / "share" / "multimc")
        roots[LAUNCHER_MODRINTH].append(home / ".local" / "share" / "com.modrinth.theseus")
        roots[LAUNCHER_CURSEFORGE].append(home / "curseforge" / "minecraft" / "Instances")

    return roots


def parse_options_resource_packs(options_path: Optional[Path]) -> List[str]:
    """
    Parses resource pack entries from a Minecraft options.txt file.
    Returns the list of raw pack identifiers in their written order
    (in Minecraft options.txt, later items override earlier items).
    """
    if options_path is None or not options_path.is_file():
        return []

    try:
        content = options_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []

    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("resourcePacks:"):
            raw_val = line[len("resourcePacks:"):].strip()
            # Try JSON array parsing
            try:
                data = json.loads(raw_val)
                if isinstance(data, list):
                    return [str(item) for item in data]
            except Exception:
                pass

            # Fallback parsing for bracketed comma-separated items
            if raw_val.startswith("[") and raw_val.endswith("]"):
                inner = raw_val[1:-1].strip()
                if not inner:
                    return []
                try:
                    reader = csv.reader([inner], quotechar='"', skipinitialspace=True)
                    return [item.strip().strip("'\"") for item in next(reader) if item.strip()]
                except Exception:
                    return [item.strip().strip("'\"") for item in inner.split(",") if item.strip()]

    return []


def _collect_candidate_pack_dirs(game_dir: Path, fallback_roots: Optional[Sequence[Path]] = None) -> List[Path]:
    """Finds all existing resource pack directories for a game directory and optional fallback roots."""
    dirs: List[Path] = []
    seen: Set[Path] = set()

    all_roots: List[Path] = []
    if game_dir and game_dir.is_dir():
        all_roots.append(game_dir)
    if fallback_roots:
        for r in fallback_roots:
            if r and r.is_dir() and r not in all_roots:
                all_roots.append(r)

    for root_candidate in all_roots:
        for dname in RESOURCE_PACK_DIR_NAMES:
            p = root_candidate / dname
            if p.is_dir():
                try:
                    resolved = p.resolve()
                except OSError:
                    resolved = p
                if resolved not in seen:
                    seen.add(resolved)
                    dirs.append(p)

    return dirs


def _scan_single_pack_dir(resourcepacks_dir: Path) -> List[Tuple[Path, bool]]:
    """
    Finds valid resource packs (.zip, .zip.disabled, or directory with pack.mcmeta/assets).
    Returns a list of tuples: (pack_path, is_initially_enabled)
    """
    found: List[Tuple[Path, bool]] = []
    if not resourcepacks_dir.is_dir():
        return found

    try:
        entries = sorted(resourcepacks_dir.iterdir(), key=lambda p: p.name.lower())
    except OSError as e:
        logger.debug("Failed scanning resource packs in %s: %s", resourcepacks_dir, e)
        return found

    for entry in entries:
        if entry.name.startswith("."):
            continue

        lower_name = entry.name.lower()
        is_disabled = (
            lower_name.endswith(".disabled")
            or lower_name.endswith(".disable")
            or lower_name.endswith(".bak")
            or lower_name.endswith(".off")
        )

        # 1. Zip archives
        if entry.is_file():
            if lower_name.endswith(".zip"):
                found.append((entry, not is_disabled))
            elif is_disabled and (".zip" in lower_name or entry.suffix.lower() == ".disabled"):
                # E.g. Faithful.zip.disabled
                found.append((entry, False))
        # 2. Directory packs
        elif entry.is_dir():
            # Standard pack with pack.mcmeta or assets directory
            if (entry / "pack.mcmeta").is_file() or (entry / "assets").is_dir():
                found.append((entry, not is_disabled))
            else:
                # Check for single-layer nested pack directory (e.g. Faithful/Faithful/pack.mcmeta)
                try:
                    subdirs = [s for s in entry.iterdir() if s.is_dir() and not s.name.startswith(".")]
                    nested_target = None
                    if len(subdirs) == 1:
                        sub = subdirs[0]
                        if (sub / "pack.mcmeta").is_file() or (sub / "assets").is_dir():
                            nested_target = sub
                    if nested_target is not None:
                        found.append((nested_target, not is_disabled))
                except OSError:
                    pass

    return found


def resolve_instance_resource_packs(
    game_dir: Path,
    fallback_roots: Optional[Sequence[Path]] = None,
) -> List[DiscoveredResourcePack]:
    """
    Discovers all resource packs for a game instance across local and shared directories,
    inspects options.txt for pack activation and stack priority, and returns an ordered list
    of DiscoveredResourcePack instances.

    Priority Ordering:
    - Active packs are ordered in reverse order of options.txt resourcePacks
      (in Minecraft, later items in the list override earlier ones; thus highest priority is at index 0).
    - Inactive / unselected packs follow active packs.
    """
    # 1. Collect all candidate directories
    pack_dirs = _collect_candidate_pack_dirs(game_dir, fallback_roots)
    if not pack_dirs:
        return []

    # 2. Scan packs from all directories, deduplicating by normalized name
    discovered_raw: List[Tuple[Path, bool]] = []
    seen_names: Set[str] = set()

    for p_dir in pack_dirs:
        for p_path, initial_enabled in _scan_single_pack_dir(p_dir):
            key = p_path.name.lower()
            if key not in seen_names:
                seen_names.add(key)
                discovered_raw.append((p_path, initial_enabled))

    if not discovered_raw:
        return []

    # 3. Locate options.txt
    options_file: Optional[Path] = None
    all_roots = [game_dir] + list(fallback_roots or [])
    for cand_root in all_roots:
        if cand_root and cand_root.is_dir():
            opt = cand_root / "options.txt"
            if opt.is_file():
                options_file = opt
                break

    options_packs: List[str] = parse_options_resource_packs(options_file) if options_file else []

    # 4. If options.txt has resourcePacks configured, match them
    if options_packs:
        enabled_matches: List[Tuple[int, DiscoveredResourcePack]] = []  # (options_index, pack)
        unmatched_packs: List[DiscoveredResourcePack] = []

        def _get_options_index(pack_path: Path) -> Optional[int]:
            stem = pack_path.stem
            name = pack_path.name
            clean_name = name[:-9] if name.lower().endswith(".disabled") else name

            for idx, opt_entry in enumerate(options_packs):
                clean_opt = opt_entry
                if clean_opt.startswith("file/"):
                    clean_opt = clean_opt[5:]
                unquoted_plus = urllib.parse.unquote_plus(clean_opt)
                unquoted = urllib.parse.unquote(clean_opt)

                candidates = {clean_opt.lower(), unquoted_plus.lower(), unquoted.lower()}
                if clean_name.lower() in candidates or stem.lower() in candidates:
                    return idx
            return None

        for p_path, _file_enabled in discovered_raw:
            opt_idx = _get_options_index(p_path)
            # If pack file explicitly ends with .disabled, respect it as disabled
            if p_path.name.lower().endswith(".disabled"):
                opt_idx = None

            if opt_idx is not None:
                pack_obj = DiscoveredResourcePack(
                    path=p_path,
                    name=p_path.name,
                    enabled=True,
                    priority=opt_idx,
                )
                enabled_matches.append((opt_idx, pack_obj))
            else:
                pack_obj = DiscoveredResourcePack(
                    path=p_path,
                    name=p_path.name,
                    enabled=False,  # Not selected in options.txt
                    priority=9999,
                )
                unmatched_packs.append(pack_obj)

        # In Minecraft, later items in options.txt override earlier items.
        # Top of the stack (highest priority, index 0 in MoziToolKit) corresponds to the highest options index!
        enabled_matches.sort(key=lambda item: item[0], reverse=True)
        unmatched_packs.sort(key=lambda p: p.name.lower())

        return [item[1] for item in enabled_matches] + unmatched_packs

    # 5. If no options.txt found, keep initial enabled status (disabled if .disabled), sorted by name
    enabled_packs = [
        DiscoveredResourcePack(path=p_path, name=p_path.name, enabled=True, priority=idx)
        for idx, (p_path, is_en) in enumerate(discovered_raw)
        if is_en
    ]
    disabled_packs = [
        DiscoveredResourcePack(path=p_path, name=p_path.name, enabled=False, priority=9999)
        for (p_path, is_en) in discovered_raw
        if not is_en
    ]
    enabled_packs.sort(key=lambda p: p.name.lower())
    disabled_packs.sort(key=lambda p: p.name.lower())
    return enabled_packs + disabled_packs


def _scan_directory_packs(resourcepacks_dir: Path) -> List[DiscoveredResourcePack]:
    """Compatibility wrapper around resolve_instance_resource_packs for a single folder."""
    if not resourcepacks_dir.is_dir():
        return []
    return resolve_instance_resource_packs(
        resourcepacks_dir.parent if resourcepacks_dir.name in RESOURCE_PACK_DIR_NAMES else resourcepacks_dir
    )


def _scan_directory_mods(
    mods_dir: Path,
    *,
    filter_functional: bool = False,
    disabled_mod_names: Optional[Set[str]] = None,
) -> Tuple[List[DiscoveredMod], List[DiscoveredMod]]:
    """
    Finds mod files (.jar, .jar.disabled) in a mods directory.
    Identifies enabled vs disabled states from file extensions and launcher configurations.
    When filter_functional=True, separates mods with renderable block assets
    from pure functional mods (e.g. Sodium, Iris, Balm, Cloth Config).
    Returns: (asset_mods, functional_mods)
    """
    discovered_mods: List[DiscoveredMod] = []
    if not mods_dir.is_dir():
        return [], []

    disabled_set = {n.lower() for n in (disabled_mod_names or set())}

    try:
        entries = sorted(mods_dir.iterdir(), key=lambda p: p.name.lower())
    except OSError as e:
        logger.debug("Failed scanning mods in %s: %s", mods_dir, e)
        return [], []

    for entry in entries:
        if not entry.is_file() or entry.name.startswith("."):
            continue

        lower = entry.name.lower()
        is_jar = lower.endswith(".jar")
        is_disabled_ext = (
            lower.endswith(".disabled")
            or lower.endswith(".disable")
            or lower.endswith(".bak")
            or lower.endswith(".off")
        )

        if not is_jar and not (is_disabled_ext and ".jar" in lower):
            continue

        is_enabled = is_jar and not is_disabled_ext and lower not in disabled_set and entry.stem.lower() not in disabled_set

        discovered_mods.append(
            DiscoveredMod(
                path=entry,
                name=entry.name,
                enabled=is_enabled,
            )
        )

    # Sort: enabled mods first, then disabled mods
    discovered_mods.sort(key=lambda m: (0 if m.enabled else 1, m.name.lower()))

    if not filter_functional:
        return discovered_mods, []

    return filter_functional_mods(discovered_mods)


def filter_functional_mods(
    mod_paths: Sequence[Any],
) -> Tuple[List[Any], List[Any]]:
    """
    Separates mods containing blockstates, models, or textures from purely functional mods
    (e.g. Sodium, Iris, Cloth Config). Evaluated on-demand when ingesting a specific instance.
    Returns: (asset_mods, functional_mods)
    """
    asset_mods: List[Any] = []
    functional_mods: List[Any] = []

    for entry in mod_paths:
        p = entry.path if hasattr(entry, "path") else Path(entry)
        try:
            probe = detect_pack_kind(p)
            # If the mod has blockstates, block models, or block textures, keep it!
            if probe.has_block_assets or probe.has_renderable_assets:
                asset_mods.append(entry)
            else:
                functional_mods.append(entry)
        except Exception:
            # Fallback to keeping it if detection failed
            asset_mods.append(entry)

    return asset_mods, functional_mods


def _verify_client_jar(jar_path: Path) -> bool:
    """Verifies whether a JAR file is a valid vanilla or client JAR."""
    if not jar_path.is_file():
        return False
    probe = peek_cached_probe(jar_path)
    if probe is None:
        try:
            probe = detect_pack_kind(jar_path)
        except Exception:
            return False
    return probe.kind == KIND_VANILLA_CLIENT
