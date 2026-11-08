"""
MoziToolKit Dev API.
Headless programmatic interface for Agent MCP execution, automated testing,
and visual benchmarking inside a running Blender instance.
"""

from typing import Any, Dict, Optional
import time

from .. import bridge
from .loader import get_engine_status
from .profile_tracker import (
    clear_last_profile,
    get_last_profile,
    record_operation_profile,
)


def get_cache_stats(prefs=None, force_refresh: bool = True) -> Dict[str, Any]:
    """
    Returns human-readable and byte statistics for all cached packages on disk.
    Includes active cache readiness status.
    """
    if prefs is None:
        try:
            from ..utils.system.dependencies import get_prefs
            prefs = get_prefs()
        except Exception:
            prefs = None

    stats = dict(bridge.get_cache_stats(prefs=prefs, force_refresh=force_refresh))
    try:
        from ..utils.assets.guard import check_asset_cache_status
        stats["status"] = check_asset_cache_status(prefs)
    except Exception:
        stats["status"] = "UNKNOWN"
    return stats


def get_diagnostics(prefs=None) -> Dict[str, Any]:
    """Returns runtime diagnostic information including native engine status and cache state."""
    if prefs is None:
        try:
            from ..utils.system.dependencies import get_prefs
            prefs = get_prefs()
        except Exception:
            prefs = None

    info = get_engine_status()
    try:
        cache_stats = get_cache_stats(prefs=prefs, force_refresh=True)
        info["cache"] = cache_stats
        info["cache_status"] = cache_stats.get("status", "UNKNOWN")
    except Exception as e:
        info["cache_error"] = str(e)

    info["debug_world_available"] = bridge.is_debug_world_available(prefs)
    return info


def load_debug_world(
    context=None,
    name: str = "MTK_Debug_World",
    enable_ao: bool = True,
    mesh_fluids: bool = True,
    weld_vertices: bool = True,
    origin_centered: bool = True,
    reuse_existing: bool = True,
    prefs=None,
) -> Any:
    """
    Instantly meshes and loads the embedded Minecraft debug world (32,539 blocks)
    into the active Blender scene with full model resolution, atlas UV remapping,
    and Atlas chunk PBR materials. Returns the created/updated bpy.types.Object.
    """
    if prefs is None:
        try:
            from ..utils.system.dependencies import get_prefs
            prefs = get_prefs(context)
        except Exception:
            prefs = None

    from ..utils.assets.guard import check_asset_cache_status, ensure_pipeline_assets_ready_sync
    status = check_asset_cache_status(prefs)
    if status in ("MISSING", "STALE"):
        ensure_pipeline_assets_ready_sync(prefs)

    from ..utils.world import create_debug_world_object
    obj, stats = create_debug_world_object(
        context=context,
        name=name,
        enable_ao=enable_ao,
        mesh_fluids=mesh_fluids,
        weld_vertices=weld_vertices,
        origin_centered=origin_centered,
        reuse_existing=reuse_existing,
        prefs=prefs,
    )
    return obj


def run_mesher_benchmark(
    iterations: int = 3,
    with_models: bool = True,
    num_threads: Optional[int] = None,
    prefs=None,
) -> Dict[str, Any]:
    """
    Benchmarks raw meshing throughput for the 32k-block debug world.
    When with_models is True (default), runs full block model baking + atlas UV remapping.
    Returns timing statistics and polygon generation rate.
    """
    if prefs is None:
        try:
            from ..utils.system.dependencies import get_prefs
            prefs = get_prefs()
        except Exception:
            prefs = None

    if not bridge.is_debug_world_available(prefs):
        raise RuntimeError("Native libmtk_py debug world capability is unavailable or vanilla client assets not configured.")

    if with_models:
        from ..utils.assets.guard import check_asset_cache_status, ensure_pipeline_assets_ready_sync
        status = check_asset_cache_status(prefs)
        if status in ("MISSING", "STALE"):
            ensure_pipeline_assets_ready_sync(prefs)

    storage = bridge.load_debug_world_storage(prefs=prefs)

    durations = []
    mesh_data = None
    for _ in range(iterations):
        m, elapsed_ms = bridge.generate_debug_world_mesh(
            storage=storage,
            with_models=with_models,
            prefs=prefs,
            num_threads=num_threads,
        )
        durations.append(elapsed_ms)
        mesh_data = m

    avg_ms = sum(durations) / len(durations)
    quads = mesh_data.quad_count if mesh_data else 0
    verts = mesh_data.vertex_count if mesh_data else 0

    quads_per_sec = (quads / (avg_ms / 1000.0)) if avg_ms > 0 else 0

    if mesh_data is not None and getattr(mesh_data, "profile", None) is not None:
        try:
            record_operation_profile("Mesher Benchmark", mesh_data.profile)
        except Exception:
            pass

    return {
        "iterations": iterations,
        "with_models": with_models,
        "avg_ms": round(avg_ms, 2),
        "min_ms": round(min(durations), 2),
        "max_ms": round(max(durations), 2),
        "quad_count": quads,
        "vertex_count": verts,
        "quads_per_second": int(quads_per_sec),
    }


def clear_material_cache(prefs=None) -> int:
    """
    Clears all precompiled material, model, and debug world caches via bridge.
    Returns the total bytes freed on disk.
    """
    if prefs is None:
        try:
            from ..utils.system.dependencies import get_prefs
            prefs = get_prefs()
        except Exception:
            prefs = None

    try:
        bridge.clear_debug_world_cache()
    except Exception:
        pass

    try:
        bridge.reset_shared_face_culler()
    except Exception:
        pass

    try:
        clear_last_profile()
    except Exception:
        pass

    freed = bridge.clear_cache(prefs)
    try:
        bridge.get_cache_stats(prefs, force_refresh=True)
    except Exception:
        pass
    return freed


clear_cache = clear_material_cache


def precompile_material_cache(
    prefs=None,
    num_threads: Optional[int] = None,
    progress_callback=None,
) -> Dict[str, Any]:
    """
    Precompiles and rebuilds complete material atlas, standalone textures,
    and model caches via bridge. Automatically prepares asset context and refreshes stats.
    """
    if prefs is None:
        try:
            from ..utils.system.dependencies import get_prefs
            prefs = get_prefs()
        except Exception:
            prefs = None

    from ..utils.assets.guard import prepare_asset_context
    asset_context = prepare_asset_context(prefs)

    if num_threads is None and prefs is not None:
        raw_threads = getattr(prefs, "thread_count", 0)
        num_threads = raw_threads if isinstance(raw_threads, int) and not isinstance(raw_threads, bool) and raw_threads > 0 else None

    res = bridge.precompile_stack(
        prefs,
        num_threads=num_threads,
        progress_callback=progress_callback,
        stack=asset_context.get("stack"),
        cache_dir=asset_context.get("cache_dir"),
    )

    try:
        bridge.clear_debug_world_cache()
    except Exception:
        pass

    try:
        bridge.get_cache_stats(prefs, force_refresh=True)
    except Exception:
        pass

    return res


precompile_cache = precompile_material_cache
rebuild_cache = precompile_material_cache


