"""
MoziToolKit Asset Guard Evaluation Submodule.
Pure Python snapshots, configuration signatures, and vanilla pack validity probing.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from ...bridge.engine import get_libmtk
from ...i18n import tr
from ...utils.system import get_prefs
from .cache_utils import get_cache_dir

logger = logging.getLogger("MoziToolKit.Utils.Assets.GuardEval")

from ...bridge.assets.pack import (
    ENV_ASSET_ARCHIVES,
    ENV_ASSET_ROOTS,
    _VANILLA_CHECK_CACHE,
    _VANILLA_PROBE_CACHE,
    has_valid_vanilla_pack,
    path_provides_vanilla_client,
)

_path_provides_vanilla_client = path_provides_vanilla_client

#: Block identifiers probed by Rust core for canonical base foundation.
BASE_PROBE_BLOCKS = ("stone", "dirt", "oak_planks")


class PurePackEntry:
    """Pure Python representation of a pack entry, free of Blender RNA pointers."""
    def __init__(self, path: str = "", enabled: bool = True, pack_type: str = "RESOURCE_PACK", name: str = "", detected_kind: str = ""):
        self.path = str(path)
        self.enabled = bool(enabled)
        self.pack_type = str(pack_type)
        self.name = str(name)
        self.detected_kind = str(detected_kind)


class PackConfigSnapshot:
    """Pure Python snapshot of preferences configuration for thread-safe evaluation."""
    def __init__(self, resource_packs, resource_pack_path: str = "", cache_dir: str = "", thread_count: int = 0):
        self.resource_packs = list(resource_packs)
        self.resource_pack_path = str(resource_pack_path)
        self.cache_dir = str(cache_dir)
        self.thread_count = int(thread_count)


def snapshot_pack_config(prefs=None) -> PackConfigSnapshot:
    """Snapshot Blender AddonPreferences into a thread-safe pure Python DTO."""
    if prefs is None:
        try:
            prefs = get_prefs()
        except Exception:
            prefs = None

    packs = []
    if prefs is not None and hasattr(prefs, "resource_packs"):
        for e in prefs.resource_packs:
            packs.append(PurePackEntry(
                path=getattr(e, "path", "") or "",
                enabled=getattr(e, "enabled", True),
                pack_type=getattr(e, "pack_type", "RESOURCE_PACK"),
                name=getattr(e, "name", "") or "",
                detected_kind=getattr(e, "detected_kind", "") or "",
            ))

    single = str(getattr(prefs, "resource_pack_path", "") or "")
    try:
        resolved_cache_dir = str(get_cache_dir(prefs).resolve())
    except Exception:
        resolved_cache_dir = ""
    raw_threads = getattr(prefs, "thread_count", 0) if prefs is not None else 0
    thread_count = raw_threads if isinstance(raw_threads, int) and not isinstance(raw_threads, bool) else 0

    return PackConfigSnapshot(
        resource_packs=packs,
        resource_pack_path=single,
        cache_dir=resolved_cache_dir,
        thread_count=thread_count,
    )


def _get_pack_signature(prefs=None) -> tuple:
    if prefs is None:
        try:
            prefs = get_prefs()
        except Exception:
            prefs = None
    packs = getattr(prefs, "resource_packs", None) or []
    entries = []
    for e in packs:
        raw = getattr(e, "path", "") or ""
        enabled = getattr(e, "enabled", True)
        pt = getattr(e, "pack_type", "")
        mtime = 0.0
        if raw:
            try:
                mtime = os.path.getmtime(raw)
            except OSError:
                mtime = 0.0
        entries.append((raw, enabled, pt, mtime))
    single = getattr(prefs, "resource_pack_path", "") or ""
    single_mtime = 0.0
    if single:
        try:
            single_mtime = os.path.getmtime(single)
        except OSError:
            single_mtime = 0.0
    env_sig = tuple((k, os.environ.get(k, "")) for k in ENV_ASSET_ROOTS + ENV_ASSET_ARCHIVES)
    return (tuple(entries), single, single_mtime, env_sig)



def can_execute_asset_pipeline(prefs=None, check_status_fn=None) -> Tuple[bool, str]:
    """
    Evaluates whether the asset pipeline (meshing, material replacement, precompilation) can be executed.
    Returns (can_execute, reason_message).
    When False, reason_message provides a clear, user-facing explanation suitable for UI tooltips or reports.
    """
    if not has_valid_vanilla_pack(prefs):
        status = check_status_fn(prefs) if check_status_fn else "NO_VANILLA"
        if status == "NO_PACKS":
            return False, tr("No Minecraft resource packs configured. Please configure a Vanilla Client JAR in Preferences.")
        return False, tr("A Minecraft Vanilla Client JAR is required. Resource packs and mods cannot be used without a vanilla base pack.")
    return True, ""
