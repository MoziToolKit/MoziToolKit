"""
Host-side execution profile aggregation and bridge profiling interface.

Collects per-stage timings coming from the Rust `ExecutionProfile` plus
host-side stages, and keeps the most recent operation for UI/diagnostics.
Deliberately dependency-free (pure data tracking) with zero DCC dependencies.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Dict, List, Optional, Tuple

_LAST_PROFILE: Optional[Dict[str, Any]] = None
_LOCK = threading.Lock()


def _make_stage(
    name: Any,
    duration_ms: Any,
    items_count: Any = None,
    source: str = "core",
    category: Optional[str] = None,
    depth: int = 0,
    start_offset_ms: float = 0.0,
) -> Dict[str, Any]:
    cat = category if category else ("host" if source == "host" else "core")
    return {
        "name": str(name),
        "category": str(cat),
        "depth": int(depth),
        "start_offset_ms": round(float(start_offset_ms or 0.0), 3),
        "duration_ms": round(float(duration_ms or 0.0), 3),
        "items_count": items_count,
        "source": source,
        "percent": 0.0,
    }


def _new_profile(operation: str) -> Dict[str, Any]:
    return {
        "operation": str(operation),
        "core_ms": 0.0,
        "host_ms": 0.0,
        "total_ms": 0.0,
        "accounted_ms": 0.0,
        "unaccounted_ms": 0.0,
        "stage_count": 0,
        "stages": [],
        "timestamp": time.strftime("%H:%M:%S"),
    }


def _recalculate(profile: Dict[str, Any]) -> None:
    """Recompute totals and percentages from the core/host split.

    Core time comes from Rust (it already includes uninstrumented work between
    its stages); host time is the sum of Blender-side stages appended later.
    Top-level stages (depth == 0) are used to compute the accounted work sum.
    The total never drops below the sum of all top-level stages.
    """
    stages = profile.get("stages", [])
    top_level_sum = sum(
        float(stage.get("duration_ms", 0.0))
        for stage in stages
        if stage.get("depth", 0) == 0
    )
    core_ms = float(profile.get("core_ms", 0.0) or 0.0)
    host_ms = float(profile.get("host_ms", 0.0) or 0.0)
    total_ms = max(core_ms + host_ms, top_level_sum)
    for stage in stages:
        duration = float(stage.get("duration_ms", 0.0))
        stage["percent"] = round((duration / total_ms) * 100.0, 1) if total_ms > 0 else 0.0

    accounted_ms = top_level_sum
    unaccounted_ms = max(0.0, total_ms - accounted_ms)

    profile["core_ms"] = round(core_ms, 3)
    profile["host_ms"] = round(host_ms, 3)
    profile["total_ms"] = round(total_ms, 3)
    profile["accounted_ms"] = round(accounted_ms, 3)
    profile["unaccounted_ms"] = round(unaccounted_ms, 3)
    profile["stage_count"] = len(stages)


def record_operation_profile(
    operation_name: str,
    profile_obj: Any,
    host_stages: Optional[List[Dict[str, Any]]] = None,
) -> None:
    """Records an execution profile snapshot from Rust or Python with optional host stages."""
    global _LAST_PROFILE
    if profile_obj is None and not host_stages:
        return

    stages: List[Dict[str, Any]] = []
    measured_total_ms = 0.0

    if hasattr(profile_obj, "stages"):
        measured_total_ms = float(getattr(profile_obj, "total_duration_ms", 0.0) or 0.0)
        for stage in profile_obj.stages:
            stages.append(
                _make_stage(
                    getattr(stage, "name", "stage"),
                    getattr(stage, "duration_ms", 0.0),
                    getattr(stage, "items_count", None),
                    "core",
                    getattr(stage, "category", "core"),
                    getattr(stage, "depth", 0),
                    getattr(stage, "start_offset_ms", 0.0),
                )
            )
    elif isinstance(profile_obj, dict):
        measured_total_ms = float(
            profile_obj.get("total_duration_ms", profile_obj.get("total_ms", 0.0)) or 0.0
        )
        for stage in profile_obj.get("stages", []):
            stages.append(
                _make_stage(
                    stage.get("name", "stage"),
                    stage.get("duration_ms", 0.0),
                    stage.get("items_count"),
                    stage.get("source", "core"),
                    stage.get("category", "core"),
                    stage.get("depth", 0),
                    stage.get("start_offset_ms", 0.0),
                )
            )

    host_total_ms = 0.0
    for host_stage in host_stages or []:
        duration = float(host_stage.get("duration_ms", 0.0) or 0.0)
        host_total_ms += duration
        stages.append(
            _make_stage(
                host_stage.get("name", "host_stage"),
                duration,
                host_stage.get("items_count"),
                "host",
                host_stage.get("category", "host"),
                host_stage.get("depth", 0),
                host_stage.get("start_offset_ms", 0.0),
            )
        )

    new_profile = _new_profile(operation_name)
    new_profile["core_ms"] = measured_total_ms
    new_profile["host_ms"] = host_total_ms
    new_profile["stages"] = stages
    _recalculate(new_profile)
    with _LOCK:
        _LAST_PROFILE = new_profile


def append_stage_to_last_profile(
    name: str,
    duration_ms: float,
    items_count: Optional[int] = None,
    category: str = "host",
) -> None:
    """Appends a host-side stage to the currently active profile (e.g. Blender injection)."""
    global _LAST_PROFILE
    with _LOCK:
        if _LAST_PROFILE is None:
            _LAST_PROFILE = _new_profile("Host Operation")
        duration = float(duration_ms or 0.0)
        _LAST_PROFILE["stages"].append(
            _make_stage(
                name=name,
                duration_ms=duration,
                items_count=items_count,
                source="host",
                category=category,
                depth=0,
            )
        )
        _LAST_PROFILE["host_ms"] = float(_LAST_PROFILE.get("host_ms", 0.0) or 0.0) + duration
        _recalculate(_LAST_PROFILE)


def get_last_profile() -> Optional[Dict[str, Any]]:
    """Returns the most recent execution profile, if any."""
    return _LAST_PROFILE


def profile_timing() -> Optional[Tuple[float, float, float]]:
    """Returns ``(core_ms, host_ms, total_ms)`` for the most recent operation."""
    profile = _LAST_PROFILE
    if not profile:
        return None
    return (
        float(profile.get("core_ms", 0.0) or 0.0),
        float(profile.get("host_ms", 0.0) or 0.0),
        float(profile.get("total_ms", 0.0) or 0.0),
    )


def format_timing_parts(core_ms: float, host_ms: float) -> str:
    """Formats a known core/host split, e.g. ``168.4ms (Rust 123.4 + Blender 45.0)``.

    Host-only operations (material building, pure Blender work) and core-only
    operations collapse to a single total.
    """
    core = float(core_ms or 0.0)
    host = float(host_ms or 0.0)
    total = core + host
    if host <= 0.0 or core <= 0.0:
        return f"{total:.1f}ms"
    return f"{total:.1f}ms (Rust {core:.1f} + Blender {host:.1f})"


def format_timing(
    fallback_ms: Optional[float] = None,
    operation: Optional[str] = None,
) -> str:
    """Formats the last operation duration, splitting Rust and Blender time.

    When ``operation`` is given, the profile must belong to it; otherwise the
    fallback (core-only) duration is returned so a stale profile is never shown.
    """
    profile = _LAST_PROFILE
    if not profile or (operation and profile.get("operation") != operation):
        return f"{float(fallback_ms or 0.0):.1f}ms"
    return format_timing_parts(
        float(profile.get("core_ms", 0.0) or 0.0),
        float(profile.get("host_ms", 0.0) or 0.0),
    )


def clear_last_profile() -> None:
    """Clears the cached profile."""
    global _LAST_PROFILE
    with _LOCK:
        _LAST_PROFILE = None


__all__ = [
    "record_operation_profile",
    "append_stage_to_last_profile",
    "get_last_profile",
    "clear_last_profile",
    "profile_timing",
    "format_timing",
    "format_timing_parts",
]
