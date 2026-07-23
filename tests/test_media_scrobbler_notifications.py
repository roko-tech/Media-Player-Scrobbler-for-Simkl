import importlib
import pathlib

import requests


REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
PACKAGE_ROOT = REPO_ROOT / "simkl_mps"


media_scrobbler_module = importlib.import_module("simkl_mps.media_scrobbler")
config_manager_module = importlib.import_module("simkl_mps.config_manager")
MediaScrobbler = media_scrobbler_module.MediaScrobbler


class _FailingIntegration:
    def get_position_duration(self, _process_name):
        raise requests.RequestException("connection refused")

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


def test_get_current_filepath_suppresses_connection_issue_when_tracking(
    tmp_path, monkeypatch
):
    scrobbler = _prepare_scrobbler(tmp_path, monkeypatch)
    notifications = []
    scrobbler.set_notification_callback(
        lambda title, message: notifications.append((title, message))
    )
    scrobbler.currently_tracking = "Example Movie"

    assert scrobbler.get_current_filepath("vlc.exe") is None
    assert notifications == []


def test_get_player_position_duration_suppresses_connection_issue_when_tracking(
    tmp_path, monkeypatch
):
    scrobbler = _prepare_scrobbler(tmp_path, monkeypatch)
    notifications = []
    scrobbler.set_notification_callback(
        lambda title, message: notifications.append((title, message))
    )
    scrobbler.currently_tracking = "Example Movie"

    assert scrobbler.get_player_position_duration("vlc.exe") == (None, None)
    assert notifications == []


def test_notifications_are_enabled_by_default():
    assert config_manager_module.DEFAULT_SETTINGS["disable_notifications"] is False
