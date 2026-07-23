import json
import sqlite3
import threading
import uuid

from simkl_mps.backlog_cleaner import BacklogCleaner
from simkl_mps.media_cache import MediaCache
from simkl_mps.media_scrobbler import MediaScrobbler
from simkl_mps.simkl_api import HistorySyncResult, ProviderStatus
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

    pending = cleaner.get_pending()
    assert pending["event"]["simkl_id"] == expected["event"]["simkl_id"]
    assert pending["event"]["title"] == expected["event"]["title"]
    assert cleaner.database_file.exists()
    assert list(tmp_path.glob("backlog.json.corrupt-*"))


def test_backlog_recovers_backup_when_json_has_wrong_type(tmp_path):
    expected = {"event": {"simkl_id": 1, "title": "Example"}}
    backlog = tmp_path / "backlog.json"
    backup = tmp_path / "backlog.json.bak"
    backlog.write_text("true", encoding="utf-8")
    backup.write_text(json.dumps(expected), encoding="utf-8")

    cleaner = BacklogCleaner(tmp_path)

    pending = cleaner.get_pending()
    assert pending["event"]["simkl_id"] == expected["event"]["simkl_id"]
    assert pending["event"]["title"] == expected["event"]["title"]
    assert cleaner.database_file.exists()
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
    connect = cleaner._connect
    monkeypatch.setattr(
        cleaner,
        "_connect",
        lambda: (_ for _ in ()).throw(sqlite3.OperationalError("write failed")),
    )

    item_key = cleaner.add(
        100,
        "Example",
        {"simkl_id": 100, "type": "show", "season": 1, "episode": 1},
        unique_event=True,
    )

    assert item_key is None
    monkeypatch.setattr(cleaner, "_connect", connect)
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


def test_failed_backlog_writes_reuse_one_completion_timestamp():
    payloads = []

    class FailingBacklog:
        @staticmethod
        def add(*_args, **kwargs):
            payloads.append(dict(kwargs["additional_data"]))
            return None

    scrobbler = MediaScrobbler.__new__(MediaScrobbler)
    scrobbler.backlog_cleaner = FailingBacklog()
    scrobbler.current_filepath = "example.mkv"
    scrobbler.currently_tracking = "Example"
    scrobbler.watched_at = None
    scrobbler.last_backlog_attempt_time = {}
    scrobbler.completed = False
    scrobbler._log_playback_event = lambda *args, **kwargs: None
    scrobbler._send_notification = lambda *args, **kwargs: None

    assert not scrobbler._add_to_backlog_due_to_issue(
        100,
        "Example",
        "offline_with_id",
        {"simkl_id": 100, "type": "movie"},
    )
    assert not scrobbler._add_to_backlog_due_to_issue(
        100,
        "Example",
        "offline_with_id",
        {"simkl_id": 100, "type": "movie"},
    )

    assert payloads[0]["watched_at"] == payloads[1]["watched_at"]
    assert scrobbler.watched_at == payloads[0]["watched_at"]


def test_backlog_update_rolls_back_when_durable_write_fails(tmp_path, monkeypatch):
    cleaner = BacklogCleaner(tmp_path)
    cleaner.add(100, "Example", {"attempt_count": 0})
    connect = cleaner._connect
    monkeypatch.setattr(
        cleaner,
        "_connect",
        lambda: (_ for _ in ()).throw(sqlite3.OperationalError("write failed")),
    )

    updated = cleaner.update_item(100, {"attempt_count": 1})

    assert updated is False
    monkeypatch.setattr(cleaner, "_connect", connect)
    assert cleaner.get_pending()["100"]["attempt_count"] == 0


def test_completion_ledger_uses_wal_and_stable_uuid(tmp_path):
    cleaner = BacklogCleaner(tmp_path)

    event_id = cleaner.add(
        100,
        "Example",
        {"type": "show", "season": 1, "episode": 2},
        unique_event=True,
    )

    assert str(uuid.UUID(event_id)) == event_id
    event = cleaner.get_event(event_id)
    assert event["event_id"] == event_id
    assert event["watched_at"].endswith("Z")
    with sqlite3.connect(cleaner.database_file) as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert BacklogCleaner(tmp_path).get_event(event_id)["event_id"] == event_id


def test_completion_ledger_persists_typed_provider_outcomes(tmp_path):
    cleaner = BacklogCleaner(tmp_path)
    event_id = cleaner.add(100, "Example", unique_event=True)

    assert cleaner.record_outcome(
        event_id,
        provider="simkl",
        status="rate_limited",
        retryable=True,
        status_code=429,
        detail={"retry_after": 10},
    )
    assert cleaner.record_outcome(
        event_id,
        provider="simkl",
        status="accepted",
        retryable=False,
        status_code=201,
    )
    assert cleaner.remove(event_id)

    reopened = BacklogCleaner(tmp_path)
    event = reopened.get_event(event_id)
    assert event["delivery_state"] == "delivered"
    assert [outcome["status"] for outcome in event["provider_outcomes"]] == [
        "rate_limited",
        "accepted",
    ]
    assert reopened.get_pending() == {}


def test_online_completion_is_durable_before_remote_delivery(tmp_path, monkeypatch):
    cleaner = BacklogCleaner(tmp_path)
    scrobbler = MediaScrobbler.__new__(MediaScrobbler)
    scrobbler.backlog_cleaner = cleaner
    scrobbler.current_filepath = str(tmp_path / "example.mkv")
    scrobbler.currently_tracking = "Example"
    scrobbler.movie_name = "Example"
    scrobbler.media_type = "movie"
    scrobbler.simkl_id = 100
    scrobbler.season = None
    scrobbler.episode = None
    scrobbler.client_id = "client"
    scrobbler.access_token = "token"
    scrobbler.completed = False
    scrobbler.watched_at = None
    scrobbler.last_backlog_attempt_time = {}
    scrobbler._build_add_to_history_payload = (
        lambda watched_at=None: {
            "movies": [{"ids": {"simkl": 100}, "watched_at": watched_at}]
        }
    )
    scrobbler._store_in_watch_history = lambda *args, **kwargs: True
    scrobbler._log_playback_event = lambda *args, **kwargs: None
    scrobbler._send_notification = lambda *args, **kwargs: None
    receipts = []
    scrobbler.completion_callback = receipts.append

    delivered_event_ids = []

    def add_to_history(*args, **kwargs):
        pending = cleaner.get_pending()
        assert len(pending) == 1
        delivered_event_ids.extend(pending)
        return HistorySyncResult(
            ProviderStatus.ACCEPTED,
            retryable=False,
            payload={"added": {"movies": 1}, "not_found": {}},
            status_code=201,
        )

    monkeypatch.setattr(
        "simkl_mps.media_scrobbler.is_internet_connected",
        lambda: True,
    )
    monkeypatch.setattr(
        "simkl_mps.media_scrobbler.add_to_history",
        add_to_history,
    )

    assert scrobbler._attempt_add_to_history() is True

    event = cleaner.get_event(delivered_event_ids[0])
    assert event["delivery_state"] == "delivered"
    assert event["watched_at"] == scrobbler.watched_at
    assert event["provider_outcomes"][0]["status"] == "accepted"
    assert receipts[0]["event_id"] == delivered_event_ids[0]
    assert receipts[0]["simkl_status"] == "Accepted"


def test_starting_new_media_item_clears_previous_completion_timestamp(monkeypatch):
    class EmptyCache:
        @staticmethod
        def get(_key):
            return None

    scrobbler = MediaScrobbler.__new__(MediaScrobbler)
    scrobbler.watched_at = "2026-07-15T18:00:00Z"
    scrobbler.media_cache = EmptyCache()
    scrobbler._derive_display_season_episode = lambda: None
    scrobbler._send_notification = lambda *args, **kwargs: None
    monkeypatch.setattr(
        "simkl_mps.media_scrobbler.is_internet_connected",
        lambda: False,
    )

    scrobbler._start_new_media_item("Second Item", None, "movie")

    assert scrobbler.watched_at is None


def test_provider_acceptance_is_not_replayed_when_outcome_audit_initially_fails(
    tmp_path,
    monkeypatch,
):
    cleaner = BacklogCleaner(tmp_path)
    scrobbler = MediaScrobbler.__new__(MediaScrobbler)
    scrobbler.backlog_cleaner = cleaner
    scrobbler.current_filepath = str(tmp_path / "example.mkv")
    scrobbler.currently_tracking = "Example"
    scrobbler.movie_name = "Example"
    scrobbler.media_type = "movie"
    scrobbler.simkl_id = 100
    scrobbler.season = None
    scrobbler.episode = None
    scrobbler.client_id = "client"
    scrobbler.access_token = "token"
    scrobbler.completed = False
    scrobbler.watched_at = None
    scrobbler.last_backlog_attempt_time = {}
    scrobbler._processing_lock = threading.Lock()
    scrobbler._processing_backlog_items = set()
    scrobbler._backlog_run_lock = threading.Lock()
    scrobbler._build_add_to_history_payload = (
        lambda watched_at=None: {
            "movies": [{"ids": {"simkl": 100}, "watched_at": watched_at}]
        }
    )
    scrobbler._store_in_watch_history = lambda *args, **kwargs: True
    scrobbler._log_playback_event = lambda *args, **kwargs: None
    scrobbler._send_notification = lambda *args, **kwargs: None
    scrobbler.completion_callback = None

    provider_calls = []
    original_record_outcome = cleaner.record_outcome
    record_attempts = 0

    def fail_first_outcome(*args, **kwargs):
        nonlocal record_attempts
        record_attempts += 1
        if record_attempts == 1:
            return False
        return original_record_outcome(*args, **kwargs)

    def accepted(*args, **kwargs):
        provider_calls.append((args, kwargs))
        return HistorySyncResult(
            ProviderStatus.ACCEPTED,
            retryable=False,
            payload={"added": {"movies": 1}, "not_found": {}},
            status_code=201,
        )

    monkeypatch.setattr(cleaner, "record_outcome", fail_first_outcome)
    monkeypatch.setattr(
        "simkl_mps.media_scrobbler.is_internet_connected",
        lambda: True,
    )
    monkeypatch.setattr("simkl_mps.media_scrobbler.add_to_history", accepted)

    assert scrobbler._attempt_add_to_history() is True
    pending = cleaner.get_pending()
    assert len(pending) == 1
    event_id, event = next(iter(pending.items()))
    assert event["simkl_synced"] is True
    assert event["local_history_saved"] is True
    assert event["provider_outcome_pending"] is True
    assert cleaner.get_event(event_id)["provider_outcomes"] == []

    result = scrobbler.process_backlog()

    assert result["processed"] == 1
    assert len(provider_calls) == 1
    delivered = cleaner.get_event(event_id)
    assert delivered["delivery_state"] == "delivered"
    assert delivered["provider_outcomes"][-1]["status"] == "accepted"
    assert delivered["provider_outcomes"][-1]["detail"] == {
        "recovered_from": "simkl_synced_marker"
    }


def test_persisted_acceptance_prevents_replay_without_synced_marker(
    tmp_path,
    monkeypatch,
):
    cleaner = BacklogCleaner(tmp_path)
    event_id = cleaner.add(
        100,
        "Example",
        {
            "simkl_id": 100,
            "title": "Example",
            "type": "movie",
            "watched_at": "2026-07-23T12:00:00Z",
        },
        unique_event=True,
    )
    assert cleaner.record_outcome(
        event_id,
        provider="simkl",
        status="accepted",
        retryable=False,
        status_code=201,
    )

    scrobbler = MediaScrobbler.__new__(MediaScrobbler)
    scrobbler.client_id = None
    scrobbler.access_token = None
    scrobbler.backlog_cleaner = cleaner
    scrobbler._processing_lock = threading.Lock()
    scrobbler._processing_backlog_items = set()
    scrobbler._backlog_run_lock = threading.Lock()
    scrobbler._send_notification = lambda *args, **kwargs: None
    scrobbler._store_in_watch_history = lambda *args, **kwargs: True

    monkeypatch.setattr(
        "simkl_mps.media_scrobbler.is_internet_connected",
        lambda: False,
    )
    monkeypatch.setattr(
        "simkl_mps.media_scrobbler.add_to_history",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("accepted completion was replayed")
        ),
    )

    result = scrobbler.process_backlog()

    assert result["processed"] == 1
    assert cleaner.get_event(event_id)["delivery_state"] == "delivered"


def test_process_backlog_has_exactly_one_active_owner():
    entered = threading.Event()
    release = threading.Event()
    scrobbler = MediaScrobbler.__new__(MediaScrobbler)
    scrobbler._backlog_run_lock = threading.Lock()

    def process_once():
        entered.set()
        release.wait(1)
        return {"processed": 0, "attempted": 0, "failed": False}

    scrobbler._process_backlog_once = process_once
    first_result = []
    worker = threading.Thread(
        target=lambda: first_result.append(scrobbler.process_backlog())
    )
    worker.start()
    assert entered.wait(1)

    second_result = scrobbler.process_backlog()
    release.set()
    worker.join(1)

    assert second_result["reason"] == "Backlog worker already running"
    assert first_result[0]["failed"] is False


def test_backlog_payload_build_does_not_mutate_live_playback(tmp_path, monkeypatch):
    ledger = BacklogCleaner(tmp_path)
    ledger.add(
        100,
        "Queued Movie",
        {
            "simkl_id": 100,
            "type": "movie",
            "watched_at": "2026-07-23T12:00:00Z",
        },
        unique_event=False,
    )
    scrobbler = MediaScrobbler.__new__(MediaScrobbler)
    scrobbler.client_id = "configured"
    scrobbler.access_token = "configured"
    scrobbler.backlog_cleaner = ledger
    scrobbler._processing_lock = threading.Lock()
    scrobbler._processing_backlog_items = set()
    scrobbler._backlog_run_lock = threading.Lock()
    scrobbler._send_notification = lambda *args, **kwargs: None
    scrobbler._resolve_backlog_item_identity = lambda key, data: (True, data, None)
    scrobbler._fetch_and_update_cache_with_full_details = (
        lambda *args, **kwargs: None
    )
    scrobbler._store_in_watch_history = lambda *args, **kwargs: True
    scrobbler.completion_callback = None
    scrobbler.simkl_id = 999
    scrobbler.media_type = "show"
    scrobbler.season = 9
    scrobbler.episode = 9
    payloads = []

    monkeypatch.setattr(
        "simkl_mps.media_scrobbler.is_internet_connected",
        lambda: True,
    )
    monkeypatch.setattr(
        "simkl_mps.media_scrobbler.add_to_history",
        lambda payload, *_args, **_kwargs: payloads.append(payload)
        or HistorySyncResult(
            ProviderStatus.ACCEPTED,
            retryable=False,
            payload={"added": {"movies": 1}, "not_found": {}},
            status_code=201,
        ),
    )

    result = scrobbler.process_backlog()

    assert result["processed"] == 1
    assert payloads == [
        {
            "movies": [
                {
                    "ids": {"simkl": 100},
                    "watched_at": "2026-07-23T12:00:00Z",
                }
            ]
        }
    ]
    assert (
        scrobbler.simkl_id,
        scrobbler.media_type,
        scrobbler.season,
        scrobbler.episode,
    ) == (999, "show", 9, 9)


def test_clear_pending_events_uses_the_queue_owner_lock():
    events = []

    class RecordingLock:
        def __enter__(self):
            events.append("locked")

        def __exit__(self, *_args):
            events.append("unlocked")

    class Cleaner:
        def clear(self):
            events.append("cleared")
            return True

    scrobbler = MediaScrobbler.__new__(MediaScrobbler)
    scrobbler._backlog_run_lock = RecordingLock()
    scrobbler.backlog_cleaner = Cleaner()
    scrobbler.clear_backlog_processing_state = lambda: events.append("reset")

    assert scrobbler.clear_pending_completion_events() is True
    assert events == ["locked", "cleared", "reset", "unlocked"]


def test_permanent_simkl_failure_moves_event_out_of_retry_queue(tmp_path, monkeypatch):
    ledger = BacklogCleaner(tmp_path)
    event_id = ledger.add(
        100,
        "Example",
        {
            "type": "movie",
            "attempt_count": 0,
            "last_attempt_timestamp": None,
        },
        unique_event=True,
    )
    scrobbler = MediaScrobbler.__new__(MediaScrobbler)
    scrobbler.client_id = "configured"
    scrobbler.access_token = "configured"
    scrobbler.backlog_cleaner = ledger
    scrobbler._processing_lock = threading.Lock()
    scrobbler._processing_backlog_items = set()
    scrobbler._backlog_run_lock = threading.Lock()
    scrobbler._backlog_notification_throttle = {}
    scrobbler._send_notification = lambda *args, **kwargs: None
    scrobbler._resolve_backlog_item_identity = lambda key, data: (True, data, None)
    scrobbler._build_add_to_history_payload = lambda watched_at=None: {"movies": [{}]}
    scrobbler.simkl_id = None
    scrobbler.media_type = None
    scrobbler.season = None
    scrobbler.episode = None

    monkeypatch.setattr(
        "simkl_mps.media_scrobbler.is_internet_connected",
        lambda: True,
    )
    monkeypatch.setattr(
        "simkl_mps.media_scrobbler.add_to_history",
        lambda *args, **kwargs: HistorySyncResult(
            ProviderStatus.UNAUTHORIZED,
            retryable=False,
            status_code=401,
            error="invalid token",
        ),
    )

    result = scrobbler.process_backlog()

    assert result["attempted"] == 1
    assert ledger.get_pending() == {}
    event = ledger.get_event(event_id)
    assert event["delivery_state"] == "failed"
    assert event["provider_outcomes"][-1]["status"] == "unauthorized"


def test_reauthentication_requeues_only_unauthorized_events(tmp_path):
    ledger = BacklogCleaner(tmp_path)
    unauthorized_id = ledger.add(100, "Unauthorized", unique_event=True)
    rejected_id = ledger.add(200, "Rejected", unique_event=True)
    assert ledger.record_outcome(
        unauthorized_id,
        provider="simkl",
        status="unauthorized",
        retryable=False,
        status_code=401,
    )
    assert ledger.record_outcome(
        rejected_id,
        provider="simkl",
        status="not_found",
        retryable=False,
        status_code=201,
    )
    assert ledger.claim_event(unauthorized_id, "crashed-worker") is True
    assert ledger.fail(unauthorized_id, "invalid token")
    assert ledger.fail(rejected_id, "not found")

    scrobbler = MediaScrobbler.__new__(MediaScrobbler)
    scrobbler.client_id = "old-client"
    scrobbler.access_token = "old-token"
    scrobbler.testing_mode = True
    scrobbler._account_type = None
    scrobbler._account_settings_all = None
    scrobbler.backlog_cleaner = ledger
    scrobbler._backlog_wakeup = threading.Event()

    scrobbler.set_credentials("new-client", "new-token")

    pending = ledger.get_pending()
    assert list(pending) == [unauthorized_id]
    assert pending[unauthorized_id]["attempt_count"] == 0
    assert pending[unauthorized_id]["last_attempt_timestamp"] is None
    assert pending[unauthorized_id]["last_error"] is None
    assert ledger.get_event(rejected_id)["delivery_state"] == "failed"
    assert scrobbler._backlog_wakeup.is_set()
    assert ledger.claim_event(unauthorized_id, "replacement-worker") is True


def test_reauthentication_during_unauthorized_attempt_does_not_strand_event(
    tmp_path,
    monkeypatch,
):
    ledger = BacklogCleaner(tmp_path)
    event_id = ledger.add(
        100,
        "Example",
        {
            "type": "movie",
            "attempt_count": 0,
            "last_attempt_timestamp": None,
        },
        unique_event=True,
    )
    scrobbler = MediaScrobbler.__new__(MediaScrobbler)
    scrobbler.client_id = "old-client"
    scrobbler.access_token = "old-token"
    scrobbler.testing_mode = True
    scrobbler._account_type = None
    scrobbler._account_settings_all = None
    scrobbler.backlog_cleaner = ledger
    scrobbler._processing_lock = threading.Lock()
    scrobbler._processing_backlog_items = set()
    scrobbler._backlog_run_lock = threading.Lock()
    scrobbler._backlog_wakeup = threading.Event()
    scrobbler._backlog_notification_throttle = {}
    scrobbler._send_notification = lambda *args, **kwargs: None
    scrobbler._resolve_backlog_item_identity = lambda key, data: (True, data, None)
    scrobbler.simkl_id = None
    scrobbler.media_type = None
    scrobbler.season = None
    scrobbler.episode = None

    def unauthorized_after_reauth(*_args, **_kwargs):
        scrobbler.set_credentials("new-client", "new-token")
        return HistorySyncResult(
            ProviderStatus.UNAUTHORIZED,
            retryable=False,
            status_code=401,
            error="old token rejected",
        )

    monkeypatch.setattr(
        "simkl_mps.media_scrobbler.is_internet_connected",
        lambda: True,
    )
    monkeypatch.setattr(
        "simkl_mps.media_scrobbler.add_to_history",
        unauthorized_after_reauth,
    )

    result = scrobbler.process_backlog()

    assert result["attempted"] == 1
    assert list(ledger.get_pending()) == [event_id]
    assert ledger.get_event(event_id)["provider_outcomes"][-1]["status"] == "unauthorized"
    assert scrobbler._backlog_wakeup.is_set()


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


def test_watch_history_projection_is_idempotent_by_event_id(tmp_path, monkeypatch):
    monkeypatch.setattr(WatchHistoryManager, "_ensure_viewer_exists", lambda self: None)
    event = {
        "event_id": "7ee88a82-9ea5-4d45-951b-8472e0a5b0c8",
        "simkl_id": 100,
        "title": "Example",
        "type": "movie",
        "watched_at": "2026-07-23T12:00:00Z",
    }
    manager = WatchHistoryManager(tmp_path)
    assert manager.add_entry(event)

    reopened = WatchHistoryManager(tmp_path)
    assert reopened.add_entry(event)

    entry = reopened.history[0]
    assert entry["watch_count"] == 1
    assert entry["rewatch_count"] == 0
    assert [watch["event_id"] for watch in entry["watch_events"]] == [event["event_id"]]


def test_viewer_projection_cannot_replace_an_inflight_history_add(tmp_path, monkeypatch):
    monkeypatch.setattr(WatchHistoryManager, "_ensure_viewer_exists", lambda self: None)
    manager = WatchHistoryManager(tmp_path)
    save_started = threading.Event()
    allow_save = threading.Event()
    viewer_loaded = threading.Event()
    writer_result = []
    original_save = manager._save_history
    original_load = manager._load_history

    def paused_save(create_backup=True):
        save_started.set()
        assert allow_save.wait(2)
        return original_save(create_backup=create_backup)

    def observed_load():
        if threading.current_thread().name == "history-viewer":
            viewer_loaded.set()
        return original_load()

    monkeypatch.setattr(manager, "_save_history", paused_save)
    monkeypatch.setattr(manager, "_load_history", observed_load)

    writer = threading.Thread(
        target=lambda: writer_result.append(
            manager.add_entry(
                {
                    "event_id": "d0408a59-8964-41a9-a03b-df0772b451cc",
                    "simkl_id": 100,
                    "title": "Example",
                    "type": "movie",
                    "watched_at": "2026-07-23T12:00:00Z",
                }
            )
        )
    )
    viewer = threading.Thread(target=manager._update_history_data, name="history-viewer")
    writer.start()
    assert save_started.wait(2)
    viewer.start()

    assert not viewer_loaded.wait(0.1)
    allow_save.set()
    writer.join(2)
    viewer.join(2)

    assert not writer.is_alive()
    assert not viewer.is_alive()
    assert writer_result == [True]
    assert len(manager.history) == 1
    saved = json.loads(manager.history_file.read_text(encoding="utf-8"))
    assert len(saved) == 1
    assert saved[0]["watch_events"][0]["event_id"] == "d0408a59-8964-41a9-a03b-df0772b451cc"


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
        "provider_outcomes": [{"provider": "simkl", "status": "accepted"}],
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
