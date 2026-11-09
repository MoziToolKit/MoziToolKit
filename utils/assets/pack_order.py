"""
Tier ordering algorithm for Minecraft fallback resource packs and mod/vanilla JARs.
"""

from __future__ import annotations

from ..config import get_config_manager
from .pack_probe import _get_is_reordering, _set_is_reordering


def reorder_resource_packs_by_tier(prefs):
    """
    Sorts prefs.resource_packs CollectionProperty into three strict tiers:
    1. RESOURCE_PACK (top)
    2. MOD_JAR (middle)
    3. VANILLA (bottom)
    Preserves existing relative order within each tier using stable in-place moves,
    and updates resource_packs_index so active selection follows the moved pack.
    """
    if _get_is_reordering() or get_config_manager().is_syncing():
        return
    if prefs is None or not hasattr(prefs, "resource_packs") or len(prefs.resource_packs) <= 1:
        return

    def tier_key(item):
        pt = getattr(item, "pack_type", "RESOURCE_PACK")
        if pt == "RESOURCE_PACK":
            return 0
        elif pt == "MOD_JAR":
            return 1
        else:  # VANILLA
            return 2

    _set_is_reordering(True)
    try:
        n = len(prefs.resource_packs)
        active_idx = getattr(prefs, "resource_packs_index", -1)
        for i in range(1, n):
            j = i
            while j > 0 and tier_key(prefs.resource_packs[j]) < tier_key(prefs.resource_packs[j - 1]):
                prefs.resource_packs.move(j, j - 1)
                if active_idx == j:
                    active_idx = j - 1
                elif active_idx == j - 1:
                    active_idx = j
                j -= 1
        if 0 <= active_idx < n and hasattr(prefs, "resource_packs_index"):
            prefs.resource_packs_index = active_idx
    finally:
        _set_is_reordering(False)
