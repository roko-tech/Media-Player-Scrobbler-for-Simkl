import importlib
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

from simkl_mps.backlog_cleaner import BacklogCleaner
from simkl_mps.monitor import Monitor
from simkl_mps import runtime_lock as runtime_lock_module
from simkl_mps.runtime_lock import RuntimeInstanceLock


main_module = importlib.import_module("simkl_mps.main")


class _StubScrobbler:
    def __init__(self):
        self.currently_tracking = None
        self.backlog_requests = 0

    def request_backlog_sync(self):
        self.backlog_requests += 1

    def stop_tracking(self):
        self.currently_tracking = None


def _initialize_runtime_lifecycle(runtime):
    runtime._lifecycle_lock = threading.RLock()
    runtime._starting = False
    runtime._stop_requested = False
    return runtime


def _monitor_without_runtime_dependencies(poll_interval=30):
    monitor = Monitor.__new__(Monitor)
    monitor.app_data_dir = None
    monitor.client_id = None
    monitor.access_token = None
    monitor.poll_interval = poll_interval
    monitor.testing_mode = True
    monitor.running = False
    monitor.monitor_thread = None
    monitor._lock = threading.RLock()
    monitor._lifecycle_lock = threading.RLock()
    monitor._stop_event = None
    monitor.scrobbler = _StubScrobbler()
    monitor.last_backlog_check = time.time()
    monitor.backlog_check_interval = 300
    monitor.search_callback = None
    monitor._last_search_attempts = {}
    monitor.offline_search_cooldown = 60
    monitor._debug_cycles = 0
    monitor.last_known_player_process = None
    monitor.last_known_filepath = None
    return monitor


def test_monitor_stop_interrupts_wait_before_restart(monkeypatch):
    monkeypatch.setattr("simkl_mps.monitor.get_all_windows_info", lambda: [])
    monitor = _monitor_without_runtime_dependencies()

    assert monitor.start() is True
    first_generation = monitor.monitor_thread

    assert monitor.stop() is True
    assert not first_generation.is_alive()

    assert monitor.start() is True
    second_generation = monitor.monitor_thread
    assert second_generation is not first_generation
    assert not first_generation.is_alive()

    assert monitor.stop() is True
    assert not second_generation.is_alive()


def test_monitor_cannot_restart_while_previous_generation_is_still_alive(monkeypatch):
    entered = threading.Event()
    release = threading.Event()

    def blocked_window_scan():
        entered.set()
        release.wait(5)
        return []

    monkeypatch.setattr(
        "simkl_mps.monitor.get_all_windows_info",
        blocked_window_scan,
    )
    monitor = _monitor_without_runtime_dependencies()

    try:
        assert monitor.start() is True
        assert entered.wait(1)
        assert monitor.stop() is False
        assert monitor.start() is False
    finally:
        release.set()
        if monitor.monitor_thread:
            monitor.monitor_thread.join(2)

    monkeypatch.setattr("simkl_mps.monitor.get_all_windows_info", lambda: [])
    assert monitor.start() is True
    assert monitor.stop() is True


def test_runtime_lock_allows_only_one_owner_per_data_store(tmp_path):
    first = RuntimeInstanceLock(tmp_path)
    second = RuntimeInstanceLock(tmp_path)

    assert first.acquire() is True
    assert second.acquire() is False

    first.release()
    assert second.acquire() is True
    second.release()


def test_runtime_lock_excludes_another_process(tmp_path):
    repository_root = Path(__file__).resolve().parents[1]
    child_code = (
        "import sys\n"
        "from simkl_mps.runtime_lock import RuntimeInstanceLock\n"
        f"lock = RuntimeInstanceLock({str(tmp_path)!r})\n"
        "assert lock.acquire()\n"
        "print('locked', flush=True)\n"
        "sys.stdin.readline()\n"
        "lock.release()\n"
    )
    child = subprocess.Popen(
        [sys.executable, "-c", child_code],
        cwd=repository_root,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert child.stdout.readline().strip() == "locked"
        contender = RuntimeInstanceLock(tmp_path)
        assert contender.acquire() is False

        child.stdin.write("\n")
        child.stdin.flush()
        assert child.wait(timeout=10) == 0

        assert contender.acquire() is True
        contender.release()
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=10)


def test_background_service_owns_store_before_constructing_runtime(
    tmp_path,
    monkeypatch,
):
    events = []
    created = []

    class StubRuntime:
        def __init__(self):
            events.append("runtime")
            created.append(self)

        @staticmethod
        def initialize():
            return True

        @staticmethod
        def start():
            return True

    monkeypatch.setattr(main_module, "APP_DATA_DIR", tmp_path)
    monkeypatch.setattr(
        main_module,
        "bootstrap_credentials",
        lambda: events.append("bootstrap") or True,
    )
    monkeypatch.setattr(main_module, "SimklScrobbler", StubRuntime)

    first = main_module.run_as_background_service()
    try:
        assert first is created[0]
        assert events[:2] == ["bootstrap", "runtime"]
        assert main_module.run_as_background_service() is None
        assert len(created) == 1
    finally:
        first._runtime_instance_lock.release()


def test_runtime_constructor_bootstraps_before_persistent_components(monkeypatch):
    events = []

    class StubMonitor:
        def __init__(self, **_kwargs):
            events.append("monitor")

    class StubTraktWatcher:
        def __init__(self):
            events.append("trakt")

    monkeypatch.setattr(
        main_module,
        "bootstrap_credentials",
        lambda: events.append("bootstrap") or True,
    )
    monkeypatch.setattr(main_module, "Monitor", StubMonitor)
    monkeypatch.setattr(main_module, "TraktSyncWatcher", StubTraktWatcher)

    main_module.SimklScrobbler()

    assert events == ["bootstrap", "monitor", "trakt"]


def test_background_runtime_releases_store_ownership_on_stop(tmp_path):
    runtime_lock = RuntimeInstanceLock(tmp_path)
    assert runtime_lock.acquire() is True

    runtime = _initialize_runtime_lifecycle(
        main_module.SimklScrobbler.__new__(main_module.SimklScrobbler)
    )
    runtime.running = True
    runtime.monitor = None
    runtime.trakt_watcher = None
    runtime._runtime_instance_lock = runtime_lock
    runtime.stop()

    contender = RuntimeInstanceLock(tmp_path)
    assert contender.acquire() is True
    contender.release()


def test_background_runtime_retains_ownership_while_worker_is_alive(tmp_path):
    runtime_lock = RuntimeInstanceLock(tmp_path)
    assert runtime_lock.acquire() is True

    class AliveThread:
        @staticmethod
        def is_alive():
            return True

    class StubMediaScrobbler:
        @staticmethod
        def stop_offline_sync_thread():
            return True

    class StubMonitor:
        monitor_thread = AliveThread()
        scrobbler = StubMediaScrobbler()

        @staticmethod
        def stop():
            return False

    runtime = _initialize_runtime_lifecycle(
        main_module.SimklScrobbler.__new__(main_module.SimklScrobbler)
    )
    runtime.running = True
    runtime.monitor = StubMonitor()
    runtime.trakt_watcher = None
    runtime._runtime_instance_lock = runtime_lock
    try:
        assert runtime.stop() is False
        assert runtime._runtime_instance_lock is runtime_lock
        assert RuntimeInstanceLock(tmp_path).acquire() is False
    finally:
        runtime_lock.release()


def test_background_start_failure_retains_ownership_while_cleanup_worker_is_alive(
    tmp_path,
    monkeypatch,
):
    class AliveThread:
        @staticmethod
        def is_alive():
            return True

    class StubMediaScrobbler:
        @staticmethod
        def start_offline_sync_thread():
            return None

        @staticmethod
        def stop_offline_sync_thread():
            return False

    class StubMonitor:
        monitor_thread = AliveThread()
        scrobbler = StubMediaScrobbler()

        @staticmethod
        def start():
            return True

        @staticmethod
        def stop():
            return False

    class FailingTraktWatcher:
        @staticmethod
        def start():
            raise RuntimeError("provider startup failed")

        @staticmethod
        def stop():
            return True

    class PartialRuntime:
        def __init__(self):
            _initialize_runtime_lifecycle(self)
            self.running = False
            self.monitor = StubMonitor()
            self.trakt_watcher = FailingTraktWatcher()

        @staticmethod
        def initialize():
            return True

        start = main_module.SimklScrobbler.start
        stop = main_module.SimklScrobbler.stop
        _start_locked = main_module.SimklScrobbler._start_locked
        _stop_locked = main_module.SimklScrobbler._stop_locked
        _has_live_workers = main_module.SimklScrobbler._has_live_workers
        _worker_is_alive = main_module.SimklScrobbler._worker_is_alive
        _signal_handler = main_module.SimklScrobbler._signal_handler

    monkeypatch.setattr(main_module, "APP_DATA_DIR", tmp_path)
    monkeypatch.setattr(main_module, "bootstrap_credentials", lambda: True)
    monkeypatch.setattr(main_module, "SimklScrobbler", PartialRuntime)
    monkeypatch.setattr(main_module.signal, "signal", lambda *_args: None)

    runtime = main_module.run_as_background_service()
    try:
        assert isinstance(runtime, PartialRuntime)
        assert runtime.running is False
        assert runtime._runtime_instance_lock is not None
        assert RuntimeInstanceLock(tmp_path).acquire() is False
    finally:
        runtime._runtime_instance_lock.release()


def test_reentrant_stop_during_startup_prevents_later_workers(monkeypatch):
    events = []

    class StubMediaScrobbler:
        _offline_sync_thread = None

        @staticmethod
        def start_offline_sync_thread():
            events.append("offline-start")

        @staticmethod
        def stop_offline_sync_thread():
            events.append("offline-stop")
            return True

    class StubMonitor:
        monitor_thread = None
        scrobbler = StubMediaScrobbler()

        def start(self):
            events.append("monitor-start")
            assert runtime.stop() is False
            return True

        @staticmethod
        def stop():
            events.append("monitor-stop")
            return True

    class StubTraktWatcher:
        _thread = None

        @staticmethod
        def start():
            events.append("trakt-start")

        @staticmethod
        def stop():
            events.append("trakt-stop")
            return True

    runtime = _initialize_runtime_lifecycle(
        main_module.SimklScrobbler.__new__(main_module.SimklScrobbler)
    )
    runtime.running = False
    runtime.monitor = StubMonitor()
    runtime.trakt_watcher = StubTraktWatcher()
    monkeypatch.setattr(main_module.signal, "signal", lambda *_args: None)

    assert runtime.start() is False
    assert runtime.running is False
    assert "offline-start" not in events
    assert "trakt-start" not in events
    assert "monitor-stop" in events


def test_provider_baseexception_stops_workers_before_propagating(monkeypatch):
    events = []

    class StubMediaScrobbler:
        _offline_sync_thread = None

        @staticmethod
        def start_offline_sync_thread():
            events.append("offline-start")

        @staticmethod
        def stop_offline_sync_thread():
            events.append("offline-stop")
            return True

    class StubMonitor:
        monitor_thread = None
        scrobbler = StubMediaScrobbler()

        @staticmethod
        def start():
            events.append("monitor-start")
            return True

        @staticmethod
        def stop():
            events.append("monitor-stop")
            return True

    class StubTraktWatcher:
        _thread = None

        @staticmethod
        def start():
            raise SystemExit(5)

        @staticmethod
        def stop():
            events.append("trakt-stop")
            return True

    runtime = _initialize_runtime_lifecycle(
        main_module.SimklScrobbler.__new__(main_module.SimklScrobbler)
    )
    runtime.running = False
    runtime.monitor = StubMonitor()
    runtime.trakt_watcher = StubTraktWatcher()
    monkeypatch.setattr(main_module.signal, "signal", lambda *_args: None)

    try:
        runtime.start()
    except SystemExit as exc:
        assert exc.code == 5
    else:
        raise AssertionError("provider startup did not propagate SystemExit")

    assert runtime.running is False
    assert events == [
        "monitor-start",
        "offline-start",
        "trakt-stop",
        "monitor-stop",
        "offline-stop",
    ]


def test_restart_is_rejected_while_any_prior_worker_is_alive(monkeypatch):
    class AliveThread:
        @staticmethod
        def is_alive():
            return True

    class StubMediaScrobbler:
        _offline_sync_thread = None

    class StubMonitor:
        monitor_thread = None
        scrobbler = StubMediaScrobbler()

        @staticmethod
        def start():
            raise AssertionError("monitor restarted over a live prior worker")

    class StubTraktWatcher:
        _thread = None

    runtime = _initialize_runtime_lifecycle(
        main_module.SimklScrobbler.__new__(main_module.SimklScrobbler)
    )
    runtime.running = False
    runtime.monitor = StubMonitor()
    runtime.trakt_watcher = StubTraktWatcher()
    monkeypatch.setattr(main_module.signal, "signal", lambda *_args: None)

    runtime.monitor.scrobbler._offline_sync_thread = AliveThread()
    assert runtime.start() is False

    runtime.monitor.scrobbler._offline_sync_thread = None
    runtime.trakt_watcher._thread = AliveThread()
    assert runtime.start() is False


def test_background_baseexception_retains_ownership_for_live_worker(
    tmp_path,
    monkeypatch,
):
    created = []

    class PartialRuntime:
        def __init__(self):
            created.append(self)

        @staticmethod
        def initialize():
            return True

        @staticmethod
        def start():
            raise SystemExit(2)

        @staticmethod
        def stop():
            return False

    monkeypatch.setattr(main_module, "APP_DATA_DIR", tmp_path)
    monkeypatch.setattr(main_module, "bootstrap_credentials", lambda: True)
    monkeypatch.setattr(main_module, "SimklScrobbler", PartialRuntime)

    try:
        main_module.run_as_background_service()
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("background startup did not propagate SystemExit")

    runtime = created[0]
    try:
        assert runtime._runtime_instance_lock is not None
        assert RuntimeInstanceLock(tmp_path).acquire() is False
    finally:
        runtime._runtime_instance_lock.release()
        runtime_lock_module._RETAINED_FAILED_RUNTIMES.remove(runtime)


def test_foreground_retains_ownership_when_shutdown_times_out(tmp_path, monkeypatch):
    created = []

    class PartialRuntime:
        running = False

        def __init__(self):
            created.append(self)

        @staticmethod
        def initialize():
            return True

        @staticmethod
        def start():
            return True

        @staticmethod
        def stop():
            return False

    monkeypatch.setattr(main_module, "APP_DATA_DIR", tmp_path)
    monkeypatch.setattr(main_module, "bootstrap_credentials", lambda: True)
    monkeypatch.setattr(main_module, "SimklScrobbler", PartialRuntime)

    assert main_module.main() == 1
    runtime = created[0]
    try:
        assert runtime._runtime_instance_lock is not None
        assert RuntimeInstanceLock(tmp_path).acquire() is False
    finally:
        runtime._runtime_instance_lock.release()
        runtime_lock_module._RETAINED_FAILED_RUNTIMES.remove(runtime)


def test_foreground_baseexception_retains_ownership_for_live_worker(
    tmp_path,
    monkeypatch,
):
    created = []

    class PartialRuntime:
        def __init__(self):
            created.append(self)

        @staticmethod
        def initialize():
            return True

        @staticmethod
        def start():
            raise SystemExit(3)

        @staticmethod
        def stop():
            return False

    monkeypatch.setattr(main_module, "APP_DATA_DIR", tmp_path)
    monkeypatch.setattr(main_module, "bootstrap_credentials", lambda: True)
    monkeypatch.setattr(main_module, "SimklScrobbler", PartialRuntime)

    try:
        main_module.main()
    except SystemExit as exc:
        assert exc.code == 3
    else:
        raise AssertionError("foreground startup did not propagate SystemExit")

    runtime = created[0]
    try:
        assert runtime._runtime_instance_lock is not None
        assert RuntimeInstanceLock(tmp_path).acquire() is False
    finally:
        runtime._runtime_instance_lock.release()
        runtime_lock_module._RETAINED_FAILED_RUNTIMES.remove(runtime)


def test_tray_fault_does_not_start_fallback_over_live_runtime(monkeypatch):
    fallback_starts = []
    platforms = (
        ("simkl_mps.tray_win", "TrayAppWin"),
        ("simkl_mps.tray_linux", "TrayAppLinux"),
        ("simkl_mps.tray_mac", "TrayAppMac"),
    )

    class UnexpectedFallback:
        def __init__(self):
            fallback_starts.append(True)

    monkeypatch.setattr(main_module, "SimklScrobbler", UnexpectedFallback)

    for module_name, class_name in platforms:
        platform_module = importlib.import_module(module_name)
        stop_calls = []
        live_runtime = SimpleNamespace(
            stop=lambda: stop_calls.append(True) or False
        )

        class FailingTray:
            monitoring_active = True
            scrobbler = live_runtime

            @staticmethod
            def run():
                raise RuntimeError("tray setup failed after runtime start")

        monkeypatch.setattr(platform_module, class_name, FailingTray)
        if module_name.endswith("tray_win"):
            monkeypatch.setattr(
                platform_module,
                "_acquire_single_instance",
                lambda: True,
            )
            monkeypatch.setattr(
                platform_module,
                "_release_single_instance",
                lambda: None,
            )

        exit_code, retained_runtime = platform_module._run_owned_tray_app()

        assert exit_code == 1
        assert retained_runtime is live_runtime
        assert stop_calls == [True]

    assert fallback_starts == []


def test_tray_baseexception_retains_ownership_for_live_runtime(
    tmp_path,
    monkeypatch,
):
    platforms = (
        ("simkl_mps.tray_win", "TrayAppWin"),
        ("simkl_mps.tray_linux", "TrayAppLinux"),
        ("simkl_mps.tray_mac", "TrayAppMac"),
    )

    for index, (module_name, class_name) in enumerate(platforms):
        platform_module = importlib.import_module(module_name)
        app_data_dir = tmp_path / str(index)
        live_runtime = SimpleNamespace(stop=lambda: False)

        class InterruptedTray:
            scrobbler = live_runtime

            @staticmethod
            def run():
                raise SystemExit(4)

        monkeypatch.setattr(platform_module, class_name, InterruptedTray)
        monkeypatch.setattr(
            platform_module,
            "bootstrap_credentials",
            lambda: True,
        )
        if module_name.endswith("tray_win"):
            monkeypatch.setattr(platform_module, "APP_DATA_DIR", app_data_dir)
            monkeypatch.setattr(
                platform_module,
                "_acquire_single_instance",
                lambda: True,
            )
            monkeypatch.setattr(
                platform_module,
                "_release_single_instance",
                lambda: None,
            )
        else:
            monkeypatch.setattr(
                platform_module,
                "get_app_data_dir",
                lambda path=app_data_dir: path,
            )

        assert platform_module.run_tray_app() == 1
        try:
            assert live_runtime._runtime_instance_lock is not None
            assert RuntimeInstanceLock(app_data_dir).acquire() is False
        finally:
            live_runtime._runtime_instance_lock.release()
            runtime_lock_module._RETAINED_FAILED_RUNTIMES.remove(live_runtime)


def test_tray_wrappers_retain_store_lock_for_failed_runtime(tmp_path, monkeypatch):
    platforms = (
        "simkl_mps.tray_win",
        "simkl_mps.tray_linux",
        "simkl_mps.tray_mac",
    )

    for index, module_name in enumerate(platforms):
        platform_module = importlib.import_module(module_name)
        app_data_dir = tmp_path / str(index)
        failed_runtime = SimpleNamespace()
        monkeypatch.setattr(
            platform_module,
            "_run_owned_tray_app",
            lambda runtime=failed_runtime: (1, runtime),
        )
        monkeypatch.setattr(
            platform_module,
            "bootstrap_credentials",
            lambda: True,
        )
        if module_name.endswith("tray_win"):
            monkeypatch.setattr(platform_module, "APP_DATA_DIR", app_data_dir)
        else:
            monkeypatch.setattr(
                platform_module,
                "get_app_data_dir",
                lambda path=app_data_dir: path,
            )

        assert platform_module.run_tray_app() == 1
        try:
            assert failed_runtime._runtime_instance_lock is not None
            assert RuntimeInstanceLock(app_data_dir).acquire() is False
        finally:
            failed_runtime._runtime_instance_lock.release()
            runtime_lock_module._RETAINED_FAILED_RUNTIMES.remove(failed_runtime)


def test_completion_claim_prevents_duplicate_workers_and_recovers_after_expiry(
    tmp_path,
    monkeypatch,
):
    clock = {"now": 1_000.0}
    monkeypatch.setattr(
        "simkl_mps.backlog_cleaner.time.time",
        lambda: clock["now"],
    )
    first = BacklogCleaner(tmp_path)
    second = BacklogCleaner(tmp_path)
    event_id = first.add(100, "Example", unique_event=True)

    assert first.claim_event(event_id, "worker-one", lease_seconds=30) is True
    assert second.claim_event(event_id, "worker-two", lease_seconds=30) is False

    clock["now"] += 31
    assert second.claim_event(event_id, "worker-two", lease_seconds=30) is True
    assert first.release_event_claim(event_id, "worker-one") is False
    assert second.release_event_claim(event_id, "worker-two") is True


def test_existing_completion_ledger_is_migrated_for_event_claims(tmp_path):
    database_file = tmp_path / "completion_ledger.sqlite3"
    with sqlite3.connect(database_file) as connection:
        connection.execute(
            """
            CREATE TABLE completion_events (
                event_id TEXT PRIMARY KEY,
                payload_json TEXT NOT NULL,
                state TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                attempt_count INTEGER NOT NULL DEFAULT 0,
                last_attempt_timestamp REAL,
                last_error TEXT
            )
            """
        )

    ledger = BacklogCleaner(tmp_path)
    with sqlite3.connect(database_file) as connection:
        columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(completion_events)"
            ).fetchall()
        }
    assert {"claim_owner", "claim_expires_at"} <= columns

    event_id = ledger.add(100, "Example", unique_event=True)
    assert ledger.claim_event(event_id, "worker") is True
