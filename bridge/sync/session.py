"""
MoziToolKit Live Sync Session Wrapper.

Encapsulates all interaction with native libmtk LiveSyncSession and
VoxelStorage engines. Provides clean data-in / data-out APIs for Blender
operators and panels.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from .lifecycle import SyncLifecycleMixin

logger = logging.getLogger("MoziToolKit.Bridge.Sync.Session")


class SyncBridgeSession(SyncLifecycleMixin):
    """
    Session wrapper managing the native background LiveSyncSession client thread
    and event queue.
    """

    def __init__(self):
        self._session: Optional[Any] = None
        self._is_active: bool = False
        self._is_connected: bool = False
        self._connection_status: str = "DISCONNECTED"
        self._current_url: str = ""
        self._model_db: Optional[Any] = None
        self._atlas: Optional[Any] = None
        self._biome_resolver: Optional[Any] = None
        self._colormaps: Optional[Any] = None
        self._custom_aliases: Optional[Any] = None
        self._config_params: Dict[str, Any] = {}
        self._cached_manifest_fingerprint: Optional[str] = None
        self._cached_manifest_timestamp: float = 0.0
        self._last_error: str = ""
        self._skin_cache_dir: Optional[str] = None

    @property
    def is_active(self) -> bool:
        """Whether a background synchronization session is running."""
        if not self._is_active:
            return False
        if self._session is not None:
            return bool(self._session.is_active)
        return False

    @property
    def is_connected(self) -> bool:
        """Whether the session is currently connected to the server."""
        if not self.is_active or self._session is None:
            return False
        return bool(self._session.is_connected)

    @property
    def connection_status(self) -> str:
        """Current connection status string ('DISCONNECTED', 'CONNECTING...', 'CONNECTED', etc.)."""
        if not self.is_active or self._session is None:
            return "DISCONNECTED"
        return str(self._session.status)

    @property
    def current_url(self) -> str:
        """Currently connected or target WebSocket URL."""
        return self._current_url

    @property
    def last_error(self) -> str:
        """Last error message recorded during session operations."""
        return self._last_error

    @property
    def atlas(self) -> Optional[Any]:
        """Currently loaded BakedAtlas in session memory."""
        return self._atlas

    @property
    def model_db(self) -> Optional[Any]:
        """Currently loaded BakedModelDatabase in session memory."""
        return self._model_db

    @property
    def biome_resolver(self) -> Optional[Any]:
        """Currently loaded BiomeResolver in session memory."""
        return self._biome_resolver

    @property
    def manifest_fingerprint(self) -> Optional[str]:
        """Fingerprint of the active asset cache package associated with this session."""
        return self._cached_manifest_fingerprint

    @property
    def client_generation(self) -> int:
        """Current client generation counter from native session."""
        if self._session is not None:
            return int(self._session.client_generation)
        return 0

    @property
    def current_sequence(self) -> int:
        """Current monotonic sequence ID from native session."""
        if self._session is not None:
            return int(self._session.current_sequence)
        return 0

    @property
    def current_epoch(self) -> int:
        """Current server epoch counter from native session."""
        if self._session is not None:
            return int(self._session.current_epoch)
        return 0

    @property
    def state_hash(self) -> int:
        """Server-confirmed canonical state hash from native session."""
        if self._session is not None:
            return int(self._session.state_hash)
        return 0

    @property
    def server_uuid(self) -> str:
        """Server UUID in hex format."""
        if self._session is not None:
            return str(self._session.server_uuid)
        return ""

    def load_from_point_cloud(self, point_cloud: Any) -> bool:
        """Restores voxel storage in the native session from an existing point cloud without re-meshing."""
        if self._session is not None:
            try:
                self._session.load_from_point_cloud(point_cloud)
                return True
            except Exception as e:
                logger.error("Failed loading from point cloud: %s", e)
        return False

    def stop(self, keep_session: bool = True) -> None:
        """Cleanly stops background sync client and pauses networking."""
        if self._session is not None:
            try:
                self._session.stop()
            except Exception as e:
                logger.debug("Error during LiveSyncSession shutdown: %s", e)
        if not keep_session:
            self._session = None
        self._is_active = False
        self._is_connected = False
        self._connection_status = "DISCONNECTED"
        logger.info("LiveSyncSession stopped.")

    def reset(self) -> None:
        """
        Stops the session and thoroughly frees references (model_db, atlas, errors),
        preventing memory leaks or stale scene references across file operations.
        """
        self.stop(keep_session=False)
        self._current_url = ""
        self._model_db = None
        self._atlas = None
        self._biome_resolver = None
        self._custom_aliases = None
        self._config_params = {}
        self._cached_manifest_fingerprint = None
        self._cached_manifest_timestamp = 0.0
        self._last_error = ""
        for attr in ("get_world_mesh", "get_storage", "poll_events"):
            if attr in self.__dict__:
                del self.__dict__[attr]

    def clear_cache(self) -> bool:
        """Purges cached section meshes on the underlying VoxelWorld, marking all sections dirty."""
        if self._session is not None:
            try:
                self._session.clear_cache()
                return True
            except Exception as e:
                logger.warning("Failed clearing native session mesh cache: %s", e)
        return False

    def poll_events(self) -> List[Dict[str, Any]]:
        """
        Polls all pending events from the background Rust engine (non-blocking).
        Updates internal connection status accordingly.
        """
        if self._session is None:
            return []
        try:
            try:
                events = self._session.poll_events(coalesce_meshes=True)
            except TypeError:
                events = self._session.poll_events()
            for ev in events:
                if ev.get("type") == "STATUS_CHANGE":
                    status = ev.get("status", "DISCONNECTED")
                    self._connection_status = status
                    if status == "CONNECTED":
                        self._is_connected = True
                    elif status.startswith("DISCONNECTED"):
                        self._is_connected = False
                        self._is_active = False
                    else:
                        self._is_connected = False
            return events
        except Exception as e:
            logger.error("Error polling Live Sync events: %s", e)
            return []

    def get_world_mesh(self) -> Optional[Any]:
        """Queries the current full merged world geometry directly as a PyMeshData buffer."""
        if self._session is None:
            return None
        try:
            return self._session.get_world_mesh()
        except Exception as e:
            logger.error("Error querying get_world_mesh: %s", e)
            return None

    def get_storage(self) -> Optional[Any]:
        """Returns a copy of the underlying PyVoxelStorage from the active live sync session."""
        if self._session is None:
            return None
        try:
            return self._session.get_storage()
        except Exception as e:
            logger.error("Error querying get_storage: %s", e)
            return None

    def request_resync(self) -> bool:
        """Sends RESYNC_REQUEST (opcode 0x0D) to request state recovery."""
        if self._session is None:
            return False
        try:
            self._session.request_resync()
            return True
        except Exception as e:
            logger.error("Failed to send Resync Request: %s", e)
            return False

    def get_dynamic_entity_atlases(self) -> Optional[dict[int, bytes]]:
        """Returns the `{chunk_id: RGBA bytes}` dynamic entity atlas map of the live session."""
        if self._session is None:
            return None
        try:
            atlases = self._session.get_dynamic_entity_atlases()
            if atlases:
                return atlases
        except Exception as e:
            logger.debug("Failed getting dynamic entity atlases from session: %s", e)
        return None

    def set_skin_cache_dir(self, cache_dir: Optional[str]) -> None:
        """Sets the player head skin disk cache path on the native session."""
        self._skin_cache_dir = cache_dir
        if self._session is not None:
            try:
                self._session.set_skin_cache_dir(cache_dir)
            except Exception as e:
                logger.debug("Failed setting skin cache dir on session: %s", e)
