"""
MoziToolKit Live Sync Relay & Mock Server Bridge.

Wraps native `libmtk_py.LiveSyncServer` for headless relay streaming,
mock synchronization, and local testing directly inside Blender.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from ..engine import require_libmtk

logger = logging.getLogger("MoziToolKit.Bridge.Sync.Server")


class SyncBridgeServer:
    """Wrapper around native `libmtk_py.LiveSyncServer`."""

    def __init__(
        self,
        bind_address: str = "127.0.0.1:8765",
        auth_token: Optional[str] = None,
        allow_loopback: bool = True,
        server_uuid: Optional[str] = None,
    ) -> None:
        mtk = require_libmtk()
        self._server = mtk.LiveSyncServer(
            bind_address=bind_address,
            auth_token=auth_token,
            allow_loopback_without_token=allow_loopback,
            server_uuid=server_uuid,
        )

    def stop(self) -> None:
        """Stops the embedded WebSocket server and shuts down worker threads."""
        if self._server is not None:
            self._server.stop()

    def is_running(self) -> bool:
        """Returns True if the server worker is actively listening and serving."""
        return self._server.is_running() if self._server is not None else False

    def poll_events(self) -> list[dict[str, Any]]:
        """Polls pending server events (ClientConnected, HandshakeCompleted, etc.)."""
        return self._server.poll_events() if self._server is not None else []

    @property
    def native_server(self) -> Any:
        """Returns the underlying PyLiveSyncServer handle."""
        return self._server
