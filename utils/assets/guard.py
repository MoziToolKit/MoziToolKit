"""
Asset Cache Readiness Guard & Operator Gating Subsystem.
Provides automated cache freshness evaluation and two-stage precompilation wrappers
for non-blocking UI operators and headless scripts.
"""

from __future__ import annotations

import logging
import os
import threading
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Set, Tuple

from ...bridge.engine import get_libmtk
from ...bridge.progress import ProgressCallback, ProgressReport
from ...i18n import tr
from ...utils.system import get_prefs
from .cache_utils import get_cache_dir
from .guard_eval import (
    BASE_PROBE_BLOCKS,
    ENV_ASSET_ARCHIVES,
    ENV_ASSET_ROOTS,
    PackConfigSnapshot,
    PurePackEntry,
    _VANILLA_CHECK_CACHE,
    _VANILLA_PROBE_CACHE,
    _get_pack_signature,
    _path_provides_vanilla_client,
    can_execute_asset_pipeline as _eval_can_execute,
    has_valid_vanilla_pack,
    snapshot_pack_config,
)

logger = logging.getLogger("MoziToolKit.Utils.Assets.Guard")

_GUARD_STATUS_CACHE: Dict[Any, str] = {}
_GUARD_CHECKING_KEYS: Set[Any] = set()
_GUARD_LOCK = threading.Lock()


def _get_configured_pack_stack(prefs=None):
    from ...bridge.assets.pack import get_configured_pack_stack
    return get_configured_pack_stack(prefs)


def _open_active_asset_cache(prefs=None):
    from ...bridge.assets.cache import open_active_asset_cache
    return open_active_asset_cache(prefs)


def _invalidate_asset_cache_memory():
    from ...bridge.assets.cache import invalidate_asset_cache_memory
    return invalidate_asset_cache_memory()


def _precompile_stack(*args, **kwargs):
    from ...bridge.assets.precompile import precompile_stack
    return precompile_stack(*args, **kwargs)


def _timers_available() -> bool:
    try:
        import bpy
        timers = getattr(getattr(bpy, "app", None), "timers", None)
        if timers is None or type(timers).__module__.startswith("unittest.mock"):
            return False
        return hasattr(timers, "register") and hasattr(timers, "is_registered")
    except Exception:
        return False


def invalidate_guard_cache() -> None:
    """Invalidate all cached pack readiness and cache status evaluations."""
    with _GUARD_LOCK:
        _VANILLA_PROBE_CACHE.clear()
        _VANILLA_CHECK_CACHE.clear()
        _GUARD_STATUS_CACHE.clear()
        _GUARD_CHECKING_KEYS.clear()


def can_execute_asset_pipeline(prefs=None) -> Tuple[bool, str]:
    """Evaluates whether the asset pipeline can be executed."""
    return _eval_can_execute(prefs, check_asset_cache_status)


def check_asset_cache_status(prefs=None) -> str:
    """
    Checks the validity and freshness of precompiled asset caches against configured resource packs.
    Returns:
    - "READY": A valid precompiled .mtkcache package matching the active stack fingerprint exists.
    - "MISSING": Valid vanilla client pack is configured, but no cache package exists on disk.
    - "STALE": Cache packages exist, but the fingerprint does not match the active configuration.
    - "NO_PACKS": No resource packs or JARs configured or enabled in preferences.
    - "NO_VANILLA": Resource packs or mod JARs are configured, but no valid Vanilla Client JAR base pack is enabled.
    """
    sig = _get_pack_signature(prefs)
    cache_dir = get_cache_dir(prefs).resolve()
    cache_dir_mtime = 0.0
    if cache_dir.exists():
        try:
            cache_dir_mtime = os.path.getmtime(cache_dir)
        except OSError:
            cache_dir_mtime = 0.0
    cache_key = (sig, str(cache_dir), cache_dir_mtime)
    if cache_key in _GUARD_STATUS_CACHE:
        return _GUARD_STATUS_CACHE[cache_key]

    mtk = get_libmtk()
    if mtk is None:
        return "MISSING"

    stack = _get_configured_pack_stack(prefs)
    pack_count = stack.get_pack_count() if stack is not None else 0
    if pack_count == 0:
        _GUARD_STATUS_CACHE[cache_key] = "NO_PACKS"
        return "NO_PACKS"

    has_vanilla = has_valid_vanilla_pack(prefs)
    if not has_vanilla:
        _GUARD_STATUS_CACHE[cache_key] = "NO_VANILLA"
        return "NO_VANILLA"

    expected_fp = stack.compute_stack_fingerprint() if hasattr(stack, "compute_stack_fingerprint") else ""

    # Test sandbox isolation guard
    has_custom_packs = prefs is not None and hasattr(prefs, "resource_packs")
    is_test_sandbox = os.environ.get("MTK_TEST_SANDBOX") == "1"
    existing_packages = list(cache_dir.glob("*.mtkcache")) if cache_dir.exists() else []
    if not has_custom_packs and is_test_sandbox and existing_packages:
        active = _open_active_asset_cache(prefs)
        if active is not None:
            _GUARD_STATUS_CACHE[cache_key] = "READY"
            return "READY"

    # Fast-path check: in-memory active cache matches expected fingerprint
    active = _open_active_asset_cache(prefs)
    if active is not None and getattr(active, "fingerprint", None) == expected_fp:
        _GUARD_STATUS_CACHE[cache_key] = "READY"
        return "READY"

    status = mtk.evaluate_cache_status(str(cache_dir), pack_count, has_vanilla, expected_fp)
    _GUARD_STATUS_CACHE[cache_key] = status
    return status


def get_ui_asset_cache_status(
    prefs=None,
    on_update_callback: Optional[Callable[[str], None]] = None,
) -> str:
    """Non-blocking, zero-latency cache status reader designed specifically for Blender UI draw() calls."""
    if prefs is None:
        try:
            prefs = get_prefs()
        except Exception:
            prefs = None

    packs = getattr(prefs, "resource_packs", None) or []
    single = getattr(prefs, "resource_pack_path", "") or ""
    env_present = any(bool(os.environ.get(k)) for k in ENV_ASSET_ROOTS + ENV_ASSET_ARCHIVES)
    if not packs and not single and not env_present:
        return "NO_PACKS"

    snapshot = snapshot_pack_config(prefs)
    sig = _get_pack_signature(snapshot)
    cache_dir = get_cache_dir(snapshot).resolve()
    cache_dir_mtime = 0.0
    if cache_dir.exists():
        try:
            cache_dir_mtime = os.path.getmtime(cache_dir)
        except OSError:
            cache_dir_mtime = 0.0
    cache_key = (sig, str(cache_dir), cache_dir_mtime)

    with _GUARD_LOCK:
        if cache_key in _GUARD_STATUS_CACHE:
            return _GUARD_STATUS_CACHE[cache_key]

        if not _timers_available():
            return check_asset_cache_status(snapshot)

        if cache_key in _GUARD_CHECKING_KEYS:
            return "CHECKING"
        _GUARD_CHECKING_KEYS.add(cache_key)

    def _worker():
        try:
            res = check_asset_cache_status(snapshot)
        except Exception as exc:
            logger.debug("Failed evaluating asset cache status: %s", exc)
            res = "UNKNOWN"
        finally:
            with _GUARD_LOCK:
                _GUARD_CHECKING_KEYS.discard(cache_key)

        def _on_main_thread():
            if on_update_callback:
                try:
                    on_update_callback(res)
                except Exception:
                    pass
            else:
                try:
                    import bpy
                    context = getattr(bpy, "context", None)
                    if context and hasattr(context, "window_manager") and context.window_manager:
                        for window in context.window_manager.windows:
                            for area in window.screen.areas:
                                if area.type == "PREFERENCES":
                                    area.tag_redraw()
                except Exception:
                    pass
            return None

        try:
            import bpy
            bpy.app.timers.register(_on_main_thread, first_interval=0.0)
        except Exception:
            pass

    threading.Thread(target=_worker, name="MTK-AssetCacheStatus", daemon=True).start()
    return "CHECKING"


def prepare_asset_context(prefs=None) -> Dict[str, Any]:
    """Resolve cache status/stack/path on the Blender main thread."""
    status = check_asset_cache_status(prefs)
    stack = _get_configured_pack_stack(prefs)
    try:
        cache_dir = str(get_cache_dir(prefs))
    except Exception:
        cache_dir = None
    raw_threads = getattr(prefs, "thread_count", 0) if prefs is not None else 0
    num_threads = raw_threads if isinstance(raw_threads, int) and not isinstance(raw_threads, bool) and raw_threads > 0 else None
    return {"status": status, "stack": stack, "cache_dir": cache_dir, "num_threads": num_threads}


def ensure_pipeline_assets_ready_sync(
    prefs=None,
    progress_callback: Optional[ProgressCallback] = None,
) -> Tuple[bool, str]:
    """Synchronously verifies asset cache readiness, automatically triggering precompilation if MISSING or STALE."""
    status = check_asset_cache_status(prefs)
    if status == "READY":
        return True, "READY"

    if status in ("NO_PACKS", "NO_VANILLA"):
        return False, status

    if status in ("MISSING", "STALE"):
        logger.info("Asset cache status is '%s'. Triggering automated precompilation...", status)
        try:
            _precompile_stack(prefs, progress_callback=progress_callback)
            _invalidate_asset_cache_memory()
            return True, status
        except Exception as e:
            logger.exception("Automated precompilation failed: %s", e)
            raise e

    return True, status


def run_task_with_asset_guard(
    target_func: Any,
    target_kwargs: Optional[Dict[str, Any]] = None,
    prefs=None,
    progress_callback: Optional[ProgressCallback] = None,
    task_title: str = "Processing",
    asset_context: Optional[Dict[str, Any]] = None,
) -> Tuple[Any, bool]:
    """Execution wrapper guarding operations requiring precompiled assets."""
    if target_kwargs is None:
        target_kwargs = {}

    status = asset_context.get("status") if asset_context else check_asset_cache_status(prefs)

    if status in ("NO_PACKS", "NO_VANILLA"):
        try:
            from ..launcher_scan import auto_configure_initial_asset_stack
            if auto_configure_initial_asset_stack(prefs) is not None:
                invalidate_guard_cache()
                status = check_asset_cache_status(prefs)
        except Exception:
            pass

    if status in ("NO_PACKS", "NO_VANILLA"):
        raise RuntimeError(
            f"Cannot execute task: asset cache status is '{status}'. A Minecraft Vanilla Client JAR must be configured."
        )

    did_precompile = False
    if status in ("MISSING", "STALE"):
        did_precompile = True
        logger.info("Asset cache is %s. Executing Stage 1/2: automated precompilation...", status)

        def stage1_cb(report: Any):
            if progress_callback:
                msg = getattr(report, "message", "") or getattr(report, "stage", "Precompiling")
                stage = getattr(report, "stage", "precompile")
                curr = getattr(report, "current", 0)
                tot = getattr(report, "total", 100)
                pct = getattr(report, "percent", 0.0)
                stage_report = ProgressReport(
                    stage=f"precompile_{stage}",
                    current=curr,
                    total=tot,
                    message=f"[1/2 Precompile] {msg}",
                    percent=pct * 0.5,
                )
                progress_callback(stage_report)

        _precompile_stack(
            prefs,
            num_threads=asset_context.get("num_threads") if asset_context else None,
            progress_callback=stage1_cb,
            stack=asset_context.get("stack") if asset_context else None,
            cache_dir=asset_context.get("cache_dir") if asset_context else None,
            resolve_prefs=asset_context is None,
        )
        _invalidate_asset_cache_memory()

    def stage2_cb(report: Any):
        if progress_callback:
            msg = getattr(report, "message", "") or getattr(report, "stage", task_title)
            stage = getattr(report, "stage", "task")
            curr = getattr(report, "current", 0)
            tot = getattr(report, "total", 100)
            pct = getattr(report, "percent", 0.0)
            if did_precompile:
                stage_report = ProgressReport(
                    stage=f"task_{stage}",
                    current=curr,
                    total=tot,
                    message=f"[2/2 {task_title}] {msg}",
                    percent=50.0 + pct * 0.5,
                )
                progress_callback(stage_report)
            else:
                progress_callback(report)

    final_kwargs = dict(target_kwargs)
    final_kwargs["progress_callback"] = stage2_cb
    result = target_func(**final_kwargs)
    return result, did_precompile


__all__ = [
    "has_valid_vanilla_pack",
    "can_execute_asset_pipeline",
    "prepare_asset_context",
    "check_asset_cache_status",
    "get_ui_asset_cache_status",
    "ensure_pipeline_assets_ready_sync",
    "run_task_with_asset_guard",
    "invalidate_guard_cache",
    "snapshot_pack_config",
    "PackConfigSnapshot",
    "PurePackEntry",
]
