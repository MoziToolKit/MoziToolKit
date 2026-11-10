"""
MoziToolKit Live Sync Bridge Package.

Encapsulates all interaction with native libmtk LiveSyncSession and
VoxelStorage engines. Provides clean data-in / data-out APIs for Blender
operators and panels.
"""

from __future__ import annotations

import logging
from typing import Optional

from ..engine import get_libmtk, require_libmtk
from ..assets import (
    load_biome_resolver_from_cache,
    load_colormaps_from_cache,
    load_baked_model_database,
    load_baked_atlas_from_cache,
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


# Global singleton bridge instance
_global_sync_session: Optional[SyncBridgeSession] = None


def get_sync_bridge_session() -> SyncBridgeSession:
    """Returns the shared global SyncBridgeSession singleton."""
    global _global_sync_session
    if _global_sync_session is None:
        _global_sync_session = SyncBridgeSession()
    return _global_sync_session


def reset_sync_bridge_session() -> SyncBridgeSession:
    """Stops and resets the global singleton session, returning the cleaned instance."""
    session = get_sync_bridge_session()
    session.reset()
    return session


__all__ = [
    "is_sync_available",
    "SyncBridgeSession",
    "get_sync_bridge_session",
    "reset_sync_bridge_session",
    "load_baked_model_database",
    "load_baked_atlas_from_cache",
    "load_biome_resolver_from_cache",
    "load_colormaps_from_cache",
    "check_cache_dirty",
    "get_cache_fingerprint",
    "get_cache_timestamp",
    "get_configured_ctm_solver",
    "get_libmtk",
    "require_libmtk",
]
