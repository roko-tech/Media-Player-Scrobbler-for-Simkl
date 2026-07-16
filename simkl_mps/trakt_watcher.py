"""Background trigger that keeps Trakt current from local watch history."""

import logging
import threading

from simkl_mps import trakt_sync


logger = logging.getLogger(__name__)


class TraktSyncWatcher:
    def __init__(self, poll_seconds=10, debounce_seconds=5):
        self.poll_seconds = poll_seconds
        self.debounce_seconds = debounce_seconds
        self._stop = threading.Event()
        self._sync_lock = threading.Lock()
        self._thread = None
        self.last_summary = "not configured"

    @property
    def configured(self):
        return trakt_sync.CONFIG_FILE.exists() and trakt_sync.TOKEN_FILE.exists()

    def start(self):
        if self._thread and self._thread.is_alive():
            return True
        if not self.configured:
            logger.info(
                "Trakt sync disabled: add %s and authorize with 'simkl-mps trakt-auth'",
                trakt_sync.CONFIG_FILE,
            )
            self.last_summary = "not configured"
            return False
        trakt_sync.ensure_state()
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._watch_loop, name="trakt-sync-watcher", daemon=True
        )
        self._thread.start()
        self.last_summary = "watching"
        logger.info("Trakt sync watcher started")
        return True

    def stop(self):
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=self.poll_seconds + 1)
        logger.info("Trakt sync watcher stopped")

    def sync_now(self):
        if not self.configured:
            self.last_summary = "not configured"
            return trakt_sync.SyncResult(False, "Trakt is not configured.")
        if not self._sync_lock.acquire(blocking=False):
            return trakt_sync.SyncResult(False, "Trakt sync is already running.")
        try:
            result = trakt_sync.sync_history()
            self.last_summary = result.summary
            return result
        except Exception as exc:
            self.last_summary = f"error: {exc}"
            logger.exception("Trakt sync failed: %s", exc)
            return trakt_sync.SyncResult(False, self.last_summary)
        finally:
            self._sync_lock.release()

    def _mtime(self):
        try:
            if trakt_sync.HISTORY_FILE.exists():
                return trakt_sync.HISTORY_FILE.stat().st_mtime
            return 0.0
        except OSError:
            return None

    def _watch_loop(self):
        last_mtime = self._mtime() or 0.0
        self.sync_now()  # catch up and retry pending events after startup
        while not self._stop.wait(self.poll_seconds):
            current_mtime = self._mtime()
            if current_mtime is None or current_mtime == last_mtime:
                continue
            if self._stop.wait(self.debounce_seconds):
                return
            last_mtime = self._mtime() or current_mtime
            logger.info("Trakt sync: watch_history.json changed; syncing exact local events")
            self.sync_now()
