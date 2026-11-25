"""
Canonical configuration property sanitization and fallback helpers for MoziToolKit.

Validates host/UI/saved `.blend` properties before passing them into native
`libmtk_py` entrypoints. When a property has an invalid type or unrecognized
value, logs a warning recording both the offending value and the fallback
default so a single malformed attribute never aborts the meshing pipeline.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Sequence, Tuple

logger = logging.getLogger("MoziToolKit.Bridge.ConfigValidation")

VALID_WELD_MODES: Tuple[str, ...] = ("manifold", "per_vertex", "none")
VALID_DIMENSIONS: Tuple[str, ...] = ("overworld", "the_nether", "the_end")
VALID_COORDINATE_SYSTEMS: Tuple[str, ...] = ("z_up", "y_up", "minecraft")
VALID_UNKNOWN_BLOCK_STRATEGIES: Tuple[str, ...] = ("FALLBACK_CUBE", "IGNORE_AS_AIR")

_DIMENSION_ALIASES: Dict[str, str] = {
    "minecraft:overworld": "overworld",
    "minecraft:the_nether": "the_nether",
    "nether": "the_nether",
    "minecraft:the_end": "the_end",
    "end": "the_end",
}

_UNKNOWN_BLOCK_ALIASES: Dict[str, str] = {
    "AIR": "IGNORE_AS_AIR",
}


def _unwrap_deferred_default(value: Any) -> Any:
    if type(value).__name__ == "_PropertyDeferred":
        keywords = getattr(value, "keywords", None)
        if isinstance(keywords, dict) and "default" in keywords:
            return keywords["default"]
        return getattr(value, "default", value)
    return value


def sanitize_enum_property(
    name: str,
    value: Any,
    valid_values: Sequence[str],
    default: str,
    *,
    uppercase: bool = False,
    aliases: Optional[Dict[str, str]] = None,
    log: Optional[logging.Logger] = None,
) -> str:
    """Validates an enum-like string property, warning and falling back to `default` if invalid."""
    raw = _unwrap_deferred_default(value)
    if isinstance(raw, str):
        normalized = raw.strip().upper() if uppercase else raw.strip().lower()
        if aliases and normalized in aliases:
            normalized = aliases[normalized]
        if normalized in valid_values:
            return normalized

    (log or logger).warning(
        "Invalid property '%s': got %r (%s); falling back to default %r",
        name,
        raw,
        type(raw).__name__,
        default,
    )
    return default


def sanitize_weld_mode(
    value: Any,
    *,
    name: str = "weld_mode",
    default: str = "manifold",
    log: Optional[logging.Logger] = None,
) -> str:
    """Validates `weld_mode` ('manifold' | 'per_vertex' | 'none')."""
    return sanitize_enum_property(
        name,
        value,
        VALID_WELD_MODES,
        default,
        log=log,
    )


def sanitize_dimension(
    value: Any,
    *,
    name: str = "dimension",
    default: str = "overworld",
    log: Optional[logging.Logger] = None,
) -> str:
    """Validates `dimension` ('overworld' | 'the_nether' | 'the_end')."""
    return sanitize_enum_property(
        name,
        value,
        VALID_DIMENSIONS,
        default,
        aliases=_DIMENSION_ALIASES,
        log=log,
    )


def sanitize_unknown_block_strategy(
    value: Any,
    *,
    name: str = "unknown_block_strategy",
    default: str = "FALLBACK_CUBE",
    log: Optional[logging.Logger] = None,
) -> str:
    """Validates `unknown_block_strategy` ('FALLBACK_CUBE' | 'IGNORE_AS_AIR')."""
    return sanitize_enum_property(
        name,
        value,
        VALID_UNKNOWN_BLOCK_STRATEGIES,
        default,
        uppercase=True,
        aliases=_UNKNOWN_BLOCK_ALIASES,
        log=log,
    )


def sanitize_coordinate_system(
    value: Any,
    *,
    name: str = "coordinate_system",
    default: str = "z_up",
    log: Optional[logging.Logger] = None,
) -> str:
    """Validates `coordinate_system` ('z_up' | 'y_up' | 'minecraft')."""
    return sanitize_enum_property(
        name,
        value,
        VALID_COORDINATE_SYSTEMS,
        default,
        log=log,
    )


def sanitize_bool_property(
    name: str,
    value: Any,
    default: bool,
    *,
    log: Optional[logging.Logger] = None,
) -> bool:
    """Validates a boolean (or Blender IDProperty 0/1 int), warning and falling back to `default`."""
    raw = _unwrap_deferred_default(value)
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, int) and raw in (0, 1):
        return bool(raw)

    (log or logger).warning(
        "Invalid property '%s': got %r (%s); falling back to default %r",
        name,
        raw,
        type(raw).__name__,
        default,
    )
    return default


def sanitize_int_vec3_property(
    name: str,
    value: Any,
    default: Tuple[int, int, int],
    *,
    log: Optional[logging.Logger] = None,
) -> Tuple[int, int, int]:
    """Validates a 3-element integer coordinate tuple, warning and falling back to `default`."""
    raw = _unwrap_deferred_default(value)
    try:
        items = list(raw)
        if len(items) == 3 and all(isinstance(v, int) and not isinstance(v, bool) for v in items):
            return (int(items[0]), int(items[1]), int(items[2]))
    except TypeError:
        pass

    (log or logger).warning(
        "Invalid property '%s': got %r (%s); falling back to default %r",
        name,
        raw,
        type(raw).__name__,
        default,
    )
    return default
