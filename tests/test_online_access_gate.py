"""Tests for the centralized bpy.app.online_access networking permission gate."""

from __future__ import annotations

import contextlib
import types
import urllib.request
from unittest.mock import patch

import bpy

from MoziToolKit.bridge.engine import get_libmtk
from MoziToolKit.bridge.network import (
    ONLINE_ACCESS_DENIED_MESSAGE,
    is_online_access_allowed,
    require_online_access,
)
from MoziToolKit.bridge.sync import (
    SyncBridgeServer,
    SyncBridgeSession,
    discover_local_sync_instances,
)
from MoziToolKit.bridge.world.mesher import build_mesher_config
from MoziToolKit.i18n import tr
from MoziToolKit.operators.sync.op_sync_connect import (
    MOZI_OT_sync_connect,
    MOZI_OT_sync_refresh,
)
from MoziToolKit.operators.sync.properties import (
    get_active_sync_container,
    set_active_sync_container,
)
from MoziToolKit.operators.sync.sync_pump import _sync_timer_tick
from MoziToolKit.ui.common import draw_online_access_warning
from MoziToolKit.utils.system.network import sync_system_proxies


class _RecordingLayout:
    def __init__(self, calls=None):
        self.calls = calls if calls is not None else []
        self.alert = False

    def _child(self):
        return _RecordingLayout(self.calls)

    def box(self):
        b = self._child()
        self.calls.append(("box", b))
        return b

    def row(self, align=False):
        return self._child()

    def column(self, align=False):
        return self._child()

    def label(self, text="", icon="NONE"):
        self.calls.append(("label", text, icon))

    def operator(self, idname, text="", icon="NONE"):
        op = types.SimpleNamespace()
        self.calls.append(("operator", idname, text, icon, op))
        return op

    def separator(self, factor=1.0):
        self.calls.append(("separator", factor))


@contextlib.contextmanager
def _online_access_state(enabled: bool):
    system_prefs = bpy.context.preferences.system
    prev = bool(system_prefs.use_online_access)
    system_prefs.use_online_access = bool(enabled)
    try:
        yield
    finally:
        system_prefs.use_online_access = prev


def test_is_online_access_allowed_tracks_blender_setting():
    with _online_access_state(False):
        assert is_online_access_allowed() is False
        assert bool(bpy.app.online_access) is False
        try:
            require_online_access("test action")
        except PermissionError as exc:
            assert ONLINE_ACCESS_DENIED_MESSAGE in str(exc)
        else:
            raise AssertionError("Expected PermissionError when online access is disabled")

    with _online_access_state(True):
        assert is_online_access_allowed() is True
        assert bool(bpy.app.online_access) is True
        require_online_access("test action")


def test_sync_system_proxies_refused_when_offline():
    with _online_access_state(False):
        with patch.object(
            urllib.request,
            "getproxies",
            side_effect=AssertionError("getproxies() must not be called offline"),
        ):
            assert sync_system_proxies() is False

    with _online_access_state(True):
        with patch.object(urllib.request, "getproxies", return_value={}) as mock_get:
            assert sync_system_proxies() is True
            mock_get.assert_called_once()


def test_sync_bridge_server_refused_when_offline():
    with _online_access_state(False):
        try:
            SyncBridgeServer(bind_address="127.0.0.1:0")
        except PermissionError as exc:
            assert ONLINE_ACCESS_DENIED_MESSAGE in str(exc)
        else:
            raise AssertionError("Expected PermissionError for SyncBridgeServer while offline")


def test_sync_bridge_session_start_and_cached_start_refused_when_offline():
    session = SyncBridgeSession()
    with _online_access_state(False):
        assert session.start(url="ws://127.0.0.1:21924") is False
        assert session.last_error == ONLINE_ACCESS_DENIED_MESSAGE

        assert session.start_with_cache(url="ws://127.0.0.1:21924") is False
        assert session.last_error == ONLINE_ACCESS_DENIED_MESSAGE


def test_local_ipc_sync_allowed_and_discoverable_when_offline():
    with _online_access_state(False):
        server = SyncBridgeServer(bind_address=None, enable_ipc=True)
        try:
            ipc_url = server.ipc_url
            assert ipc_url and ipc_url.startswith("ipc://")
            instances = discover_local_sync_instances()
            assert any(inst.get("ipc_url") == ipc_url for inst in instances)

            session = SyncBridgeSession()
            assert session.start(url=ipc_url, auto_reconnect=False) is True
            session.stop()
        finally:
            server.stop()


def test_sync_connect_and_refresh_operators_refused_when_offline():
    scene = bpy.context.scene
    prev_mode = scene.mozi_sync.sync_mode
    scene.mozi_sync.sync_mode = "REMOTE_WS"
    try:
        with _online_access_state(False):
            assert MOZI_OT_sync_connect.poll(bpy.context) is False
            assert MOZI_OT_sync_refresh.poll(bpy.context) is False

            connect_reports = []
            mock_connect_op = types.SimpleNamespace(
                target_container="",
                report=lambda level, msg: connect_reports.append((set(level), msg)),
            )
            res_connect = MOZI_OT_sync_connect.execute(mock_connect_op, bpy.context)
            assert res_connect == {"CANCELLED"}
            assert any(
                "ERROR" in lvl and tr(ONLINE_ACCESS_DENIED_MESSAGE) in msg
                for lvl, msg in connect_reports
            )

            refresh_reports = []
            mock_refresh_op = types.SimpleNamespace(
                target_container="",
                report=lambda level, msg: refresh_reports.append((set(level), msg)),
            )
            res_refresh = MOZI_OT_sync_refresh.execute(mock_refresh_op, bpy.context)
            assert res_refresh == {"CANCELLED"}
            assert any(
                "ERROR" in lvl and tr(ONLINE_ACCESS_DENIED_MESSAGE) in msg
                for lvl, msg in refresh_reports
            )
    finally:
        scene.mozi_sync.sync_mode = prev_mode


def test_build_mesher_config_tracks_online_access():
    mtk = get_libmtk()
    assert mtk is not None

    with _online_access_state(False):
        cfg_off = build_mesher_config(mtk)
        assert cfg_off.allow_network is False

    with _online_access_state(True):
        cfg_on = build_mesher_config(mtk)
        assert cfg_on.allow_network is True


def test_sync_timer_tick_stops_active_session_when_toggled_offline():
    scene = bpy.context.scene
    prev_active = get_active_sync_container(scene)
    container = bpy.data.objects.new("test_online_gate_sync", None)
    scene.collection.objects.link(container)
    container["mtk:is_yefira_world"] = True
    container.mozi_sync.sync_mode = "REMOTE_WS"
    container.mozi_sync.is_connected = True
    container.mozi_sync.is_streaming = True
    container.mozi_sync.connection_status = "CONNECTED"
    set_active_sync_container(scene, container)

    stopped = []

    class _ActiveSessionStub:
        is_active = True
        current_url = "ws://127.0.0.1:21924"

        def stop(self):
            stopped.append(True)
            self.is_active = False

    session_stub = _ActiveSessionStub()

    try:
        with _online_access_state(False):
            with patch(
                "MoziToolKit.operators.sync.op_sync_connect.get_sync_bridge_session",
                return_value=session_stub,
            ):
                ret = _sync_timer_tick()

        assert ret is None
        assert stopped == [True]
        assert container.mozi_sync.is_connected is False
        assert container.mozi_sync.is_streaming is False
        assert container.mozi_sync.connection_status == "DISCONNECTED"
        assert container.mozi_sync.validation_info == tr("Network Access Disabled")
    finally:
        set_active_sync_container(scene, prev_active)
        if container.name in bpy.data.objects:
            bpy.data.objects.remove(container, do_unlink=True)


def test_draw_online_access_warning_renders_only_when_offline():
    with _online_access_state(False):
        layout = _RecordingLayout()
        shown = draw_online_access_warning(layout)
        assert shown is True
        ops = [c for c in layout.calls if c[0] == "operator"]
        assert len(ops) == 1
        assert ops[0][1] == "screen.userpref_show"
        assert ops[0][4].section == "SYSTEM"

    with _online_access_state(True):
        layout = _RecordingLayout()
        shown = draw_online_access_warning(layout)
        assert shown is False
        assert layout.calls == []
