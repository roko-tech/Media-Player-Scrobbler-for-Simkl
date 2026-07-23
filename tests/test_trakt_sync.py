import json
import threading

from simkl_mps import trakt_sync
from simkl_mps.backlog_cleaner import BacklogCleaner
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


def test_partial_not_found_retries_only_echoed_event():
    events = [
        {
            "kind": "episode",
            "simkl_id": 1,
            "season": 1,
            "episode": 1,
            "watched_at": "2026-07-15T18:00:00.000Z",
        },
        {
            "kind": "episode",
            "simkl_id": 1,
            "season": 1,
            "episode": 2,
            "watched_at": "2026-07-15T18:30:00.000Z",
        },
    ]
    not_found = {
        "shows": [
            {
                "seasons": [
                    {
                        "number": 1,
                        "episodes": [
                            {
                                "number": 2,
                                "watched_at": "2026-07-15T18:30:00.000Z",
                            }
                        ],
                    }
                ]
            }
        ]
    }

    retry = trakt_sync._not_found_events(events, not_found)

    assert retry == [events[1]]


def test_push_trakt_exposes_retry_after_without_immediate_loop(monkeypatch):
    calls = []

    class Response:
        status_code = 429
        headers = {"Retry-After": "300", "X-Ratelimit": '{"remaining":0}'}

        @staticmethod
        def json():
            return {"error": "rate limited"}

    def fake_post(*args, **kwargs):
        calls.append((args, kwargs))
        return Response()

    monkeypatch.setattr(trakt_sync.requests, "post", fake_post)

    status, body, retry_after = trakt_sync.push_trakt(
        {"client_id": "client"}, "token", {"movies": [], "shows": []}
    )

    assert status == 429
    assert body == {"error": "rate limited"}
    assert retry_after == 300
    assert len(calls) == 1


def test_watcher_uses_provider_retry_after():
    watcher = TraktSyncWatcher(retry_seconds=120)

    delay = watcher._retry_delay(
        trakt_sync.SyncResult(False, "rate limited", retry_after=300)
    )

    assert delay == 300


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


def test_dismiss_pending_events_prevents_same_history_event_from_returning(tmp_path, monkeypatch):
    state_file = tmp_path / "trakt_sync_state.json"
    history_file = tmp_path / "watch_history.json"
    pending_event = {
        "kind": "episode",
        "title": "Wrong Match",
        "simkl_id": 99,
        "season": 1,
        "episode": 1,
        "watched_at": "2026-07-23T02:56:12.725Z",
        "ids": {},
        "is_anime": True,
    }
    state_file.write_text(
        json.dumps(
            {
                "synced_through": "2026-07-23T02:56:12.725Z",
                "pending": [pending_event],
            }
        ),
        encoding="utf-8",
    )
    history_file.write_text(
        json.dumps(
            [
                {
                    "type": "anime",
                    "title": "Wrong Match",
                    "simkl_id": 99,
                    "season": 1,
                    "episode": 1,
                    "watch_events": [
                        {
                            "season": 1,
                            "episode": 1,
                            "watched_at": "2026-07-23T02:56:12.725794Z",
                        }
                    ],
                }
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(trakt_sync, "STATE_FILE", state_file)
    monkeypatch.setattr(trakt_sync, "HISTORY_FILE", history_file)

    dismissed = trakt_sync.dismiss_pending_events()
    saved = json.loads(state_file.read_text(encoding="utf-8"))

    assert dismissed == 1
    assert saved["pending"] == []
    assert saved["dismissed_event_keys"] == [trakt_sync._event_key(pending_event)]

    result = trakt_sync.sync_history()

    assert result.ok is True
    assert result.pending == 0
    assert "nothing new" in result.summary


def test_trakt_state_recovers_backup_and_preserves_corrupt_primary(
    tmp_path, monkeypatch
):
    state_file = tmp_path / "trakt_sync_state.json"
    backup_file = tmp_path / "trakt_sync_state.json.bak"
    expected = {
        "synced_through": "2026-07-15T14:00:00.000Z",
        "pending": [{"kind": "movie", "watched_at": "2026-07-15T15:00:00.000Z"}],
    }
    state_file.write_text("{broken", encoding="utf-8")
    backup_file.write_text(json.dumps(expected), encoding="utf-8")
    monkeypatch.setattr(trakt_sync, "STATE_FILE", state_file)

    recovered = trakt_sync.load_state({})

    assert recovered == expected
    assert json.loads(state_file.read_text(encoding="utf-8")) == expected
    assert list(tmp_path.glob("trakt_sync_state.json.corrupt-*"))


def test_sync_health_records_response_and_builds_secret_safe_report(tmp_path, monkeypatch):
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

    backlog_file.write_text("{}", encoding="utf-8")
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


def test_fribb_map_reloads_when_cache_appears(tmp_path, monkeypatch):
    cache = tmp_path / "anime-list-full.json"
    monkeypatch.setattr(trakt_sync, "FRIBB_FILE", cache)
    monkeypatch.setattr(trakt_sync, "_FRIBB", None)

    assert trakt_sync._fribb_map() == {}

    cache.write_text(
        '[{"simkl_id":529392,"tvdb_id":291630,"season":{"tvdb":2}}]',
        encoding="utf-8",
    )

    assert trakt_sync._fribb_map()[529392]["season"] == 2


def test_watcher_retries_failed_sync_without_another_history_change(monkeypatch):
    watcher = TraktSyncWatcher(poll_seconds=0.01, debounce_seconds=0.01, retry_seconds=0.03)
    calls = []
    receipts = []

    def fake_sync_now():
        calls.append(len(calls))
        if len(calls) >= 2:
            watcher._stop.set()
            return trakt_sync.SyncResult(True, "recovered")
        return trakt_sync.SyncResult(False, "network failed", pending=1)

    monkeypatch.setattr(watcher, "sync_now", fake_sync_now)
    monkeypatch.setattr(watcher, "_mtime", lambda: 0.0)
    monkeypatch.setattr(
        watcher,
        "_latest_event",
        lambda: {"kind": "episode", "simkl_id": 100, "season": 1, "episode": 1},
    )
    watcher.set_result_callback(lambda result, event: receipts.append((result, event)))

    watcher._watch_loop()

    assert len(calls) == 2
    assert receipts == []


def test_history_saved_sync_emits_exact_completion_receipt(monkeypatch):
    watcher = TraktSyncWatcher(poll_seconds=0.01, debounce_seconds=0.01)
    results = [
        trakt_sync.SyncResult(True, "startup"),
        trakt_sync.SyncResult(True, "Trakt: +1 episode(s)", pushed=True),
    ]
    event = {
        "event_id": "8ddf190c-57fd-48cb-838b-8457fd340b83",
        "kind": "episode",
        "title": "Example",
        "simkl_id": 100,
        "season": 2,
        "episode": 3,
        "watched_at": "2026-07-17T10:00:00Z",
        "is_anime": False,
    }
    receipts = []

    monkeypatch.setattr(watcher, "sync_now", lambda: results.pop(0))
    monkeypatch.setattr(watcher, "_mtime", lambda: 1.0)
    monkeypatch.setattr(watcher, "_latest_event", lambda: event)

    def on_result(result, latest_event):
        receipts.append((result, latest_event))
        watcher._stop.set()

    watcher.set_result_callback(on_result)
    watcher._history_saved.set()
    watcher._watch_loop()

    assert len(receipts) == 1
    assert receipts[0][0].ok is True
    assert receipts[0][0].pushed is True
    assert receipts[0][1] == event


def test_completion_event_id_survives_history_flattening():
    event_id = "8ddf190c-57fd-48cb-838b-8457fd340b83"
    history = [
        {
            "simkl_id": 100,
            "title": "Example",
            "type": "show",
            "watch_events": [
                {
                    "event_id": event_id,
                    "season": 2,
                    "episode": 3,
                    "watched_at": "2026-07-17T10:00:00Z",
                }
            ],
        }
    ]

    events = trakt_sync.collect_history_events(history, None)

    assert events[0]["event_id"] == event_id
    assert trakt_sync._event_key(events[0]) == event_id


def test_trakt_outcome_is_recorded_against_same_completion_event(tmp_path, monkeypatch):
    ledger = BacklogCleaner(tmp_path)
    event_id = ledger.add(100, "Example", unique_event=True)
    ledger.remove(event_id)
    watcher = TraktSyncWatcher()
    event = {
        "event_id": event_id,
        "kind": "movie",
        "title": "Example",
        "simkl_id": 100,
        "watched_at": "2026-07-17T10:00:00Z",
    }
    monkeypatch.setattr(trakt_sync, "APP_DATA_DIR", tmp_path)
    monkeypatch.setattr(watcher, "_latest_event", lambda: event)
    watcher.set_result_callback(lambda result, latest_event: None)

    watcher._emit_result(trakt_sync.SyncResult(True, "accepted"))

    outcomes = BacklogCleaner(tmp_path).get_event(event_id)["provider_outcomes"]
    assert outcomes[-1]["provider"] == "trakt"
    assert outcomes[-1]["status"] == "accepted"


def test_repeated_trakt_retry_emits_receipt_only_when_outcome_changes(monkeypatch):
    watcher = TraktSyncWatcher()
    event = {
        "kind": "episode",
        "title": "Example",
        "simkl_id": 100,
        "season": 2,
        "episode": 3,
        "watched_at": "2026-07-17T10:00:00Z",
    }
    receipts = []

    monkeypatch.setattr(watcher, "_latest_event", lambda: event)
    watcher.set_result_callback(lambda result, latest_event: receipts.append((result, latest_event)))

    pending = trakt_sync.SyncResult(False, "still pending", pending=1)
    accepted = trakt_sync.SyncResult(True, "recovered")
    watcher._emit_result(pending)
    watcher._emit_result(pending)
    watcher._emit_result(accepted)
    watcher._emit_result(accepted)

    assert [result for result, _event in receipts] == [pending, accepted]
    assert [latest_event for _result, latest_event in receipts] == [event, event]
