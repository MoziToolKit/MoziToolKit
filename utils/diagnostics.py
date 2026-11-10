"""
Host-side execution profile aggregation (convenience re-export).

Re-exports pure data profiling utilities from ``bridge.diagnostics``.
Maintains backward compatibility for host-side modules in utils/, operators/, UI, and tests.
"""

from __future__ import annotations

from ..bridge.diagnostics import (
    append_stage_to_last_profile,
    clear_last_profile,
    format_timing,
    format_timing_parts,
    get_last_profile,
    profile_timing,
    record_operation_profile,
)

__all__ = [
    "record_operation_profile",
    "append_stage_to_last_profile",
    "get_last_profile",
    "clear_last_profile",
    "profile_timing",
    "format_timing",
    "format_timing_parts",
]
