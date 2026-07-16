import json
import threading

from simkl_mps import trakt_sync
from simkl_mps.trakt_watcher import TraktSyncWatcher
from simkl_mps.watch_history_manager import WatchHistoryManager


def test_history_save_callback_runs_after_completed_event_is_on_disk(tmp_path, monkeypatch):
    monkeypatch.setattr(WatchHistoryManager, "_ensure_viewer_exists", lambda self: None)
    manager = WatchHistoryManager(tmp_path)
    observed = []

    def on_saved():
        observed.extend(json.loads(manager.history_file.read_text(encoding="utf-8")))

    manager.set_on_saved(on_saved)
    assert manager.add_entry(
        {
            "simkl_id": 42,
            "title": "Example",
            "type": "show",
            "season": 1,
            "episode": 2,
            "ids": {"tvdb": 123},
        }
    )

    assert observed[0]["simkl_id"] == 42
    assert observed[0]["episode"] == 2


def test_history_saved_notification_wakes_trakt_without_waiting_for_poll(monkeypatch):
    watcher = TraktSyncWatcher(poll_seconds=60, debounce_seconds=0)
    calls = 0
    startup_done = threading.Event()
    direct_done = threading.Event()

    def fake_sync_now():
        nonlocal calls
        calls += 1
        (startup_done if calls == 1 else direct_done).set()
        return trakt_sync.SyncResult(True, "ok")

    monkeypatch.setattr(watcher, "sync_now", fake_sync_now)
    watcher._thread = threading.Thread(target=watcher._watch_loop, daemon=True)
    watcher._thread.start()
    try:
        assert startup_done.wait(1)
        watcher.notify_history_saved()
        assert direct_done.wait(1)
        assert calls == 2
    finally:
        watcher.stop()


def test_build_payload_uses_exact_local_events_and_remaps_anime(monkeypatch):
    monkeypatch.setattr(
        trakt_sync,
        "_FRIBB",
        {529392: {"imdb": "tt5023666", "tvdb": 291630, "tmdb": 62430, "season": 2}},
    )
    events = [
        {
            "kind": "movie",
            "title": "Movie",
            "simkl_id": 1,
            "watched_at": "2026-07-15T18:00:00.000Z",
            "ids": {"imdb": "tt0000001", "tmdb": "10"},
            "is_anime": False,
        },
        {
            "kind": "episode",
            "title": "Show",
            "simkl_id": 2,
            "season": 3,
            "episode": 4,
            "watched_at": "2026-07-15T18:10:00.000Z",
            "ids": {"tvdb": "20"},
            "is_anime": False,
        },
        {
            "kind": "episode",
            "title": "Arslan Senki: Fuujin Ranbu",
            "simkl_id": 529392,
            "season": 1,
            "episode": 6,
            "watched_at": "2026-07-15T18:20:00.000Z",
            "ids": {"mal": "31821"},
            "is_anime": True,
        },
    ]

    payload, unresolved = trakt_sync.build_payload(events, client_id=None)

    assert unresolved == []
    assert payload["movies"] == [
        {
            "watched_at": "2026-07-15T18:00:00.000Z",
            "ids": {"imdb": "tt0000001", "tmdb": 10},
        }
    ]
    by_tvdb = {show["ids"].get("tvdb"): show for show in payload["shows"]}
    assert by_tvdb[20]["seasons"][0]["number"] == 3
    anime = by_tvdb[291630]
    assert anime["seasons"][0]["number"] == 2
    assert anime["seasons"][0]["episodes"][0]["number"] == 6


def test_collect_history_events_uses_watch_events_not_aggregate_fields():
    history = [
        {
            "type": "anime",
            "title": "Example",
            "simkl_id": 7,
            "season": 1,
            "episode": 8,
            "ids": {"imdb": "tt7"},
            "watch_events": [
                {"season": 1, "episode": 6, "watched_at": "2026-07-15T15:00:00Z"},
                {"season": 1, "episode": 7, "watched_at": "2026-07-15T16:00:00Z"},
            ],
        }
    ]

    events = trakt_sync.collect_history_events(
        history, trakt_sync.parse_dt("2026-07-15T15:30:00Z")
    )

    assert len(events) == 1
    assert events[0]["episode"] == 7


def test_unmatched_event_is_saved_and_retried_after_marker_advances(tmp_path, monkeypatch):
    config_file = tmp_path / "trakt_config.json"
    token_file = tmp_path / "trakt_token.json"
    state_file = tmp_path / "trakt_sync_state.json"
    history_file = tmp_path / "watch_history.json"
    backlog_file = tmp_path / "backlog.json"
    for name, value in (
        ("CONFIG_FILE", config_file),
        ("TOKEN_FILE", token_file),
        ("STATE_FILE", state_file),
        ("HISTORY_FILE", history_file),
        ("SIMKL_BACKLOG_FILE", backlog_file),
    ):
        monkeypatch.setattr(trakt_sync, name, value)
    monkeypatch.setattr(trakt_sync, "_FRIBB", {})
    monkeypatch.setattr(trakt_sync, "_DETAIL_CACHE", {})
    monkeypatch.setattr(trakt_sync, "get_credentials", lambda: {"client_id": None})

    config_file.write_text('{"client_id":"x","client_secret":"y"}', encoding="utf-8")
    token_file.write_text("{}", encoding="utf-8")
    state_file.write_text(
        '{"synced_through":"2026-07-15T14:00:00.000Z","pending":[]}',
        encoding="utf-8",
    )
    backlog_file.write_text("{}", encoding="utf-8")
    history_file.write_text(
        json.dumps(
            [
                {
                    "type": "anime",
                    "title": "Unmapped Anime",
                    "simkl_id": 99,
                    "season": 1,
                    "episode": 2,
                    "ids": {"mal": "99"},
                    "watch_events": [
                        {
                            "season": 1,
                            "episode": 2,
                            "watched_at": "2026-07-15T15:00:00Z",
                        }
                    ],
                }
            ]
        ),
        encoding="utf-8",
    )

    first = trakt_sync.sync_history()
    saved = json.loads(state_file.read_text(encoding="utf-8"))
    assert first.ok is False
    assert len(saved["pending"]) == 1
    assert trakt_sync.parse_dt(saved["synced_through"]) == trakt_sync.parse_dt(
        "2026-07-15T15:00:00Z"
    )

    monkeypatch.setattr(
        trakt_sync,
        "_FRIBB",
        {99: {"imdb": "tt99", "tvdb": 999, "tmdb": None, "season": 2}},
    )
    monkeypatch.setattr(trakt_sync, "trakt_token", lambda config: "token")
    pushed = {}

    def fake_push(config, token, payload, retries=3):
        pushed["payload"] = payload
        return 201, {"added": {"movies": 0, "episodes": 1}, "not_found": {}}

    monkeypatch.setattr(trakt_sync, "push_trakt", fake_push)

    second = trakt_sync.sync_history()
    saved = json.loads(state_file.read_text(encoding="utf-8"))
    assert second.ok is True
    assert pushed["payload"]["shows"][0]["seasons"][0]["number"] == 2
    assert saved["pending"] == []


def test_sync_health_records_response_and_builds_secret_safe_report(tmp_path, monkeypatch):
    config_file = tmp_path / "trakt_config.json"
    token_file = tmp_path / "trakt_token.json"
    state_file = tmp_path / "trakt_sync_state.json"
    history_file = tmp_path / "watch_history.json"
    for name, value in (
        ("CONFIG_FILE", config_file),
        ("TOKEN_FILE", token_file),
        ("STATE_FILE", state_file),
        ("HISTORY_FILE", history_file),
    ):
        monkeypatch.setattr(trakt_sync, name, value)

    config_file.write_text(
        '{"client_id":"private-client-id","client_secret":"private-secret"}',
        encoding="utf-8",
    )
    token_file.write_text(
        '{"access_token":"private-token","created_at":0,"expires_in":9999999999}',
        encoding="utf-8",
    )
    state_file.write_text(
        '{"synced_through":"2026-07-15T14:00:00.000Z","pending":[]}',
        encoding="utf-8",
    )
    history_file.write_text(
        json.dumps(
            [
                {
                    "type": "show",
                    "title": "Private Show Title",
                    "simkl_id": 42,
                    "season": 3,
                    "episode": 4,
                    "ids": {"tvdb": "12345"},
                    "watch_events": [
                        {
                            "season": 3,
                            "episode": 4,
                            "watched_at": "2026-07-15T15:00:00Z",
                        }
                    ],
                }
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(trakt_sync, "get_credentials", lambda: {"client_id": None})
    monkeypatch.setattr(trakt_sync, "trakt_token", lambda config: "private-token")
    monkeypatch.setattr(
        trakt_sync,
        "push_trakt",
        lambda config, token, payload: (
            201,
            {"added": {"movies": 0, "episodes": 1}, "not_found": {}},
        ),
    )

    result = trakt_sync.sync_history()
    state = json.loads(state_file.read_text(encoding="utf-8"))
    health = state["health"]

    assert result.ok is True
    assert health["last_ok"] is True
    assert health["last_http_status"] == 201
    assert health["last_added_episodes"] == 1
    assert health["last_pending"] == 0
    assert health["last_success_at"]

    watcher = TraktSyncWatcher()
    watcher._thread = type("Thread", (), {"is_alive": lambda self: True})()
    local_report = watcher.health_report(include_title=True)
    safe_report = watcher.health_report(include_title=False)

    assert "SIMKL\nStatus: accepted" in local_report
    assert "Private Show Title - S03E04" in local_report
    assert "TRAKT\nStatus: OK" in local_report
    assert "HTTP 201" in local_report
    assert "Private Show Title" not in safe_report
    assert "private-client-id" not in safe_report
    assert "private-secret" not in safe_report
    assert "private-token" not in safe_report
    assert "12345" not in safe_report
    assert str(tmp_path) not in safe_report
    assert "ids" not in trakt_sync.get_sync_health()["latest_event"]


def test_failed_response_is_visible_in_health_without_replacing_last_success(
    tmp_path, monkeypatch
):
    state_file = tmp_path / "trakt_sync_state.json"
    monkeypatch.setattr(trakt_sync, "STATE_FILE", state_file)
    state = {
        "synced_through": "2026-07-15T14:00:00.000Z",
        "pending": [],
        "health": {"last_success_at": "2026-07-15T14:30:00.000Z"},
    }

    trakt_sync._record_health(
        state,
        "Trakt returned HTTP 503; state was not advanced.",
        False,
        http_status=503,
        pending=1,
    )

    saved = json.loads(state_file.read_text(encoding="utf-8"))
    assert saved["synced_through"] == "2026-07-15T14:00:00.000Z"
    assert saved["health"]["last_ok"] is False
    assert saved["health"]["last_http_status"] == 503
    assert saved["health"]["last_pending"] == 1
    assert saved["health"]["last_success_at"] == "2026-07-15T14:30:00.000Z"
