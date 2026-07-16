import json

from simkl_mps import trakt_sync


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
    for name, value in (
        ("CONFIG_FILE", config_file),
        ("TOKEN_FILE", token_file),
        ("STATE_FILE", state_file),
        ("HISTORY_FILE", history_file),
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
