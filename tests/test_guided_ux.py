import importlib
import sys
from types import SimpleNamespace

from simkl_mps import simkl_api, tray_base
from simkl_mps.activity import format_delivery_activity, format_setup_health
from simkl_mps.backlog_cleaner import BacklogCleaner
from simkl_mps.media_cache import MediaCache
from simkl_mps.media_identity import cache_key_for_media
from simkl_mps.simkl_api import SearchCandidate
from simkl_mps.tray_base import TrayAppBase


class StubTray(TrayAppBase):
    def update_icon(self, status=None):
        pass

    def show_notification(self, title, message):
        pass

    def show_about(self, *_args):
        pass

    def show_help(self, *_args):
        pass

    def exit_app(self, *_args):
        pass

    def run(self):
        pass

    def _ask_custom_threshold_dialog(self, callback):
        pass

    def _ask_directory_filter_dialog(self, *_args):
        pass


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


def test_correction_search_normalizes_filters_and_deduplicates(monkeypatch):
    calls = []
    payloads = {
        "anime": [
            {
                "title": "Correct Anime",
                "year": "2024",
                "type": "tv",
                "ids": {"simkl_id": "101"},
            },
            {
                "title": "Anime Movie",
                "year": 2024,
                "type": "movie",
                "ids": {"simkl": 999},
            },
        ],
        "tv": [
            {"title": "Duplicate", "year": 2024, "ids": {"simkl": 101}},
            {"title": "Correct Show", "year": 2023, "ids": {"simkl": 202}},
        ],
    }

    def fake_get(url, **kwargs):
        endpoint = url.rsplit("/", 1)[-1]
        calls.append((endpoint, kwargs))
        return FakeResponse(payloads[endpoint])

    monkeypatch.setattr(simkl_api, "is_internet_connected", lambda: True)
    monkeypatch.setattr(simkl_api.requests, "get", fake_get)

    candidates = simkl_api.search_media_candidates(
        "correct title",
        "public-client",
        "user-token",
        media_kind="anime",
    )

    assert [(item.title, item.simkl_id, item.media_type) for item in candidates] == [
        ("Correct Anime", 101, "anime"),
        ("Correct Show", 202, "show"),
    ]
    assert [call[0] for call in calls] == ["anime", "tv"]
    for _endpoint, kwargs in calls:
        assert kwargs["params"]["client_id"] == "public-client"
        assert kwargs["params"]["app-name"] == simkl_api.APP_NAME
        assert kwargs["params"]["app-version"] == simkl_api.__version__
        assert kwargs["headers"]["User-Agent"] == simkl_api.USER_AGENT
        assert kwargs["headers"]["Authorization"] == "Bearer user-token"


def test_search_driven_correction_saves_selected_match(monkeypatch):
    saved = []
    notifications = []
    answers = iter(("wrong title", "2, 3"))
    media = SimpleNamespace(
        current_filepath=r"D:\Anime\Wrong Show\S01E04.mkv",
        movie_name="Wrong Show",
        currently_tracking="Wrong Show S01E04",
        episode=4,
        season=1,
        media_type="anime",
        client_id="client",
        access_token="token",
        media_overrides=SimpleNamespace(
            set=lambda *args, **kwargs: saved.append((args, kwargs))
        ),
    )
    app = StubTray.__new__(StubTray)
    app.scrobbler = SimpleNamespace(monitor=SimpleNamespace(scrobbler=media))
    app._ask_directory_filter_dialog = lambda *_args: next(answers)
    app.show_notification = lambda title, message: notifications.append((title, message))
    app.try_scrobble_again = lambda: 7
    monkeypatch.setattr(
        tray_base,
        "search_media_candidates",
        lambda *_args, **_kwargs: [
            SearchCandidate("First", 2020, 100, "anime"),
            SearchCandidate("Correct Show", 2024, 200, "anime"),
        ],
    )

    result = app._set_current_media_override("file")

    assert result == 7
    assert saved == [
        (
            ("file", media.current_filepath, 200),
            {
                "season": 3,
                "title": "Correct Show",
                "media_type": "anime",
            },
        )
    ]
    assert notifications[-1][0] == "Correct Match"


def test_activity_summary_uses_persisted_provider_state_without_paths():
    text = format_delivery_activity(
        {
            "title": "Current Show",
            "season": 1,
            "episode": 2,
            "progress": 42,
            "state": "playing",
            "simkl_id": 10,
        },
        [
            {
                "event_id": "12345678-aaaa",
                "title": "Completed Show",
                "season": 2,
                "episode": 3,
                "watched_at": "2026-07-23T12:00:00Z",
                "delivery_state": "pending",
                "original_filepath": r"D:\Private\Completed Show.mkv",
                "provider_outcomes": [
                    {"provider": "simkl", "status": "accepted", "retryable": False},
                    {"provider": "trakt", "status": "pending_retry", "retryable": True},
                ],
            }
        ],
        trakt_configured=True,
    )

    assert "Current Show S01E02 · 42% · Simkl 10 · Playing" in text
    assert "Simkl Accepted · Local Pending · Trakt Pending retry" in text
    assert "event 12345678" in text
    assert r"D:\Private" not in text


def test_activity_summary_exposes_accepted_event_with_pending_audit():
    text = format_delivery_activity(
        None,
        [
            {
                "event_id": "12345678-abcd",
                "title": "Example",
                "simkl_synced": True,
                "provider_outcome_pending": True,
                "provider_outcomes": [],
            }
        ],
    )

    assert "Simkl Accepted (audit pending)" in text


def test_first_run_health_is_actionable_before_authentication():
    text = format_setup_health(
        authenticated=False,
        monitoring_status="error",
        current_title=None,
        delivery_counts={"pending": 2, "failed": 1},
        trakt_configured=False,
        allow_dir_count=1,
        deny_dir_count=2,
        first_run=True,
    )

    assert text.startswith("WELCOME TO MPS FOR SIMKL")
    assert "connect Simkl from the SIMKL menu" in text
    assert "2 pending, 1 need attention" in text
    assert "play a local file to test detection" in text


def test_first_run_setup_is_shown_only_once():
    app = StubTray.__new__(StubTray)
    app.is_first_run = True
    calls = []
    completed = []
    app.show_setup_health = lambda **kwargs: calls.append(kwargs) or True
    app._mark_first_run_complete = lambda: completed.append(True) or True

    app._show_first_run_setup_if_needed()
    app._show_first_run_setup_if_needed()

    assert calls == [{"first_run": True}]
    assert completed == [True]
    assert app.is_first_run is False


def test_first_run_stays_pending_when_onboarding_dialog_fails():
    app = StubTray.__new__(StubTray)
    app.is_first_run = True
    completed = []
    app.show_setup_health = lambda **_kwargs: False
    app._mark_first_run_complete = lambda: completed.append(True) or True

    assert app._show_first_run_setup_if_needed() is False
    assert completed == []
    assert app.is_first_run is True


def test_linux_first_run_marker_is_written_only_when_completed(tmp_path):
    from simkl_mps.tray_linux import TrayAppLinux

    app = TrayAppLinux.__new__(TrayAppLinux)
    app.config_path = tmp_path / ".simkl_mps.env"
    marker = tmp_path / ".first_run_complete"

    app.check_first_run()
    assert app.is_first_run is True
    assert not marker.exists()

    assert app._mark_first_run_complete() is True
    assert marker.exists()
    app.check_first_run()
    assert app.is_first_run is False


def test_windows_first_run_registry_is_read_then_written_hermetically(monkeypatch):
    from simkl_mps import tray_win

    values = {}
    registry_exists = [False]

    class FakeKey:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    def open_key(_root, _path):
        if not registry_exists[0]:
            raise FileNotFoundError
        return FakeKey()

    def create_key(_root, _path):
        registry_exists[0] = True
        return FakeKey()

    def query_value(_key, name):
        if name not in values:
            raise FileNotFoundError
        return values[name], 4

    def set_value(_key, name, _reserved, _kind, value):
        values[name] = value

    fake_winreg = SimpleNamespace(
        HKEY_CURRENT_USER=object(),
        REG_DWORD=4,
        OpenKey=open_key,
        CreateKey=create_key,
        QueryValueEx=query_value,
        SetValueEx=set_value,
    )
    monkeypatch.setitem(sys.modules, "winreg", fake_winreg)
    monkeypatch.setattr(tray_win, "sys", SimpleNamespace(platform="win32"))
    app = tray_win.TrayAppWin.__new__(tray_win.TrayAppWin)

    app.check_first_run()
    assert app.is_first_run is True
    assert values == {}

    assert app._mark_first_run_complete() is True
    assert values == {"FirstRun": 1}
    app.check_first_run()
    assert app.is_first_run is False


def test_retry_last_scrobble_clears_canonical_path_and_title_cache(tmp_path, monkeypatch):
    current_path = tmp_path / "Series A" / "Episode.mkv"
    other_path = tmp_path / "Series B" / "Episode.mkv"
    cache = MediaCache(tmp_path)
    cache.set(cache_key_for_media(current_path), {"simkl_id": 1, "type": "show"})
    cache.set(cache_key_for_media(other_path), {"simkl_id": 2, "type": "show"})
    cache.set(
        cache_key_for_media(title="Series A S01E01"),
        {"simkl_id": 1, "type": "show"},
    )
    media = SimpleNamespace(
        currently_tracking="Series A S01E01",
        current_filepath=str(current_path),
        media_cache=cache,
        media_overrides=SimpleNamespace(find=lambda _path: None),
        simkl_id=1,
        movie_name="Series A",
        media_type="show",
        season=1,
        episode=1,
        completed=False,
        start_time=1,
        watch_time=2,
        state="playing",
        current_position_seconds=3,
        total_duration_seconds=4,
    )
    app = StubTray.__new__(StubTray)
    app._get_media_scrobbler = lambda: media
    app.show_notification = lambda *_args: None
    app.update_icon = lambda *_args: None
    monkeypatch.setattr(simkl_api, "is_internet_connected", lambda: False)

    app.try_scrobble_again()

    assert cache.get(cache_key_for_media(current_path)) is None
    assert cache.get(cache_key_for_media(title="Series A S01E01")) is None
    assert cache.get(cache_key_for_media(other_path))["simkl_id"] == 2


def test_clear_all_data_aborts_when_background_worker_is_still_alive(monkeypatch):
    app = StubTray.__new__(StubTray)
    app.scrobbler = SimpleNamespace(stop=lambda: False)
    app._show_confirmation_dialog = lambda *_args: True
    notifications = []
    exits = []
    app.show_notification = lambda *args: notifications.append(args)
    app.exit_app = lambda: exits.append(True)

    class UnexpectedManifest:
        def __init__(self, *_args, **_kwargs):
            raise AssertionError("purge started while a background worker was alive")

    monkeypatch.setattr(tray_base, "AppPathManifest", UnexpectedManifest)

    assert app.clear_all_data() == 0
    assert notifications[-1][0] == "simkl-mps Error"
    assert "No application data was removed" in notifications[-1][1]
    assert exits == []


def test_platform_tray_exit_keeps_runtime_open_when_workers_are_alive():
    platforms = (
        ("simkl_mps.tray_win", "TrayAppWin"),
        ("simkl_mps.tray_linux", "TrayAppLinux"),
        ("simkl_mps.tray_mac", "TrayAppMac"),
    )

    for module_name, class_name in platforms:
        tray_class = getattr(importlib.import_module(module_name), class_name)
        app = tray_class.__new__(tray_class)
        app.monitoring_active = True
        app.stop_monitoring = lambda: False
        stopped = []
        app.tray_icon = SimpleNamespace(stop=lambda: stopped.append(True))
        if class_name == "TrayAppWin":
            app._exit_requested = False
        elif class_name == "TrayAppLinux":
            app.using_appindicator = False

        assert app.exit_app() is False
        assert stopped == []
        if class_name == "TrayAppWin":
            assert app._exit_requested is False


def test_failed_tray_start_preserves_active_state_when_cleanup_times_out():
    stop_calls = []
    media_scrobbler = SimpleNamespace(
        set_notification_callback=lambda _callback: None
    )
    runtime = SimpleNamespace(
        monitor=SimpleNamespace(running=False, scrobbler=media_scrobbler),
        trakt_watcher=None,
        start=lambda: False,
        stop=lambda: stop_calls.append(True) or False,
    )
    app = StubTray.__new__(StubTray)
    app.scrobbler = runtime
    app.monitoring_active = False
    app.is_first_run = False
    app.update_status = lambda *_args: None
    app.show_notification = lambda *_args: None

    assert app.start_monitoring() is False
    assert stop_calls == [True]
    assert app.monitoring_active is True


def test_tray_ui_error_after_start_stops_the_new_runtime():
    stop_calls = []
    media_scrobbler = SimpleNamespace(
        set_notification_callback=lambda _callback: None
    )
    runtime = SimpleNamespace(
        monitor=SimpleNamespace(running=False, scrobbler=media_scrobbler),
        trakt_watcher=None,
        start=lambda: True,
        stop=lambda: stop_calls.append(True) or True,
    )
    app = StubTray.__new__(StubTray)
    app.scrobbler = runtime
    app.monitoring_active = False
    app.is_first_run = False
    app.show_notification = lambda *_args: None

    def update_status(status, *_args):
        if status == "running":
            raise RuntimeError("tray refresh failed")

    app.update_status = update_status

    assert app.start_monitoring() is False
    assert stop_calls == [True]
    assert app.monitoring_active is False


def test_delivery_health_counts_all_persisted_states(tmp_path):
    ledger = BacklogCleaner(tmp_path)
    delivered = ledger.add(1, "Delivered", unique_event=True)
    pending = ledger.add(2, "Pending", unique_event=True)
    failed = ledger.add(3, "Failed", unique_event=True)
    assert ledger.remove(delivered)
    assert ledger.fail(failed, "permanent rejection")

    assert ledger.delivery_counts() == {
        "pending": 1,
        "delivered": 1,
        "failed": 1,
    }
    assert ledger.get_event(pending)["delivery_state"] == "pending"
