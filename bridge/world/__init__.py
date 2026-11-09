"""
MoziToolKit Unified Voxel World Bridge Package.

Authoritative offline pipeline for converting any VoxelStorage (embedded debug world,
future Anvil save files, schematics, or sync streams) into fully modeled, textured,
and shaded Blender objects with Atlas PBR materials and Biome tinting.
Completely host-agnostic and decoupled from WebSocket network synchronization.
"""

from __future__ import annotations

# Re-export assets readers & helpers for mock patching compatibility in tests
from ..assets import (
    get_active_cache_package_path,
    get_cache_fingerprint,
    get_cache_timestamp,
    get_configured_ctm_solver,
)
from .assets import (
    get_shared_face_culler,
    get_world_pipeline_assets,
    get_world_pipeline_colormaps,
    invalidate_world_pipeline_assets,
    prepare_world_mesh_context,
    reset_shared_face_culler,
)
from .mesher import mesh_voxel_storage, scan_unknown_blocks
from .session import (
    DIRTY_SECTION_REBUILD_THRESHOLD,
    MAX_CACHED_WORLD_SESSIONS,
    _WORLD_SESSION_META,
    _WORLD_SESSIONS,
    clear_world_sessions,
    get_world_session_dynamic_entity_atlases,
    get_world_session_dynamic_entity_slots,
    resolve_dynamic_entity_atlases,
    resolve_dynamic_entity_slots,
)

__all__ = [
    "get_shared_face_culler",
    "reset_shared_face_culler",
    "invalidate_world_pipeline_assets",
    "get_world_pipeline_assets",
    "get_world_pipeline_colormaps",
    "prepare_world_mesh_context",
    "clear_world_sessions",
    "resolve_dynamic_entity_atlases",
    "get_world_session_dynamic_entity_atlases",
    "resolve_dynamic_entity_slots",
    "get_world_session_dynamic_entity_slots",
    "mesh_voxel_storage",
    "scan_unknown_blocks",
    "_WORLD_SESSIONS",
    "_WORLD_SESSION_META",
    "MAX_CACHED_WORLD_SESSIONS",
    "DIRTY_SECTION_REBUILD_THRESHOLD",
    "get_active_cache_package_path",
    "get_configured_ctm_solver",
    "get_cache_fingerprint",
    "get_cache_timestamp",
]
