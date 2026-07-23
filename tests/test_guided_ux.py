from types import SimpleNamespace

from simkl_mps import simkl_api, tray_base
from simkl_mps.activity import format_delivery_activity, format_setup_health
from simkl_mps.backlog_cleaner import BacklogCleaner
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
    app.show_setup_health = lambda **kwargs: calls.append(kwargs) or 0

    app._show_first_run_setup_if_needed()
    app._show_first_run_setup_if_needed()

    assert calls == [{"first_run": True}]
    assert app.is_first_run is False


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
