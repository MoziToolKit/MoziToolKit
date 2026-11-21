"""
Host-level asset cache statistics, clearing, and OS file manager openers.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Optional

from ...bridge.assets.cache import (
    get_cache_dir,
    invalidate_asset_cache_memory,
    register_cache_invalidation_callback,
)

logger = logging.getLogger("MoziToolKit.Utils.Assets.CacheOps")

_cached_cache_stats: Optional[Dict[str, Any]] = None
_cached_cache_stats_path: Optional[str] = None


def invalidate_cache_stats_memo() -> None:
    """Invalidates the memoized cache statistics so the next read re-scans the directory."""
    global _cached_cache_stats, _cached_cache_stats_path
    _cached_cache_stats = None
    _cached_cache_stats_path = None


register_cache_invalidation_callback(invalidate_cache_stats_memo)


def get_cache_stats(prefs=None, force_refresh: bool = False) -> Dict[str, Any]:
    """
    Returns human-readable and byte statistics for all cached packages on disk.
    Memoized against the resolved cache path string.
    """
    global _cached_cache_stats, _cached_cache_stats_path
    cache_path = get_cache_dir(prefs)
    path_str = str(cache_path)

    if (
        not force_refresh
        and _cached_cache_stats_path is not None
        and _cached_cache_stats is not None
        and _cached_cache_stats_path == path_str
    ):
        return _cached_cache_stats

    total_size = 0
    file_count = 0

    if cache_path.exists():
        for root, _, files in os.walk(cache_path):
            for f in files:
                fp = Path(root) / f
                try:
                    total_size += fp.stat().st_size
                    file_count += 1
                except Exception:
                    pass

    if total_size < 1024:
        size_str = f"{total_size} B"
    elif total_size < 1024 * 1024:
        size_str = f"{total_size / 1024:.1f} KB"
    elif total_size < 1024 * 1024 * 1024:
        size_str = f"{total_size / (1024 * 1024):.1f} MB"
    else:
        size_str = f"{total_size / (1024 * 1024 * 1024):.2f} GB"

    _cached_cache_stats = {
        "path": path_str,
        "size_bytes": total_size,
        "size_formatted": size_str,
        "files_count": file_count,
    }
    _cached_cache_stats_path = path_str
    return _cached_cache_stats


def clear_cache(prefs=None, on_invalidate_callback=None) -> int:
    """Empties all compiled caches in the cache directory and returns the total bytes freed."""
    global _cached_cache_stats, _cached_cache_stats_path

    invalidate_asset_cache_memory()

    if on_invalidate_callback is not None:
        try:
            on_invalidate_callback()
        except Exception:
            pass

    cache_path = get_cache_dir(prefs)
    freed_bytes = 0

    def _item_size(p: Path) -> int:
        if p.is_file():
            try:
                return p.stat().st_size
            except Exception:
                return 0
        total = 0
        try:
            for root, _, files in os.walk(p):
                for f in files:
                    try:
                        total += (Path(root) / f).stat().st_size
                    except Exception:
                        pass
        except Exception:
            pass
        return total

    if cache_path.exists():
        for item in list(cache_path.iterdir()):
            size = _item_size(item)
            try:
                if item.is_dir():
                    shutil.rmtree(item, ignore_errors=False)
                else:
                    item.unlink()
                freed_bytes += size
            except Exception as exc:
                logger.warning("Failed to remove cache entry %s: %s", item, exc)

    _cached_cache_stats = None
    _cached_cache_stats_path = None
    return freed_bytes


def open_cache_folder(prefs=None) -> Path:
    """Opens the persistent cache directory in the host operating system's native file explorer."""
    cache_path = get_cache_dir(prefs)
    try:
        cache_path.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

    if sys.platform == "win32":
        os.startfile(str(cache_path))
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(cache_path)])
    else:
        subprocess.Popen(["xdg-open", str(cache_path)])
    return cache_path


__all__ = [
    "get_cache_stats",
    "invalidate_cache_stats_memo",
    "clear_cache",
    "open_cache_folder",
]
