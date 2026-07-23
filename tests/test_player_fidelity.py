import time
from types import SimpleNamespace

import pytest
import requests

import simkl_mps.players.mpv as mpv_module
import simkl_mps.players.potplayer as potplayer_module
from simkl_mps.media_scrobbler import MediaScrobbler
from simkl_mps.players.mpc import MPCIntegration
from simkl_mps.players.mpcqt import MPCQTIntegration
from simkl_mps.players.mpv import MPVIntegration
from simkl_mps.players.vlc import VLCIntegration
from simkl_mps.utils.constants import PAUSED, PLAYING


class _CoalescedSocket:
    def __init__(self, payload):
        self.payload = payload
        self.reads = 0

    def setblocking(self, value):
        return None

    def recv(self, size):
        self.reads += 1
        payload, self.payload = self.payload, b""
        return payload


def test_mpv_preserves_coalesced_responses(monkeypatch):
    socket = _CoalescedSocket(
        b'{"request_id":1,"data":10}\n{"request_id":2,"data":20}\n'
    )
    integration = MPVIntegration.__new__(MPVIntegration)
    integration.connection = socket
    integration._receive_buffer = b""
    monkeypatch.setattr(
        mpv_module,
        "select",
        SimpleNamespace(select=lambda *args: ([socket], [], [])),
        raising=False,
    )

    first = integration._receive_response_posix(timeout=0.1)
    second = integration._receive_response_posix(timeout=0.1)

    assert first["request_id"] == 1
    assert second["request_id"] == 2
    assert socket.reads == 1


@pytest.mark.parametrize("integration_class", [MPCIntegration, MPCQTIntegration])
def test_http_player_tries_later_port_after_connection_failure(integration_class):
    integration = integration_class.__new__(integration_class)
    integration.working_port = None
    integration.default_ports = [1001, 1002]

    def get_vars(port):
        if port == 1001:
            raise requests.RequestException("closed")
        return {"position": "1000", "duration": "2000"}

    integration.get_vars = get_vars

    assert integration.get_position_duration() == (1.0, 2.0)


def test_vlc_tries_later_configuration_after_connection_failure():
    integration = VLCIntegration.__new__(VLCIntegration)
    integration.last_successful_config = None
    integration.vlc_config = {"port": 9999, "password": "configured"}
    calls = []

    def try_config(config):
        calls.append(config["port"])
        if len(calls) == 1:
            raise requests.RequestException("closed")
        return 10, 20

    integration._try_vlc_config = try_config

    assert integration.get_position_duration() == (10, 20)
    assert calls[:2] == [9999, 8080]


def test_snapshot_pause_state_is_authoritative():
    class Integration:
        def get_current_filepath(self, process_name):
            return r"D:\Media\Example.mkv"

        def get_position_duration(self, process_name):
            return 10, 100

        def is_paused(self):
            return True

    integration = Integration()
    scrobbler = MediaScrobbler.__new__(MediaScrobbler)
    scrobbler._last_connection_error_log = {}
    scrobbler._get_player_integration = lambda process: integration

    snapshot = scrobbler.get_player_snapshot("potplayer.exe")
    assert snapshot.playback_state == PAUSED

    scrobbler.currently_tracking = "Example"
    scrobbler.current_filepath = snapshot.filepath
    scrobbler.last_update_time = time.time()
    scrobbler.movie_name = "Example"
    scrobbler.total_duration_seconds = 100
    scrobbler.estimated_duration = 100
    scrobbler.state = PLAYING
    scrobbler.previous_state = PLAYING
    scrobbler.current_position_seconds = 0
    scrobbler.watch_time = 0
    scrobbler.simkl_id = 1
    scrobbler.media_type = "movie"
    scrobbler.season = None
    scrobbler.episode = None
    scrobbler.last_scrobble_time = time.time()
    scrobbler.last_progress_check = time.time()
    scrobbler.completed = False
    scrobbler._log_playback_event = lambda *args, **kwargs: None
    scrobbler._detect_pause = lambda window: (_ for _ in ()).throw(
        AssertionError("title pause fallback should not run")
    )

    scrobbler._update_tracking(
        {"process_name": "potplayer.exe"},
        player_snapshot=snapshot,
    )

    assert scrobbler.state == PAUSED


def test_potplayer_polling_uses_read_only_opcodes(monkeypatch):
    calls = []
    responses = {
        0x5003: 120_000,
        0x5004: 30_000,
        0x5006: 2,
    }

    class Win32Gui:
        @staticmethod
        def SendMessage(hwnd, message, opcode, value):
            calls.append((hwnd, message, opcode, value))
            return responses[opcode]

    monkeypatch.setattr(potplayer_module, "win32gui", Win32Gui)
    monkeypatch.setattr(
        potplayer_module,
        "win32con",
        SimpleNamespace(WM_USER=0x0400),
    )
    monkeypatch.setattr(potplayer_module, "find_potplayer_hwnd", lambda: 123)

    integration = potplayer_module.PotPlayerIntegration()

    assert potplayer_module.get_playback_ms(123) == 30_000
    assert potplayer_module.get_total_ms(123) == 120_000
    assert integration.is_paused() is False
    assert [call[2] for call in calls] == [0x5004, 0x5003, 0x5006]
    assert all(call[2] != 0x5001 for call in calls)
