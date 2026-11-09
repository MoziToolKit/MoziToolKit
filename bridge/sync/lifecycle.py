"""
MoziToolKit Live Sync Session Lifecycle & Configuration.

Handles MesherConfig assembly, session instantiation, handshake dispatch,
and hot-reloading on asset cache modifications.
"""

from __future__ import annotations

import logging
import sys
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger("MoziToolKit.Bridge.Sync.Lifecycle")


def _get_sync_mod():
    """Dynamically retrieves the MoziToolKit.bridge.sync module to support mock patches in tests."""
    return sys.modules.get("MoziToolKit.bridge.sync")


class SyncLifecycleMixin:
    """Mixin implementing connection lifecycle, startup, and hot-reload for SyncBridgeSession."""

    def _prepare_config(
        self,
        url: str,
        model_db: Optional[Any],
        atlas: Optional[Any],
        enable_ao: bool,
        mesh_fluids: bool,
        biome_resolver: Optional[Any],
        custom_aliases: Optional[Any],
        num_threads: Optional[int],
        auth_token: Optional[str],
        enable_animated_textures: Optional[bool],
        colormaps: Optional[Any],
        smooth_biome_blend: Optional[bool],
    ) -> Tuple[Optional[Any], Optional[str], Optional[Any]]:
        sync_mod = _get_sync_mod()
        if sync_mod is not None:
            mtk = sync_mod.get_libmtk()
            get_cache_fingerprint = sync_mod.get_cache_fingerprint
            get_cache_timestamp = sync_mod.get_cache_timestamp
            get_configured_ctm_solver = getattr(sync_mod, "get_configured_ctm_solver", None)
        else:
            from ..engine import get_libmtk
            from ..assets import get_cache_fingerprint, get_cache_timestamp, get_configured_ctm_solver
            mtk = get_libmtk()

        if mtk is None:
            self._last_error = "Native libmtk_py core is not available."
            logger.error("Cannot start Live Sync: native libmtk_py core is not available.")
            return None, None, None

        self.stop()
        self._last_error = ""

        # The Yefira config parser trims auth tokens (RFC v3.1 §4.1).
        clean_token = (auth_token or "").strip() or None

        prefs = None
        try:
            from ...utils.system import get_prefs
            prefs = get_prefs()
        except Exception:
            prefs = None

        if enable_animated_textures is None:
            raw_anim = getattr(prefs, "enable_animated_textures", True) if prefs is not None else True
            anim_enabled = raw_anim if isinstance(raw_anim, bool) else True
        else:
            anim_enabled = bool(enable_animated_textures)

        if smooth_biome_blend is None:
            raw_smooth = getattr(prefs, "smooth_biome_blend", True) if prefs is not None else True
            smooth_blend = raw_smooth if isinstance(raw_smooth, bool) else True
        else:
            smooth_blend = bool(smooth_biome_blend)

        self._model_db = model_db
        self._atlas = atlas
        self._biome_resolver = biome_resolver
        self._custom_aliases = custom_aliases
        self._config_params = {
            "enable_ao": enable_ao,
            "mesh_fluids": mesh_fluids,
            "num_threads": num_threads,
            "enable_animated_textures": anim_enabled,
            "smooth_biome_blend": smooth_blend,
        }
        self._cached_manifest_fingerprint = get_cache_fingerprint()
        self._cached_manifest_timestamp = get_cache_timestamp()
        self._current_url = url

        if num_threads is None and prefs is not None:
            num_threads = getattr(prefs, "thread_count", 0) or None

        ctm_solver = get_configured_ctm_solver(prefs) if get_configured_ctm_solver is not None else None
        self._colormaps = colormaps

        config_kwargs = {
            "enable_ao": enable_ao,
            "mesh_fluids": mesh_fluids,
            "z_up_coordinates": True,
            "origin_centered": True,
            "weld_vertices": True,
            "num_threads": num_threads,
            "atlas": atlas,
            "biome_resolver": biome_resolver,
            "custom_aliases": custom_aliases,
            "enable_animated_textures": anim_enabled,
            "smooth_biome_blend": smooth_blend,
        }
        if ctm_solver is not None:
            config_kwargs["ctm_solver"] = ctm_solver
        if colormaps is not None:
            config_kwargs["colormaps"] = colormaps

        config = mtk.MesherConfig(**config_kwargs)
        return config, clean_token, mtk

    def start(
        self,
        url: str = "ws://127.0.0.1:8765",
        auto_reconnect: bool = True,
        max_reconnect_attempts: int = 5,
        model_db: Optional[Any] = None,
        atlas: Optional[Any] = None,
        enable_ao: bool = True,
        mesh_fluids: bool = True,
        biome_resolver: Optional[Any] = None,
        custom_aliases: Optional[Any] = None,
        num_threads: Optional[int] = None,
        auth_token: Optional[str] = None,
        enable_animated_textures: Optional[bool] = None,
        colormaps: Optional[Any] = None,
        smooth_biome_blend: Optional[bool] = None,
    ) -> bool:
        """Starts the background LiveSync client thread connecting to `url`."""
        config, clean_token, mtk = self._prepare_config(
            url=url,
            model_db=model_db,
            atlas=atlas,
            enable_ao=enable_ao,
            mesh_fluids=mesh_fluids,
            biome_resolver=biome_resolver,
            custom_aliases=custom_aliases,
            num_threads=num_threads,
            auth_token=auth_token,
            enable_animated_textures=enable_animated_textures,
            colormaps=colormaps,
            smooth_biome_blend=smooth_biome_blend,
        )
        if config is None or mtk is None:
            return False

        try:
            def _start_native(session: Any) -> None:
                if clean_token:
                    session.start(url, auto_reconnect, max_reconnect_attempts, auth_token=clean_token)
                else:
                    session.start(url, auto_reconnect, max_reconnect_attempts)

            if self._session is not None:
                try:
                    self._session.hot_reload(config, model_db)
                    if self._skin_cache_dir:
                        try:
                            self._session.set_skin_cache_dir(self._skin_cache_dir)
                        except Exception as e:
                            logger.debug("Failed setting skin cache dir on hot reload: %s", e)
                    _start_native(self._session)
                    self._is_active = True
                    self._is_connected = False
                    self._connection_status = "CONNECTING..."
                    logger.info("Reused existing LiveSyncSession connecting to %s", url)
                    return True
                except Exception as e:
                    logger.debug("Failed reusing existing session, creating new: %s", e)
                    self._session = None

            culler = mtk.FaceCuller()
            self._session = mtk.LiveSyncSession(
                config=config,
                culler=culler,
                model_db=model_db,
            )
            if self._skin_cache_dir:
                try:
                    self._session.set_skin_cache_dir(self._skin_cache_dir)
                except Exception as e:
                    logger.debug("Failed setting skin cache dir on new session: %s", e)

            _start_native(self._session)
            self._is_active = True
            self._is_connected = False
            self._connection_status = "CONNECTING..."
            logger.info("LiveSyncSession started connecting to %s", url)
            return True
        except Exception as e:
            self._last_error = str(e)
            logger.error("Failed to start LiveSyncSession: %s", e)
            self._session = None
            self._is_active = False
            self._is_connected = False
            self._connection_status = "DISCONNECTED"
            return False

    def start_with_cache(
        self,
        url: str = "ws://127.0.0.1:8765",
        auto_reconnect: bool = True,
        max_reconnect_attempts: int = 5,
        server_uuid: str = "",
        epoch: int = 0,
        sequence: int = 0,
        min_pos: tuple[int, int, int] = (0, 0, 0),
        size: tuple[int, int, int] = (0, 0, 0),
        state_hash: int = 0,
        dimension: str = "minecraft:overworld",
        point_cloud: Optional[Any] = None,
        model_db: Optional[Any] = None,
        atlas: Optional[Any] = None,
        enable_ao: bool = True,
        mesh_fluids: bool = True,
        biome_resolver: Optional[Any] = None,
        custom_aliases: Optional[Any] = None,
        num_threads: Optional[int] = None,
        auth_token: Optional[str] = None,
        enable_animated_textures: Optional[bool] = None,
        colormaps: Optional[Any] = None,
        smooth_biome_blend: Optional[bool] = None,
    ) -> bool:
        """
        Starts the LiveSync client using cached handshake parameters for 0ms zero-rebuild reconnect.
        Restores VoxelStorage from `point_cloud` if provided.
        """
        config, clean_token, mtk = self._prepare_config(
            url=url,
            model_db=model_db,
            atlas=atlas,
            enable_ao=enable_ao,
            mesh_fluids=mesh_fluids,
            biome_resolver=biome_resolver,
            custom_aliases=custom_aliases,
            num_threads=num_threads,
            auth_token=auth_token,
            enable_animated_textures=enable_animated_textures,
            colormaps=colormaps,
            smooth_biome_blend=smooth_biome_blend,
        )
        if config is None or mtk is None:
            return False

        try:
            def _start_cached_native(session: Any) -> None:
                if point_cloud is not None:
                    session.load_from_point_cloud(point_cloud)
                session.start_with_cache(
                    url=url,
                    auto_reconnect=auto_reconnect,
                    max_reconnect_attempts=max_reconnect_attempts,
                    server_uuid=server_uuid,
                    epoch=epoch,
                    sequence=sequence,
                    min_pos=min_pos,
                    size=size,
                    state_hash=state_hash,
                    dimension=dimension,
                    auth_token=clean_token,
                )

            if self._session is not None:
                try:
                    self._session.hot_reload(config, model_db)
                    if self._skin_cache_dir:
                        try:
                            self._session.set_skin_cache_dir(self._skin_cache_dir)
                        except Exception as e:
                            logger.debug("Failed setting skin cache dir on hot reload: %s", e)
                    _start_cached_native(self._session)
                    self._is_active = True
                    self._is_connected = False
                    self._connection_status = "CONNECTING..."
                    logger.info("Reused existing LiveSyncSession connecting with cache to %s", url)
                    return True
                except Exception as e:
                    logger.debug("Failed reusing existing session for start_with_cache, creating new: %s", e)
                    self._session = None

            culler = mtk.FaceCuller()
            self._session = mtk.LiveSyncSession(
                config=config,
                culler=culler,
                model_db=model_db,
            )
            if self._skin_cache_dir:
                try:
                    self._session.set_skin_cache_dir(self._skin_cache_dir)
                except Exception as e:
                    logger.debug("Failed setting skin cache dir on new session: %s", e)

            _start_cached_native(self._session)
            self._is_active = True
            self._is_connected = False
            self._connection_status = "CONNECTING..."
            logger.info("LiveSyncSession started with cache connecting to %s", url)
            return True
        except Exception as e:
            self._last_error = str(e)
            logger.error("Failed to start LiveSyncSession with cache: %s", e)
            self._session = None
            self._is_active = False
            self._is_connected = False
            self._connection_status = "DISCONNECTED"
            return False

    def check_and_reload_dirty_cache(self, prefs=None, force: bool = False) -> bool:
        """
        Checks whether the on-disk asset cache has been modified or recompiled.
        If dirty (or force=True):
        1. Reloads latest BakedAtlas, BakedModelDatabase, and BiomeResolver from disk.
        2. Updates the native LiveSyncSession config and model database.
        3. Clears dirty section mesh cache in Rust.
        """
        sync_mod = _get_sync_mod()
        if sync_mod is not None:
            check_cache_dirty = sync_mod.check_cache_dirty
            load_model_db = sync_mod.load_model_database_from_cache
            load_atlas = sync_mod.load_atlas_from_cache
            load_biome_resolver = sync_mod.load_biome_resolver_from_cache
            get_configured_ctm_solver = getattr(sync_mod, "get_configured_ctm_solver", None)
            mtk = sync_mod.get_libmtk()
        else:
            from ..assets import (
                check_cache_dirty,
                load_model_database_from_cache as load_model_db,
                load_atlas_from_cache as load_atlas,
                load_biome_resolver_from_cache as load_biome_resolver,
                get_configured_ctm_solver,
            )
            from ..engine import get_libmtk
            mtk = get_libmtk()

        is_dirty, current_fp, current_ts = check_cache_dirty(
            self._cached_manifest_fingerprint,
            self._cached_manifest_timestamp,
            prefs=prefs,
        )

        if not is_dirty and not force:
            return False

        logger.info(
            "Dirty cache detected (fp: %s -> %s, ts: %s -> %s). Hot-reloading assets...",
            self._cached_manifest_fingerprint,
            current_fp,
            self._cached_manifest_timestamp,
            current_ts,
        )

        new_model_db = load_model_db(prefs)
        new_atlas = load_atlas(prefs)
        new_biome_resolver = load_biome_resolver(prefs)

        self._model_db = new_model_db
        self._atlas = new_atlas
        self._biome_resolver = new_biome_resolver
        self._colormaps = None
        self._cached_manifest_fingerprint = current_fp
        self._cached_manifest_timestamp = current_ts

        if self._session is not None and mtk is not None:
            enable_ao = self._config_params.get("enable_ao", True)
            mesh_fluids = self._config_params.get("mesh_fluids", True)
            num_threads = self._config_params.get("num_threads", None)
            anim_enabled = self._config_params.get("enable_animated_textures")
            if anim_enabled is None:
                raw_anim = getattr(prefs, "enable_animated_textures", True) if prefs is not None else True
                anim_enabled = raw_anim if isinstance(raw_anim, bool) else True
            ctm_solver = get_configured_ctm_solver(prefs) if get_configured_ctm_solver is not None else None
            try:
                cfg_kwargs = {
                    "enable_ao": enable_ao,
                    "mesh_fluids": mesh_fluids,
                    "z_up_coordinates": True,
                    "origin_centered": True,
                    "weld_vertices": True,
                    "num_threads": num_threads,
                    "atlas": new_atlas,
                    "biome_resolver": new_biome_resolver,
                    "custom_aliases": self._custom_aliases,
                    "enable_animated_textures": anim_enabled,
                }
                if ctm_solver is not None:
                    cfg_kwargs["ctm_solver"] = ctm_solver
                new_config = mtk.MesherConfig(**cfg_kwargs)
            except Exception:
                new_config = None

            try:
                self._session.hot_reload(new_config, new_model_db)
            except Exception as e:
                logger.warning("Session hot_reload failed: %s", e)

        return True
