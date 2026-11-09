"""
MoziToolKit World Bridge - Meshing & Unknown Blocks Scanner Submodule.
High-performance offline conversion of VoxelStorage into modeled, textured, and shaded meshes.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional, Tuple

from ..assets import (
    get_cache_fingerprint,
    get_cache_timestamp,
    get_configured_ctm_solver,
)
from ..engine import require_libmtk
from .assets import (
    get_shared_face_culler,
    get_world_pipeline_assets,
    get_world_pipeline_colormaps,
)
from .session import (
    DIRTY_SECTION_REBUILD_THRESHOLD,
    MAX_CACHED_WORLD_SESSIONS,
    _WORLD_SESSION_META,
    _WORLD_SESSIONS,
)

logger = logging.getLogger("MoziToolKit.Bridge.World.Mesher")


def _get_world_mod():
    import sys
    return sys.modules.get("MoziToolKit.bridge.world")


def mesh_voxel_storage(
    storage: Any,
    config: Optional[Any] = None,
    model_db: Optional[Any] = None,
    atlas: Optional[Any] = None,
    biome_resolver: Optional[Any] = None,
    ctm_solver: Optional[Any] = None,
    prefs=None,
    enable_ao: bool = True,
    mesh_fluids: bool = True,
    weld_vertices: bool = True,
    origin_centered: bool = True,
    num_threads: Optional[int] = None,
    progress_callback: Optional[Any] = None,
    world_session_key: Optional[str] = None,
    material_mode: str = "ATLAS",
    force_rebuild: bool = False,
    enable_animated_textures: Optional[bool] = None,
    skin_cache_dir: Optional[str] = None,
    cache_fingerprint: Optional[str] = None,
    cache_timestamp: Optional[float] = None,
    resolve_prefs: bool = True,
    resolve_ctm: bool = True,
    colormaps: Optional[Any] = None,
    unknown_block_strategy: Optional[str] = None,
    z_up: bool = True,
    smooth_biome_blend: Optional[bool] = None,
) -> Tuple[Any, float]:
    """
    Meshes a VoxelStorage volume with full model resolution, atlas UV remapping,
    culling, and biome tinting. Supports incremental dirty sync when world_session_key is provided.
    Returns (mesh_data, elapsed_ms).
    """
    mtk = require_libmtk("mesh_voxel_storage")

    # Auto-load cached assets if not explicitly passed and caller didn't opt out
    if resolve_prefs and atlas is not False and material_mode != "STANDALONE":
        world_mod = _get_world_mod()
        assets_fn = getattr(world_mod, "get_world_pipeline_assets", get_world_pipeline_assets) if world_mod else get_world_pipeline_assets
        colormaps_fn = getattr(world_mod, "get_world_pipeline_colormaps", get_world_pipeline_colormaps) if world_mod else get_world_pipeline_colormaps
        if model_db is None or atlas is None or biome_resolver is None:
            cached_model_db, cached_atlas, cached_biome_resolver = assets_fn(prefs)
            model_db = model_db or cached_model_db
            atlas = atlas or cached_atlas
            biome_resolver = biome_resolver or cached_biome_resolver
        if colormaps is None:
            colormaps = colormaps_fn(prefs)
    else:
        if atlas is False:
            atlas = None

    # Auto-resolve CTM solver from pack stack if available
    if resolve_ctm:
        ctm_solver = ctm_solver or get_configured_ctm_solver(prefs)

    # Auto-resolve thread count from preferences if not explicitly set
    if resolve_prefs and num_threads is None:
        if prefs is None:
            try:
                from ...utils.system.dependencies import get_prefs
                prefs = get_prefs()
            except Exception:
                pass
        if prefs is not None:
            raw_threads = getattr(prefs, "thread_count", 0)
            num_threads = raw_threads if isinstance(raw_threads, int) and not isinstance(raw_threads, bool) and raw_threads > 0 else None

    anim_tex_enabled = enable_animated_textures
    if anim_tex_enabled is None:
        raw_val = getattr(prefs, "enable_animated_textures", True) if (resolve_prefs and prefs) else True
        anim_tex_enabled = raw_val if isinstance(raw_val, bool) else True
    else:
        anim_tex_enabled = bool(anim_tex_enabled)

    smooth_blend = smooth_biome_blend
    if smooth_blend is None:
        raw_val = getattr(prefs, "smooth_biome_blend", True) if (resolve_prefs and prefs) else True
        smooth_blend = raw_val if isinstance(raw_val, bool) else True
    else:
        smooth_blend = bool(smooth_blend)

    if unknown_block_strategy is None and resolve_prefs and prefs is not None:
        unknown_block_strategy = getattr(prefs, "unknown_block_strategy", "FALLBACK_CUBE")

    if config is None:
        config_kwargs = {
            "enable_ao": enable_ao,
            "mesh_fluids": mesh_fluids,
            "z_up_coordinates": z_up,
            "origin_centered": origin_centered,
            "weld_vertices": weld_vertices,
            "num_threads": num_threads,
            "atlas": atlas,
            "biome_resolver": biome_resolver,
            "enable_animated_textures": anim_tex_enabled,
            "smooth_biome_blend": smooth_blend,
        }
        if ctm_solver is not None:
            config_kwargs["ctm_solver"] = ctm_solver
        if colormaps is not None:
            config_kwargs["colormaps"] = colormaps
        if unknown_block_strategy is not None:
            config_kwargs["unknown_block_strategy"] = unknown_block_strategy
        config = mtk.MesherConfig(**config_kwargs)
    else:
        if num_threads is not None:
            config.num_threads = num_threads
        if ctm_solver is not None:
            config.set_ctm_solver(ctm_solver)
        config.enable_animated_textures = anim_tex_enabled
        config.set_smooth_biome_blend(smooth_blend)
        if colormaps is not None:
            config.set_colormaps(colormaps)
        if unknown_block_strategy is not None:
            config.unknown_block_strategy = unknown_block_strategy

    culler = get_shared_face_culler(mtk)

    if skin_cache_dir is None:
        try:
            from ..assets import get_cache_dir
            sc = get_cache_dir(prefs if resolve_prefs else None) / "skins"
            sc.mkdir(parents=True, exist_ok=True)
            skin_cache_dir = str(sc)
        except Exception:
            pass

    t0 = time.perf_counter()
    current_fp = None
    current_ts = None
    if world_session_key:
        if cache_timestamp is not None:
            current_fp = cache_fingerprint
            current_ts = float(cache_timestamp)
        else:
            current_fp = get_cache_fingerprint(prefs)
            current_ts = get_cache_timestamp(prefs)

    if world_session_key:
        cached_world = _WORLD_SESSIONS.get(world_session_key)
        cached_meta = _WORLD_SESSION_META.get(world_session_key)

        is_cache_valid = (
            cached_world is not None
            and cached_meta is not None
            and cached_meta.get("fingerprint") == current_fp
            and cached_meta.get("timestamp") == current_ts
            and not force_rebuild
        )

        if is_cache_valid:
            # Refresh LRU position
            _WORLD_SESSIONS[world_session_key] = _WORLD_SESSIONS.pop(world_session_key)
            dirty_count = cached_world.sync_from_storage(storage)
            if dirty_count == 0:
                mesh_data = cached_world.get_world_mesh()
            elif dirty_count <= DIRTY_SECTION_REBUILD_THRESHOLD:
                cached_world.rebuild_dirty()
                mesh_data = cached_world.get_world_mesh()
            else:
                mesh_data = cached_world.rebuild_all()
        else:
            if cached_world is not None:
                cached_world.clear_cache()
            cached_world = mtk.VoxelWorld.from_storage(
                storage=storage,
                config=config,
                culler=culler,
                model_db=model_db,
                num_threads=num_threads,
            )
            if skin_cache_dir is not None:
                try:
                    cached_world.set_skin_cache_dir(skin_cache_dir)
                except Exception:
                    pass

            # Enforce LRU capacity limit
            if len(_WORLD_SESSIONS) >= MAX_CACHED_WORLD_SESSIONS and world_session_key not in _WORLD_SESSIONS:
                oldest_key = next(iter(_WORLD_SESSIONS))
                oldest_world = _WORLD_SESSIONS.pop(oldest_key, None)
                if oldest_world is not None:
                    try:
                        oldest_world.clear_cache()
                    except Exception:
                        pass
                _WORLD_SESSION_META.pop(oldest_key, None)

            _WORLD_SESSIONS[world_session_key] = cached_world
            _WORLD_SESSION_META[world_session_key] = {
                "fingerprint": current_fp,
                "timestamp": current_ts,
            }
            if progress_callback is not None:
                from ..progress import wrap_progress_callback
                wrapped_cb = wrap_progress_callback(progress_callback)
                mesh_data = cached_world.rebuild_all(callback=wrapped_cb)
            else:
                mesh_data = cached_world.rebuild_all()
    else:
        world = mtk.VoxelWorld.from_storage(
            storage=storage,
            config=config,
            culler=culler,
            model_db=model_db,
            num_threads=num_threads,
        )
        if skin_cache_dir is not None:
            try:
                world.set_skin_cache_dir(skin_cache_dir)
            except Exception:
                pass
        if progress_callback is not None:
            from ..progress import wrap_progress_callback
            wrapped_cb = wrap_progress_callback(progress_callback)
            mesh_data = world.rebuild_all(callback=wrapped_cb)
        else:
            mesh_data = world.rebuild_all()
    t1 = time.perf_counter()

    if mesh_data.profile is not None:
        from ..diagnostics import record_operation_profile

        record_operation_profile("Mesh Voxel Storage", mesh_data.profile)

    elapsed_ms = (t1 - t0) * 1000.0
    return mesh_data, elapsed_ms


def scan_unknown_blocks(
    storage_or_cloud: Any,
    model_db: Optional[Any] = None,
    atlas: Optional[Any] = None,
    prefs=None,
    custom_aliases: Optional[Dict[str, Any]] = None,
) -> List[str]:
    """Scans a VoxelStorage or VoxelPointCloud for unknown blocks that lack models and texture candidates.

    Returns a sorted list of unique missing block state identifiers.
    """
    if storage_or_cloud is None or not hasattr(storage_or_cloud, "scan_unknown_blocks"):
        return []

    if model_db is None or atlas is None:
        cached_model_db, cached_atlas, _ = get_world_pipeline_assets(prefs)
        model_db = model_db or cached_model_db
        atlas = atlas or cached_atlas

    # Fail-closed: do not swallow exceptions and silently return [].
    # An error indicates invalid state/assets and must halt before fallback air/cubes.
    try:
        return list(
            storage_or_cloud.scan_unknown_blocks(
                model_db=model_db,
                atlas=atlas,
                custom_aliases=custom_aliases,
            )
        )
    except Exception as exc:
        logger.error("Failed scanning unknown blocks: %s", exc)
        raise RuntimeError(f"Failed scanning unknown blocks: {exc}") from exc
