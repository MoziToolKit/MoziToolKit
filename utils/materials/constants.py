"""
Constants for MoziToolKit Material and Atlas System.
Authoritative block models, culling rules, and texture categorizations
are computed directly in Rust (libmtk).
"""

from __future__ import annotations

# Custom Property Keys on Materials and Images (namespaced with mtk:)
PROP_ATLAS_MAPPING = "mtk:atlas_mapping"
PROP_CREATED_BY = "mtk:created_by"


def chunk_supports_tint(cm: dict, category: str) -> bool:
    """Return whether an atlas chunk should have biome tint enabled."""
    return bool(cm.get("has_tint", True)) if category in ("blocks", "scene") else False
