"""
Network environment and proxy synchronization utilities.
"""

from __future__ import annotations

import logging
import urllib.request

from ...bridge.network import (
    ONLINE_ACCESS_DENIED_MESSAGE,
    is_online_access_allowed,
    require_online_access,
)

logger = logging.getLogger("MoziToolKit.System.Network")


def sync_system_proxies() -> bool:
    """
    Probe OS-level default proxies without mutating process-wide ``os.environ``.

    Strictly refuses to run and returns False when Blender online access is disabled.
    """
    if not is_online_access_allowed():
        return False

    try:
        urllib.request.getproxies()
        return True
    except Exception as e:
        logger.debug("Failed probing system proxies: %s", e)
        return False


__all__ = [
    "ONLINE_ACCESS_DENIED_MESSAGE",
    "is_online_access_allowed",
    "require_online_access",
    "sync_system_proxies",
]
