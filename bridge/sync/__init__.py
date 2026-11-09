"""
MoziToolKit Live Sync Bridge Package.

Encapsulates all interaction with native libmtk LiveSyncSession and
VoxelStorage engines. Provides clean data-in / data-out APIs for Blender
operators and panels.
"""

from __future__ import annotations

import builtins
import logging

from ..engine import get_libmtk, require_libmtk
from ..assets import (
    load_biome_resolver_from_cache,
    load_colormaps_from_cache,
    load_model_database_from_cache,
    load_atlas_from_cache,
    check_cache_dirty,
    get_cache_fingerprint,
    get_cache_timestamp,
    get_configured_ctm_solver,
)
from .session import SyncBridgeSession

logger = logging.getLogger("MoziToolKit.Bridge.Sync")


def is_sync_available() -> bool:
    """Checks whether the native libmtk Live Sync backend is loaded and ready."""
    mtk = get_libmtk()
    return mtk is not None


def require_sync() -> None:
    """Ensures native libmtk Live Sync backend is loaded, raising RuntimeError otherwise."""
    require_libmtk("Live Sync")


# Global singleton bridge instance (anchored in builtins to prevent dual-import split brain across packages)
_GLOBAL_SESSION_ATTR = "_mozi_global_sync_bridge_session"
if not hasattr(builtins, _GLOBAL_SESSION_ATTR):
    setattr(builtins, _GLOBAL_SESSION_ATTR, SyncBridgeSession())

_global_sync_session = getattr(builtins, _GLOBAL_SESSION_ATTR)


def get_sync_bridge_session() -> SyncBridgeSession:
    """Returns the shared global SyncBridgeSession singleton."""
    return getattr(builtins, _GLOBAL_SESSION_ATTR, _global_sync_session)


def reset_sync_bridge_session() -> SyncBridgeSession:
    """Stops and resets the global singleton session, returning the cleaned instance."""
    session = get_sync_bridge_session()
    session.reset()
    return session


__all__ = [
    "is_sync_available",
    "require_sync",
    "SyncBridgeSession",
    "get_sync_bridge_session",
    "reset_sync_bridge_session",
    "load_model_database_from_cache",
    "load_atlas_from_cache",
    "load_biome_resolver_from_cache",
    "load_colormaps_from_cache",
    "check_cache_dirty",
    "get_cache_fingerprint",
    "get_cache_timestamp",
    "get_configured_ctm_solver",
    "get_libmtk",
    "require_libmtk",
]
