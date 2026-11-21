"""
Shared panel widgets used across the 3D Viewport, Object Properties, and
Add-on Preferences UI.
"""

from __future__ import annotations

from typing import Optional

from ..bridge.network import is_online_access_allowed
from ..i18n import tr
from ..utils.assets import has_valid_vanilla_pack
from ..utils.system import get_prefs

DEFAULT_PACK_HINT = "Configure a Vanilla Client JAR in Preferences to enable save refresh."
DEFAULT_ONLINE_ACCESS_HINT = (
    "Enable 'Allow Online Access' in Preferences > System > Network to authorize network features."
)


def draw_vanilla_pack_warning(
    layout,
    prefs=None,
    message: Optional[str] = None,
    separator: bool = True,
) -> bool:
    """Draws the "Vanilla Client JAR required" callout when the gate is closed.

    Returns True when the warning was drawn so callers can avoid duplicating it.
    """
    if prefs is None:
        prefs = get_prefs()
    if has_valid_vanilla_pack(prefs):
        return False

    box = layout.box()
    box.alert = True
    box.label(text=tr("Vanilla Client JAR Required"), icon="ERROR")
    column = box.column(align=True)
    column.scale_y = 0.85
    column.label(text=tr(message or DEFAULT_PACK_HINT))
    row = box.row(align=True)
    row.scale_y = 1.1
    op = row.operator(
        "mozi.open_preferences",
        text=tr("Configure Resource Packs..."),
        icon="PREFERENCES",
    )
    op.tab = "RESOURCE_PACKS"
    if separator:
        layout.separator()
    return True


def draw_online_access_warning(
    layout,
    message: Optional[str] = None,
    *,
    alert: bool = True,
    show_button: bool = True,
    separator: bool = False,
) -> bool:
    """Draws a network-authorization notice when Blender online access is disabled."""
    if is_online_access_allowed():
        return False

    box = layout.box()
    box.alert = alert
    box.label(text=tr("Network Access Disabled"), icon="ERROR" if alert else "INFO")
    column = box.column(align=True)
    column.scale_y = 0.85
    column.label(text=tr(message or DEFAULT_ONLINE_ACCESS_HINT))
    if show_button:
        row = box.row(align=True)
        row.scale_y = 1.1
        op = row.operator(
            "screen.userpref_show",
            text=tr("Open Network Settings..."),
            icon="PREFERENCES",
        )
        op.section = "SYSTEM"
    if separator:
        layout.separator()
    return True


__all__ = [
    "draw_vanilla_pack_warning",
    "draw_online_access_warning",
    "DEFAULT_PACK_HINT",
    "DEFAULT_ONLINE_ACCESS_HINT",
]
