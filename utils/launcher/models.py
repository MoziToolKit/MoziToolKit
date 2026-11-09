"""
Data models and launcher constants for Minecraft installation discovery.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

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
