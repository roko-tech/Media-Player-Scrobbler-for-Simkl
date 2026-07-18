import json
import threading

from simkl_mps.backlog_cleaner import BacklogCleaner
from simkl_mps.media_cache import MediaCache
from simkl_mps.media_scrobbler import MediaScrobbler
from simkl_mps.watch_history_manager import WatchHistoryManager


def test_watch_history_recovers_backup_instead_of_erasing_corrupt_data(tmp_path):
    expected = [{"simkl_id": 1, "title": "Example", "type": "movie"}]
    history = tmp_path / "watch_history.json"
    backup = tmp_path / "watch_history.json.bak"
    history.write_text("{broken", encoding="utf-8")
    backup.write_text(json.dumps(expected), encoding="utf-8")

    manager = WatchHistoryManager(tmp_path)

    assert manager.history == expected
    assert json.loads(history.read_text(encoding="utf-8")) == expected
    assert list(tmp_path.glob("watch_history.json.corrupt-*"))


def test_watch_history_recovers_backup_when_json_has_wrong_type(tmp_path):
    expected = [{"simkl_id": 1, "title": "Example", "type": "movie"}]
    history = tmp_path / "watch_history.json"
    backup = tmp_path / "watch_history.json.bak"
    history.write_text("{}", encoding="utf-8")
    backup.write_text(json.dumps(expected), encoding="utf-8")

    manager = WatchHistoryManager(tmp_path)

    assert manager.history == expected
    assert json.loads(history.read_text(encoding="utf-8")) == expected
    assert list(tmp_path.glob("watch_history.json.corrupt-*"))


def test_backlog_recovers_backup_instead_of_erasing_corrupt_data(tmp_path):
    expected = {"event": {"simkl_id": 1, "title": "Example"}}
    backlog = tmp_path / "backlog.json"
    backup = tmp_path / "backlog.json.bak"
    backlog.write_text("{broken", encoding="utf-8")
    backup.write_text(json.dumps(expected), encoding="utf-8")

    cleaner = BacklogCleaner(tmp_path)

    assert cleaner.get_pending() == expected
    assert json.loads(backlog.read_text(encoding="utf-8")) == expected
    assert list(tmp_path.glob("backlog.json.corrupt-*"))


def test_backlog_recovers_backup_when_json_has_wrong_type(tmp_path):
    expected = {"event": {"simkl_id": 1, "title": "Example"}}
    backlog = tmp_path / "backlog.json"
    backup = tmp_path / "backlog.json.bak"
    backlog.write_text("true", encoding="utf-8")
    backup.write_text(json.dumps(expected), encoding="utf-8")

    cleaner = BacklogCleaner(tmp_path)

    assert cleaner.get_pending() == expected
    assert json.loads(backlog.read_text(encoding="utf-8")) == expected
    assert list(tmp_path.glob("backlog.json.corrupt-*"))


def test_completed_backlog_events_never_overwrite_same_show_episode(tmp_path):
    cleaner = BacklogCleaner(tmp_path)

    first = cleaner.add(
        100,
        "Example",
        {"simkl_id": 100, "type": "show", "season": 1, "episode": 1},
        unique_event=True,
    )
    second = cleaner.add(
        100,
        "Example",
        {"simkl_id": 100, "type": "show", "season": 1, "episode": 2},
        unique_event=True,
    )

    assert first != second
    assert len(cleaner.get_pending()) == 2


def test_legacy_backlog_conversion_preserves_duplicate_show_events(tmp_path):
    (tmp_path / "backlog.json").write_text(
        json.dumps(
            [
                {"simkl_id": 100, "title": "Example", "season": 1, "episode": 1},
                {"simkl_id": 100, "title": "Example", "season": 1, "episode": 2},
            ]
        ),
        encoding="utf-8",
    )

    cleaner = BacklogCleaner(tmp_path)

    assert len(cleaner.get_pending()) == 2


def test_backlog_add_reports_failed_durable_write(tmp_path, monkeypatch):
    cleaner = BacklogCleaner(tmp_path)
    monkeypatch.setattr(cleaner, "_save_backlog", lambda: False)

    item_key = cleaner.add(
        100,
        "Example",
        {"simkl_id": 100, "type": "show", "season": 1, "episode": 1},
        unique_event=True,
    )

    assert item_key is None
    assert cleaner.get_pending() == {}


def test_scrobbler_does_not_complete_when_backlog_write_fails(monkeypatch):
    scrobbler = MediaScrobbler.__new__(MediaScrobbler)
    scrobbler.backlog_cleaner = type(
        "Backlog", (), {"add": lambda self, *args, **kwargs: None}
    )()
    scrobbler.current_filepath = "example.mkv"
    scrobbler.currently_tracking = "Example"
    scrobbler.watched_at = "2026-07-15T18:00:00Z"
    scrobbler.last_backlog_attempt_time = {}
    scrobbler.completed = False
    scrobbler._log_playback_event = lambda *args, **kwargs: None
    notifications = []
    scrobbler._send_notification = lambda *args, **kwargs: notifications.append(
        (args, kwargs)
    )

    queued = scrobbler._add_to_backlog_due_to_issue(
        100,
        "Example",
        "offline_with_id",
        {"simkl_id": 100, "type": "show", "season": 1, "episode": 1},
    )

    assert queued is False
    assert scrobbler.completed is False
    assert scrobbler.last_backlog_attempt_time == {}
    assert notifications[-1][1]["critical"] is True


def test_backlog_update_rolls_back_when_durable_write_fails(tmp_path, monkeypatch):
    cleaner = BacklogCleaner(tmp_path)
    cleaner.add(100, "Example", {"attempt_count": 0})
    monkeypatch.setattr(cleaner, "_save_backlog", lambda: False)

    updated = cleaner.update_item(100, {"attempt_count": 1})

    assert updated is False
    assert cleaner.get_pending()["100"]["attempt_count"] == 0


def test_history_add_rolls_back_when_durable_write_fails(tmp_path, monkeypatch):
    manager = WatchHistoryManager(tmp_path)
    monkeypatch.setattr(manager, "_save_history", lambda: False)

    saved = manager.add_entry(
        {
            "simkl_id": 100,
            "title": "Example",
            "type": "show",
            "season": 1,
            "episode": 1,
        }
    )

    assert saved is False
    assert manager.history == []


def test_backlog_retries_past_old_five_attempt_limit(monkeypatch):
    item = {
        "simkl_id": 100,
        "title": "Example",
        "type": "show",
        "season": 1,
        "episode": 2,
        "attempt_count": 5,
        "last_attempt_timestamp": None,
    }

    class Backlog:
        def __init__(self):
            self.items = {"event": item}
            self.updated = None
            self.removed = []

        def get_pending(self):
            return self.items

        def update_item(self, key, updates):
            self.updated = (key, updates)
            self.items[key].update(updates)
            return True

        def remove(self, key):
            self.removed.append(key)
            self.items.pop(key, None)
            return True

    backlog = Backlog()
    scrobbler = MediaScrobbler.__new__(MediaScrobbler)
    scrobbler.client_id = "configured"
    scrobbler.access_token = "configured"
    scrobbler.backlog_cleaner = backlog
    scrobbler._processing_lock = threading.Lock()
    scrobbler._processing_backlog_items = set()
    scrobbler._backlog_notification_throttle = {}
    scrobbler._send_notification = lambda *args, **kwargs: None
    scrobbler._resolve_backlog_item_identity = (
        lambda key, data: (False, data, "temporary failure")
    )
    monkeypatch.setattr("simkl_mps.media_scrobbler.is_internet_connected", lambda: True)

    result = scrobbler.process_backlog()

    assert result["attempted"] == 1
    assert backlog.removed == []
    assert backlog.updated[1]["attempt_count"] == 6


def test_backlog_cooldown_does_not_repeat_ready_notification(monkeypatch):
    item = {
        "simkl_id": 100,
        "title": "Example",
        "type": "show",
        "season": 1,
        "episode": 2,
        "attempt_count": 3,
        "last_attempt_timestamp": 10**20,
    }

    class Backlog:
        @staticmethod
        def get_pending():
            return {"event": item}

    notifications = []
    scrobbler = MediaScrobbler.__new__(MediaScrobbler)
    scrobbler.client_id = "configured"
    scrobbler.access_token = "configured"
    scrobbler.backlog_cleaner = Backlog()
    scrobbler._processing_lock = threading.Lock()
    scrobbler._processing_backlog_items = set()
    scrobbler._send_notification = lambda *args, **kwargs: notifications.append(args)
    monkeypatch.setattr("simkl_mps.media_scrobbler.is_internet_connected", lambda: True)

    result = scrobbler.process_backlog()

    assert result["attempted"] == 0
    assert notifications == []


def test_simkl_not_found_response_is_not_accepted():
    scrobbler = MediaScrobbler.__new__(MediaScrobbler)

    accepted = scrobbler._simkl_history_result_accepted(
        {"added": {"episodes": 0}, "not_found": {"episodes": [{"ids": {}}]}}
    )

    assert accepted is False


def test_simkl_added_or_empty_success_response_is_accepted():
    scrobbler = MediaScrobbler.__new__(MediaScrobbler)

    assert scrobbler._simkl_history_result_accepted(
        {"added": {"episodes": 1}, "not_found": {}}
    )
    assert scrobbler._simkl_history_result_accepted(
        {"status": "success", "message": "accepted without JSON"}
    )


def test_store_in_watch_history_returns_failed_save(monkeypatch):
    scrobbler = MediaScrobbler.__new__(MediaScrobbler)
    scrobbler.watch_history = type(
        "History", (), {"add_entry": lambda self, *args, **kwargs: False}
    )()
    scrobbler.current_filepath = None
    scrobbler.media_cache = {}
    scrobbler.client_id = None
    scrobbler.access_token = None
    monkeypatch.setattr("simkl_mps.media_scrobbler.is_internet_connected", lambda: False)

    saved = scrobbler._store_in_watch_history(
        100,
        "Example",
        media_type="show",
        season=1,
        episode=2,
    )

    assert saved is False


def test_local_only_backlog_retry_does_not_require_internet(monkeypatch):
    item = {
        "simkl_id": 100,
        "title": "Example",
        "type": "show",
        "season": 1,
        "episode": 2,
        "simkl_synced": True,
        "attempt_count": 0,
        "last_attempt_timestamp": None,
    }

    class Backlog:
        def __init__(self):
            self.items = {"event": item}
            self.updated = None
            self.removed = []

        def get_pending(self):
            return self.items

        def update_item(self, key, updates):
            self.updated = (key, updates)
            self.items[key].update(updates)
            return True

        def remove(self, key):
            self.removed.append(key)
            self.items.pop(key, None)
            return True

    backlog = Backlog()
    scrobbler = MediaScrobbler.__new__(MediaScrobbler)
    scrobbler.client_id = "configured"
    scrobbler.access_token = "configured"
    scrobbler.backlog_cleaner = backlog
    scrobbler._processing_lock = threading.Lock()
    scrobbler._processing_backlog_items = set()
    scrobbler._backlog_notification_throttle = {}
    scrobbler._send_notification = lambda *args, **kwargs: None
    scrobbler._store_in_watch_history = lambda *args, **kwargs: True
    monkeypatch.setattr("simkl_mps.media_scrobbler.is_internet_connected", lambda: False)

    result = scrobbler.process_backlog()

    assert result["attempted"] == 1
    assert result["processed"] == 1
    assert backlog.removed == ["event"]


def test_episode_cache_keeps_distinct_files_for_same_show(tmp_path):
    scrobbler = MediaScrobbler.__new__(MediaScrobbler)
    scrobbler.media_cache = MediaCache(tmp_path)
    scrobbler.total_duration_seconds = None
    scrobbler.currently_tracking = None
    scrobbler.current_filepath = None

    scrobbler.cache_media_info(
        "example-s02e07.mkv",
        100,
        "Example",
        media_type="anime",
        season=1,
        episode=7,
    )
    scrobbler.cache_media_info(
        "example-s02e08.mkv",
        100,
        "Example",
        media_type="anime",
        season=1,
        episode=8,
    )

    assert scrobbler.media_cache.get("example-s02e07.mkv")["episode"] == 7
    assert scrobbler.media_cache.get("example-s02e08.mkv")["episode"] == 8


def test_identification_receipt_contains_visual_match_details():
    scrobbler = MediaScrobbler.__new__(MediaScrobbler)
    scrobbler.currently_tracking = "The Heroic Legend of Arslan (2015) S02E06"
    scrobbler.current_filepath = "The Heroic Legend of Arslan (2015) - S02E06.mkv"
    scrobbler.movie_name = "Arslan Senki: Fuujin Ranbu"
    scrobbler.simkl_id = 529392
    scrobbler.media_type = "anime"
    scrobbler.season = 1
    scrobbler.episode = 6
    scrobbler.display_season = 2
    scrobbler.display_episode = 6
    scrobbler.identification_callback = None
    scrobbler._last_identification_receipt_key = None
    scrobbler.identification_rejected = False
    receipts = []

    scrobbler.set_identification_callback(receipts.append)
    emitted = scrobbler._emit_identification_receipt(
        {
            "year": 2016,
            "poster_url": "1234/abc567",
            "source": "simkl_search_file",
        }
    )

    assert emitted is True
    assert receipts == [
        {
            "kind": "identification",
            "title": "Arslan Senki: Fuujin Ranbu",
            "year": 2016,
            "media_type": "anime",
            "season": 1,
            "episode": 6,
            "display_season": 2,
            "display_episode": 6,
            "simkl_id": 529392,
            "poster_url": "1234/abc567",
            "match_method": "Simkl file match",
        }
    ]


def test_rejected_identification_blocks_history_submission():
    scrobbler = MediaScrobbler.__new__(MediaScrobbler)
    scrobbler.currently_tracking = "Example (2020)"
    scrobbler.movie_name = "Example"
    scrobbler.identification_rejected = False
    scrobbler._identification_block_logged = False
    scrobbler.completed = False

    assert scrobbler.reject_current_identification() is True
    assert scrobbler._attempt_add_to_history() is False
    assert scrobbler.completed is False
