"""
Launcher and Game Instance Discovery Utility.

Discovers locally installed Minecraft game instances across official and third-party
launchers (Official Launcher, Prism Launcher, MultiMC, Modrinth App, CurseForge, HMCL, PCL2),
extracts version metadata, loader type, and resource stacks, and distinguishes between
isolated instance containers and shared installations.
"""

from __future__ import annotations

import json
import logging
import os
import platform
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from .pack_detect import (
    KIND_VANILLA_CLIENT,
    PackProbe,
    detect_pack_kind,
    peek_cached_probe,
)

logger = logging.getLogger("MoziToolKit.Utils.LauncherScan")

# ---------------------------------------------------------------------------
# Launcher & Isolation Constants
# ---------------------------------------------------------------------------

LAUNCHER_OFFICIAL = "OFFICIAL"
LAUNCHER_PRISM = "PRISM"
LAUNCHER_MULTIMC = "MULTIMC"
LAUNCHER_MODRINTH = "MODRINTH"
LAUNCHER_CURSEFORGE = "CURSEFORGE"
LAUNCHER_HMCL = "HMCL"
LAUNCHER_CUSTOM = "CUSTOM"

ISOLATION_ISOLATED = "ISOLATED"
ISOLATION_SHARED = "SHARED"

LOADER_VANILLA = "vanilla"
LOADER_FABRIC = "fabric"
LOADER_NEOFORGE = "neoforge"
LOADER_FORGE = "forge"
LOADER_QUILT = "quilt"
LOADER_UNKNOWN = "unknown"

RESOURCE_PACK_DIR_NAMES = ("resourcepacks", "texturepacks", "ResourcePacks", "resource_packs")


# ---------------------------------------------------------------------------
# Data Containers
# ---------------------------------------------------------------------------


@dataclass
class DiscoveredResourcePack:
    """Represents a discovered resource pack file or directory."""

    path: Path
    name: str = ""
    enabled: bool = True
    priority: int = 0  # Lower index = higher priority in resolution stack

    def __post_init__(self) -> None:
        if not self.name:
            self.name = self.path.name

    @property
    def display_name(self) -> str:
        clean = self.path.name
        if clean.lower().endswith(".disabled"):
            clean = clean[:-9]
        return Path(clean).stem

    @property
    def stem(self) -> str:
        return self.path.stem

    @property
    def suffix(self) -> str:
        return self.path.suffix

    def __fspath__(self) -> str:
        return str(self.path)

    def __str__(self) -> str:
        return str(self.path)

    def exists(self) -> bool:
        return self.path.exists()

    def is_file(self) -> bool:
        return self.path.is_file()

    def is_dir(self) -> bool:
        return self.path.is_dir()


@dataclass
class DiscoveredMod:
    """Represents a discovered mod JAR archive."""

    path: Path
    name: str = ""
    enabled: bool = True

    def __post_init__(self) -> None:
        if not self.name:
            self.name = self.path.name

    @property
    def display_name(self) -> str:
        clean = self.path.name
        if clean.lower().endswith(".disabled"):
            clean = clean[:-9]
        return Path(clean).stem

    @property
    def stem(self) -> str:
        return self.path.stem

    @property
    def suffix(self) -> str:
        return self.path.suffix

    def __fspath__(self) -> str:
        return str(self.path)

    def __str__(self) -> str:
        return str(self.path)

    def exists(self) -> bool:
        return self.path.exists()

    def is_file(self) -> bool:
        return self.path.is_file()

    def is_dir(self) -> bool:
        return self.path.is_dir()


@dataclass
class DiscoveredInstance:
    """Represents a discovered Minecraft game installation or instance."""

    name: str
    launcher_kind: str
    isolation_mode: str
    game_dir: Path
    minecraft_version: str
    loader: str = LOADER_VANILLA
    client_jar: Optional[Path] = None
    resource_packs: List[Any] = field(default_factory=list)
    mods: List[Any] = field(default_factory=list)
    skipped_functional_mods: List[Any] = field(default_factory=list)
    icon_key: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_isolated(self) -> bool:
        return self.isolation_mode == ISOLATION_ISOLATED

    @property
    def has_client_jar(self) -> bool:
        return self.client_jar is not None and self.client_jar.is_file()

    @property
    def enabled_resource_packs(self) -> List[Any]:
        return [p for p in self.resource_packs if getattr(p, "enabled", True)]

    @property
    def enabled_mods(self) -> List[Any]:
        return [m for m in self.mods if getattr(m, "enabled", True)]

    @property
    def instance_id(self) -> str:
        import hashlib
        key = f"{self.launcher_kind}:{str(self.game_dir.resolve())}:{self.name}"
        return hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "instance_id": self.instance_id,
            "name": self.name,
            "launcher_kind": self.launcher_kind,
            "isolation_mode": self.isolation_mode,
            "game_dir": str(self.game_dir),
            "minecraft_version": self.minecraft_version,
            "loader": self.loader,
            "client_jar": str(self.client_jar) if self.client_jar else None,
            "resource_pack_count": len(self.resource_packs),
            "enabled_resource_pack_count": len(self.enabled_resource_packs),
            "mod_count": len(self.mods),
            "enabled_mod_count": len(self.enabled_mods),
            "skipped_functional_mods_count": len(self.skipped_functional_mods),
            "icon_key": self.icon_key,
            "extra": self.extra,
        }


# ---------------------------------------------------------------------------
# Path Helpers
# ---------------------------------------------------------------------------


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
                import csv
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
        import urllib.parse

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


# ---------------------------------------------------------------------------
# Custom Directory & Universal Scanner
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Aggregated Scanner
# ---------------------------------------------------------------------------


def scan_all_instances(custom_directories: Optional[Sequence[Path]] = None) -> List[DiscoveredInstance]:
    """
    Scans all standard system launcher directories plus any optional custom paths.
    Deduplicates instances and returns a comprehensive sorted list.
    """
    roots = get_default_search_roots()
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


# ---------------------------------------------------------------------------
# Out-of-the-Box Auto-Setup & Version Comparison
# ---------------------------------------------------------------------------


def parse_minecraft_version(version_str: str) -> Tuple[int, ...]:
    """
    Parses a Minecraft release or snapshot version into a comparable tuple of integers.
    E.g.: '1.21.4' -> (1, 21, 4), '1.21' -> (1, 21, 0), '1.12.2' -> (1, 12, 2).
    """
    import re

    clean = version_str.strip()
    m = re.match(r"^(\d+)\.(\d+)(?:\.(\d+))?", clean)
    if m:
        major = int(m.group(1))
        minor = int(m.group(2))
        patch = int(m.group(3)) if m.group(3) else 0
        return (major, minor, patch)

    # Snapshot fallback, e.g. '24w45a' -> (1, 24, 45)
    snap_m = re.match(r"^(\d{2})w(\d{2})", clean)
    if snap_m:
        return (1, int(snap_m.group(1)), int(snap_m.group(2)))

    return (0, 0, 0)


def find_latest_vanilla_instance(
    instances: Sequence[DiscoveredInstance],
) -> Optional[DiscoveredInstance]:
    """
    Selects the discovered instance with the highest Minecraft release version
    that actually provides a verified Minecraft Client JAR.
    """
    valid_instances = [
        inst for inst in instances
        if inst.has_client_jar and inst.minecraft_version
    ]
    if not valid_instances:
        return None

    return max(
        valid_instances,
        key=lambda inst: (
            parse_minecraft_version(inst.minecraft_version),
            1 if inst.is_isolated else 0,
        ),
    )


def apply_instance_to_preferences(inst: DiscoveredInstance, prefs: Any) -> Tuple[List[Any], List[Any]]:
    """
    Applies the given game instance assets (Vanilla Client JAR, filtered mods, resource packs)
    to Blender AddonPreferences, sorting them by standard tier hierarchy.
    Returns: (asset_mods, skipped_mods)
    """
    from .pack_detect import (
        KIND_MOD,
        KIND_RESOURCE_PACK,
        KIND_VANILLA_CLIENT,
        TYPE_SOURCE_MANUAL,
    )

    asset_mods, skipped_mods = filter_functional_mods(inst.mods)

    try:
        from ..ui.preferences_packs import _set_is_reordering
        _set_is_reordering(True)
    except Exception:
        pass
    if hasattr(prefs, "_mozi_is_syncing"):
        setattr(prefs, "_mozi_is_syncing", True)

    try:
        coll = prefs.resource_packs
        coll.clear()

        def _add_elem():
            if hasattr(coll, "add"):
                return coll.add()
            import types
            elem = types.SimpleNamespace()
            coll.append(elem)
            return elem

        # 1. Vanilla base JAR
        if inst.client_jar and inst.client_jar.is_file():
            elem = _add_elem()
            elem.name = f"Minecraft {inst.minecraft_version} Vanilla Client"
            elem.pack_type = "VANILLA"
            elem.pack_type_source = TYPE_SOURCE_MANUAL
            elem.detected_kind = KIND_VANILLA_CLIENT
            elem.path = str(inst.client_jar)
            elem.enabled = True

        # 2. Mod JARs (filtered for renderable block assets)
        for mod in asset_mods:
            elem = _add_elem()
            m_path = mod.path if hasattr(mod, "path") else Path(mod)
            m_name = getattr(mod, "display_name", None) or m_path.stem
            m_enabled = getattr(mod, "enabled", True)

            elem.name = m_name
            elem.pack_type = "MOD_JAR"
            elem.pack_type_source = TYPE_SOURCE_MANUAL
            elem.detected_kind = KIND_MOD
            elem.path = str(m_path)
            elem.enabled = m_enabled

        # 3. Resource Packs (ordered by options.txt stack priority: highest priority first)
        for pack in inst.resource_packs:
            elem = _add_elem()
            p_path = pack.path if hasattr(pack, "path") else Path(pack)
            p_name = getattr(pack, "display_name", None) or p_path.stem
            p_enabled = getattr(pack, "enabled", True)

            elem.name = p_name
            elem.pack_type = "RESOURCE_PACK"
            elem.pack_type_source = TYPE_SOURCE_MANUAL
            elem.detected_kind = KIND_RESOURCE_PACK
            elem.path = str(p_path)
            elem.enabled = p_enabled
    finally:
        if hasattr(prefs, "_mozi_is_syncing"):
            setattr(prefs, "_mozi_is_syncing", False)
        try:
            from ..ui.preferences_packs import _set_is_reordering
            _set_is_reordering(False)
        except Exception:
            pass

    try:
        from ..ui.preferences_packs import _CACHED_DISCOVERED_INSTANCES
        if not any(i.instance_id == inst.instance_id for i in _CACHED_DISCOVERED_INSTANCES):
            _CACHED_DISCOVERED_INSTANCES.append(inst)
    except Exception:
        pass

    try:
        from ..ui.preferences_packs import reorder_resource_packs_by_tier
        reorder_resource_packs_by_tier(prefs)
    except Exception:
        pass

    prefs.resource_packs_index = 0
    if hasattr(prefs, "detected_instance_id"):
        try:
            from ..ui.preferences_packs import is_updating_instance, set_is_updating_instance
            was_updating = is_updating_instance()
            set_is_updating_instance(True)
            try:
                prefs.detected_instance_id = inst.instance_id
            finally:
                set_is_updating_instance(was_updating)
        except Exception:
            try:
                prefs.detected_instance_id = inst.instance_id
            except Exception:
                pass

    try:
        from ..ui.preferences_packs import get_config_manager
        get_config_manager().sync_from_preferences(prefs)
    except Exception:
        pass

    return asset_mods, skipped_mods


def auto_configure_initial_asset_stack(prefs=None) -> Optional[DiscoveredInstance]:
    """
    Zero-configuration auto-setup:
    If no resource packs or JARs are configured, scans local launchers, finds the
    highest Minecraft version with a verified Client JAR, and automatically configures it
    as the default base asset stack.
    """
    if prefs is None:
        try:
            from .system import get_prefs
            prefs = get_prefs()
        except Exception:
            return None

    if prefs is None:
        return None

    existing_packs = getattr(prefs, "resource_packs", None) or []
    has_any_existing_file = any(
        bool(getattr(p, "path", "")) and Path(getattr(p, "path", "")).exists()
        for p in existing_packs
    )
    if has_any_existing_file and len(existing_packs) > 0:
        return None

    # Use memory cache if available to avoid duplicate scans
    instances = None
    try:
        from ..ui.preferences_packs import _CACHED_DISCOVERED_INSTANCES
        if _CACHED_DISCOVERED_INSTANCES:
            instances = _CACHED_DISCOVERED_INSTANCES
    except Exception:
        pass

    if instances is None:
        instances = scan_all_instances()

    best = find_latest_vanilla_instance(instances)
    if best is None:
        if hasattr(prefs, "initial_auto_setup_done"):
            try:
                prefs.initial_auto_setup_done = True
            except Exception:
                pass
        return None

    apply_instance_to_preferences(best, prefs)
    if hasattr(prefs, "initial_auto_setup_done"):
        try:
            prefs.initial_auto_setup_done = True
        except Exception:
            pass

    try:
        from ..utils.assets.guard import invalidate_guard_cache
        invalidate_guard_cache()
    except Exception:
        pass

    logger.info(
        "MoziToolKit: Automatically configured Minecraft %s (%s) as default asset stack.",
        best.minecraft_version,
        best.launcher_kind,
    )
    return best


def schedule_background_auto_setup() -> None:
    """
    Schedules an asynchronous, non-blocking check on Blender startup.
    Scans local Minecraft launchers in a background daemon thread and auto-populates
    the default asset stack if the preferences are completely empty.
    """
    try:
        import bpy
    except ImportError:
        return

    import threading

    def _bg_worker():
        try:
            instances = scan_all_instances()
            try:
                from ..ui import preferences_packs
                with preferences_packs._INSTANCE_SCAN_LOCK:
                    preferences_packs._CACHED_DISCOVERED_INSTANCES = instances
                    preferences_packs._INSTANCE_SCAN_STATE = "DONE"
            except Exception:
                pass

            best = find_latest_vanilla_instance(instances)
            if best is None:
                return

            def _apply_on_main():
                try:
                    from .system import get_prefs
                    p = get_prefs()
                    if p is not None:
                        existing_packs = getattr(p, "resource_packs", None) or []
                        has_any_valid = any(
                            bool(getattr(x, "path", "")) and Path(getattr(x, "path", "")).exists()
                            for x in existing_packs
                        )
                        if not has_any_valid:
                            apply_instance_to_preferences(best, p)
                            try:
                                from ..utils.assets.guard import invalidate_guard_cache
                                invalidate_guard_cache()
                            except Exception:
                                pass
                            logger.info(
                                "MoziToolKit: Automatically configured Minecraft %s (%s) as default asset stack.",
                                best.minecraft_version,
                                best.launcher_kind,
                            )
                            try:
                                from ..ui.preferences_packs import refresh_ui_and_menus
                                refresh_ui_and_menus(bpy.context)
                            except Exception:
                                pass
                except Exception as e:
                    logger.debug("Failed applying auto-configured instance on main thread: %s", e)
                return None

            try:
                if hasattr(bpy, "app") and hasattr(bpy.app, "timers"):
                    bpy.app.timers.register(_apply_on_main, first_interval=0.0)
                else:
                    _apply_on_main()
            except Exception:
                pass
        except Exception as e:
            logger.debug("Background instance auto-setup scan failed: %s", e)

    threading.Thread(target=_bg_worker, name="Mozi_Initial_AutoSetup", daemon=True).start()
