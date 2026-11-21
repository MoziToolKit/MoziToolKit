"""
Network environment and proxy synchronization utilities.
"""

from __future__ import annotations

import logging
import os
import urllib.request

from ...bridge.network import (
    ONLINE_ACCESS_DENIED_MESSAGE,
    is_online_access_allowed,
    require_online_access,
)

logger = logging.getLogger("MoziToolKit.System.Network")


def sync_system_proxies() -> bool:
    """
    Syncs OS-level default proxies (e.g. macOS SystemConfiguration or Windows Registry)
    into standard process environment variables (http_proxy, https_proxy, all_proxy)
    if not already explicitly specified in the environment.

    Strictly refuses to run and returns False when Blender online access is disabled.
    """
    if not is_online_access_allowed():
        return False

    try:
        proxies = urllib.request.getproxies()
        for proto in ("http", "https", "all"):
            if proto in proxies:
                val = proxies[proto]
                if not val.startswith(("http://", "https://", "socks5://")):
                    val = f"http://{val}"
                os.environ.setdefault(f"{proto}_proxy", val)
                os.environ.setdefault(f"{proto.upper()}_PROXY", val)
        return True
    except Exception as e:
        logger.debug("Failed syncing system proxies: %s", e)
        return False


__all__ = [
    "ONLINE_ACCESS_DENIED_MESSAGE",
    "is_online_access_allowed",
    "require_online_access",
    "sync_system_proxies",
]
