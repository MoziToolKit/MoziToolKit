"""
MoziToolKit Utils: Asset Management & Processing Subsystem.

Contains host-level asset helpers including cache directory resolvers and execution guards.
"""

from __future__ import annotations

from .cache_utils import (
    clear_cache,
    get_cache_dir,
    get_cache_stats,
    invalidate_cache_stats_memo,
    open_cache_folder,
)
from .guard import (
    can_execute_asset_pipeline,
    check_asset_cache_status,
    ensure_pipeline_assets_ready_sync,
    get_ui_asset_cache_status,
    has_valid_vanilla_pack,
    prepare_asset_context,
    run_task_with_asset_guard,
)

__all__ = [
    # Cache utils
    "get_cache_dir",
    "get_cache_stats",
    "invalidate_cache_stats_memo",
    "clear_cache",
    "open_cache_folder",
    # Guards
    "has_valid_vanilla_pack",
    "can_execute_asset_pipeline",
    "check_asset_cache_status",
    "get_ui_asset_cache_status",
    "prepare_asset_context",
    "ensure_pipeline_assets_ready_sync",
    "run_task_with_asset_guard",
]
