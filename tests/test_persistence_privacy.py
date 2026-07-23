import json
from pathlib import Path

from simkl_mps.app_paths import AppPathManifest
from simkl_mps.media_scrobbler import MediaScrobbler
from simkl_mps.migration import MIGRATION_MARKER, migrate_user_directory
from simkl_mps.watch_history_manager import WatchHistoryManager


def test_owned_manifest_purge_preserves_cwd_and_unknown_files(tmp_path, monkeypatch):
    app_root = tmp_path / "app-data"
    cwd = tmp_path / "unrelated-project"
    app_root.mkdir()
    cwd.mkdir()
    monkeypatch.chdir(cwd)

    sentinel = cwd / ".env"
    sentinel.write_text("unrelated", encoding="utf-8")
    unknown = app_root / "user-note.txt"
    unknown.write_text("preserve", encoding="utf-8")

    owned_files = [
        app_root / ".simkl_mps.env",
        app_root / "settings.json.bak",
        app_root / "watch_history.json.corrupt-1",
        app_root / "completion_ledger.sqlite3-wal",
        app_root / "simkl_mps.log.2026-01-01",
        app_root / ".first_run_complete",
    ]
    for path in owned_files:
        path.write_text("private", encoding="utf-8")
    viewer = app_root / "watch-history-viewer"
    viewer.mkdir()
    (viewer / "data.js").write_text("private", encoding="utf-8")

    result = AppPathManifest(app_root).purge()

    assert result.success
    assert sentinel.read_text(encoding="utf-8") == "unrelated"
    assert unknown.read_text(encoding="utf-8") == "preserve"
    assert all(not path.exists() for path in owned_files)
    assert not viewer.exists()


def test_history_purge_removes_backups_corrupt_copies_and_viewer(tmp_path):
    manager = WatchHistoryManager(tmp_path)
    private_paths = [
        tmp_path / "watch_history.json",
        tmp_path / "watch_history.json.bak",
        tmp_path / "watch_history.json.corrupt-1",
    ]
    for path in private_paths:
        path.write_text("[]", encoding="utf-8")
    viewer = tmp_path / "watch-history-viewer"
    viewer.mkdir(exist_ok=True)
    (viewer / "data.js").write_text("private", encoding="utf-8")

    result = manager.purge_local_data()

    assert result.success
    assert manager.history == []
    assert all(not path.exists() for path in private_paths)
    assert not viewer.exists()


def test_partial_migration_converges_and_preserves_conflicts(tmp_path, monkeypatch):
    old = tmp_path / "old" / "simkl-mps"
    new = tmp_path / "new" / "simkl-mps"
    old.mkdir(parents=True)
    new.mkdir(parents=True)
    (old / "old-only.json").write_text("old-only", encoding="utf-8")
    (old / "same.json").write_text("same", encoding="utf-8")
    (new / "same.json").write_text("same", encoding="utf-8")
    (old / "conflict.json").write_text("legacy", encoding="utf-8")
    (new / "conflict.json").write_text("current", encoding="utf-8")
    monkeypatch.setattr(
        "simkl_mps.migration.get_user_data_paths",
        lambda: (old, new),
    )

    assert migrate_user_directory() is True
    assert not old.exists()
    assert (new / "old-only.json").read_text(encoding="utf-8") == "old-only"
    assert (new / "same.json").read_text(encoding="utf-8") == "same"
    assert (new / "conflict.json").read_text(encoding="utf-8") == "current"
    conflict_files = list((new / ".migration-conflicts").rglob("conflict.json.*"))
    assert len(conflict_files) == 1
    assert conflict_files[0].read_text(encoding="utf-8") == "legacy"
    marker = json.loads((new / MIGRATION_MARKER).read_text(encoding="utf-8"))
    assert marker["schema"] == 2
    assert len(marker["conflicts"]) == 1
    assert migrate_user_directory() is True


def test_viewer_projection_redacts_paths_by_default(tmp_path, monkeypatch):
    history = [{
        "simkl_id": 1,
        "title": "Example",
        "file_path": r"D:\Private\Example.mkv",
        "watch_events": [{
            "watched_at": "2026-01-01T00:00:00Z",
            "media_file_path": r"D:\Private\Example.mkv",
        }],
    }]
    (tmp_path / "watch_history.json").write_text(
        json.dumps(history),
        encoding="utf-8",
    )
    manager = WatchHistoryManager(tmp_path)
    monkeypatch.setattr(
        "simkl_mps.watch_history_manager.get_setting",
        lambda name, default=None: False
        if name == "viewer_include_file_paths"
        else default,
    )

    manager._update_history_data()
    viewer_data = (tmp_path / "watch-history-viewer" / "data.js").read_text(
        encoding="utf-8"
    )

    assert "D:\\\\Private" not in viewer_data
    assert "file_path" not in viewer_data
    assert "media_file_path" not in viewer_data


def test_history_is_unlimited_by_default(tmp_path, monkeypatch):
    manager = WatchHistoryManager(tmp_path)
    monkeypatch.setattr(
        "simkl_mps.watch_history_manager.get_setting",
        lambda name, default=None: 0 if name == "history_retention_limit" else default,
    )
    monkeypatch.setattr(manager, "_save_history", lambda: True)
    monkeypatch.setattr(manager, "_notify_saved", lambda: None)

    for index in range(501):
        assert manager._add_entry_unlocked({
            "simkl_id": index + 1,
            "title": f"Item {index}",
            "type": "movie",
            "watched_at": f"2026-01-01T00:00:{index % 60:02d}Z",
        })

    assert len(manager.history) == 501


def test_offline_viewer_has_no_automatic_remote_resources():
    viewer = Path(__file__).parents[1] / "simkl_mps" / "watch-history-viewer"
    index = (viewer / "index.html").read_text(encoding="utf-8")
    fonts = (viewer / "fonts.css").read_text(encoding="utf-8")
    script = (viewer / "script.js").read_text(encoding="utf-8")

    assert "connect-src 'none'" in index
    assert 'src="http' not in index
    assert 'href="http' not in index.split("<body>", 1)[0]
    assert "http://" not in fonts
    assert "https://" not in fonts
    assert "return OFFLINE_POSTER;" in script


def test_completion_queue_worker_stops_cleanly():
    class EmptyQueue:
        def has_pending_items(self):
            return False

    scrobbler = MediaScrobbler.__new__(MediaScrobbler)
    scrobbler.backlog_cleaner = EmptyQueue()

    scrobbler.start_offline_sync_thread(interval_seconds=60)
    scrobbler.stop_offline_sync_thread(timeout=1)

    assert not scrobbler._offline_sync_thread.is_alive()
