"""
Central Blender online-access permission gate for MoziToolKit.

Enforces Blender's ``bpy.app.online_access`` preference across all outbound and
socket-binding operations (Live Sync client/server, system proxy inspection, and
online player head skin downloads).
"""

from __future__ import annotations

import bpy

ONLINE_ACCESS_DENIED_MESSAGE = (
    "Network access is disabled in Blender. "
    "Enable 'Allow Online Access' in Preferences > System > Network."
)


def is_online_access_allowed() -> bool:
    """Return True iff Blender currently permits online/network access (fail-closed)."""
    app = getattr(bpy, "app", None)
    if app is None:
        return False
    return bool(getattr(app, "online_access", False))


def require_online_access(action: str = "network operations") -> None:
    """Raise PermissionError when Blender online/network access is disabled."""
    if not is_online_access_allowed():
        raise PermissionError(f"Cannot perform {action}: {ONLINE_ACCESS_DENIED_MESSAGE}")


__all__ = [
    "ONLINE_ACCESS_DENIED_MESSAGE",
    "is_online_access_allowed",
    "require_online_access",
]
