"""
MoziToolKit World Bridge - Session Cache & Dynamic Entities Submodule.
Manages LRU cached VoxelWorld sessions and dynamic entity atlas/slot resolution.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger("MoziToolKit.Bridge.World.Session")

MAX_CACHED_WORLD_SESSIONS = 8
DIRTY_SECTION_REBUILD_THRESHOLD = 30

_WORLD_SESSIONS: Dict[str, Any] = {}
_WORLD_SESSION_META: Dict[str, Dict[str, Any]] = {}


def clear_world_sessions(session_key: Optional[str] = None) -> None:
    """Clears cached VoxelWorld instances across sessions."""
    global _WORLD_SESSIONS, _WORLD_SESSION_META
    if session_key is None:
        for w in _WORLD_SESSIONS.values():
            try:
                w.clear_cache()
            except Exception:
                pass
        _WORLD_SESSIONS.clear()
        _WORLD_SESSION_META.clear()
    else:
        w = _WORLD_SESSIONS.pop(session_key, None)
        if w is not None:
            try:
                w.clear_cache()
            except Exception:
                pass
        _WORLD_SESSION_META.pop(session_key, None)


def resolve_dynamic_entity_atlases(mesh_data: Any = None, session: Any = None) -> Dict[int, bytes]:
    """
    Canonical `{chunk_id: RGBA bytes}` dynamic entity atlas map.

    Resolution order: the atlas map already carried by ``mesh_data``, then the live
    sync session. Returns ``{}`` when the world has no dynamic entities (skins,
    banners, decorated pots).

    Every caller must use this helper instead of probing
    ``dynamic_entity_atlases`` / ``dynamic_entity_atlas`` by hand: provenance is a
    single multi-chunk dictionary, so a lone byte blob is never a valid shape.
    """
    if mesh_data is not None:
        atlases = mesh_data.dynamic_entity_atlases
        if atlases:
            return {int(chunk_id): data for chunk_id, data in atlases.items()}
    if session is not None:
        getter = getattr(session, "get_dynamic_entity_atlases", None)
        if callable(getter):
            try:
                atlases = getter()
                if atlases:
                    return {int(chunk_id): data for chunk_id, data in atlases.items()}
            except Exception as exc:
                logger.debug("Failed reading dynamic entity atlases from session: %s", exc)
    return {}


def get_world_session_dynamic_entity_atlases(session_key: str) -> Dict[int, bytes]:
    """Returns the `{chunk_id: RGBA bytes}` dynamic entity atlas map of a cached VoxelWorld session."""
    world = _WORLD_SESSIONS.get(session_key)
    return resolve_dynamic_entity_atlases(None, world) if world is not None else {}


def resolve_dynamic_entity_slots(mesh_data: Any = None, session: Any = None) -> Dict[str, Tuple[int, int]]:
    """
    Canonical `{key: (chunk_id, slot_idx)}` dynamic entity slot map.
    """
    if mesh_data is not None:
        slots = mesh_data.dynamic_entity_slots
        if slots:
            return {str(k): (int(v[0]), int(v[1])) for k, v in slots.items()}
    if session is not None:
        getter = getattr(session, "get_dynamic_entity_slots", None)
        if callable(getter):
            try:
                slots = getter()
                if slots:
                    return {str(k): (int(v[0]), int(v[1])) for k, v in slots.items()}
            except Exception as exc:
                logger.debug("Failed reading dynamic entity slots from session: %s", exc)
    return {}


def get_world_session_dynamic_entity_slots(session_key: str) -> Dict[str, Tuple[int, int]]:
    """Returns the `{key: (chunk_id, slot_idx)}` dynamic entity slot map of a cached VoxelWorld session."""
    world = _WORLD_SESSIONS.get(session_key)
    return resolve_dynamic_entity_slots(None, world) if world is not None else {}
