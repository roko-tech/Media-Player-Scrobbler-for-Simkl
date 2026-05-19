import importlib
import pathlib
import sys
import types

import requests


REPO_ROOT = pathlib.Path(__file__).resolve().parent
PACKAGE_ROOT = REPO_ROOT / "simkl_mps"


if "simkl_mps" not in sys.modules:
    package = types.ModuleType("simkl_mps")
    package.__path__ = [str(PACKAGE_ROOT)]
    sys.modules["simkl_mps"] = package

media_scrobbler_module = importlib.import_module("simkl_mps.media_scrobbler")
MediaScrobbler = media_scrobbler_module.MediaScrobbler


class _FailingIntegration:
    def get_current_filepath(self, _process_name):
        raise requests.RequestException("connection refused")


def _prepare_scrobbler(tmp_path, monkeypatch):
    scrobbler = MediaScrobbler(app_data_dir=tmp_path, testing_mode=True)
    failing_integration = _FailingIntegration()
    monkeypatch.setattr(
        scrobbler,
        "_get_player_integration",
        lambda _name: failing_integration,
    )
    monkeypatch.setattr(scrobbler, "_get_player_type", lambda _name: "VLC")
    monkeypatch.setattr(
        scrobbler,
        "_get_player_config_instructions",
        lambda _ptype: "Enable web interface",
    )
    monkeypatch.setattr(
        media_scrobbler_module, "is_internet_connected", lambda: True
    )
    monkeypatch.setattr(media_scrobbler_module.time, "time", lambda: 1000)
    return scrobbler


def test_suppresses_connection_notification_when_not_tracking(
    tmp_path, monkeypatch
):
    scrobbler = _prepare_scrobbler(tmp_path, monkeypatch)
    notifications = []
    scrobbler.set_notification_callback(
        lambda title, message: notifications.append((title, message))
    )
    scrobbler.currently_tracking = None

    assert scrobbler.get_current_filepath("vlc.exe") is None
    assert notifications == []


def test_get_current_filepath_notifies_connection_issue_when_tracking(
    tmp_path, monkeypatch
):
    scrobbler = _prepare_scrobbler(tmp_path, monkeypatch)
    notifications = []
    scrobbler.set_notification_callback(
        lambda title, message: notifications.append((title, message))
    )
    scrobbler.currently_tracking = "Example Movie"

    assert scrobbler.get_current_filepath("vlc.exe") is None
    assert len(notifications) == 1
    assert notifications[0][0] == "VLC Connection Error"
    assert "Could not connect to VLC web interface" in notifications[0][1]
