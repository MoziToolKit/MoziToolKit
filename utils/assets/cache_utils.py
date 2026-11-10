"""
Host-level asset cache directory resolvers, statistics, and system folder openers.
Implementation resides in bridge.assets.cache; this module re-exports them for backwards compatibility.
"""

from __future__ import annotations

from ...bridge.assets.cache import (
    clear_cache,
    get_cache_dir,
    get_cache_stats,
    invalidate_cache_stats_memo,
    open_cache_folder,
)

__all__ = [
    "get_cache_dir",
    "get_cache_stats",
    "invalidate_cache_stats_memo",
    "clear_cache",
    "open_cache_folder",
]
