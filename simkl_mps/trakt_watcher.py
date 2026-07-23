"""Background trigger that keeps Trakt current from local watch history."""

import logging
import threading
import time

from simkl_mps import trakt_sync


logger = logging.getLogger(__name__)


class TraktSyncWatcher:
    def __init__(self, poll_seconds=10, debounce_seconds=5, retry_seconds=120):
        self.poll_seconds = poll_seconds
        self.debounce_seconds = debounce_seconds
        self.retry_seconds = retry_seconds
        self._stop = threading.Event()
        self._history_saved = threading.Event()
        self._sync_lock = threading.Lock()
        self._thread = None
        self._result_callback = None
        self._last_emitted_receipt_key = None
        self.last_summary = "not configured"

    @property
    def configured(self):
        return trakt_sync.CONFIG_FILE.exists() and trakt_sync.TOKEN_FILE.exists()

    @property
    def running(self):
        return bool(self._thread and self._thread.is_alive())

    @staticmethod
    def _format_time(value):
        if not value:
            return "never"
        try:
            return trakt_sync.parse_dt(value).strftime("%Y-%m-%d %H:%M:%S UTC")
        except (TypeError, ValueError):
            return "unknown"

    @staticmethod
    def _media_label(event, include_title):
        if not event:
            return "none"
        title = event.get("title") if include_title else None
        if event.get("kind") == "movie":
            return title or "movie"
        episode = trakt_sync._int(event.get("episode"))
        if event.get("is_anime"):
            suffix = f"E{episode:02d}" if episode else "episode"
        else:
            season = trakt_sync._int(event.get("season"))
            suffix = f"S{season:02d}E{episode:02d}" if season and episode else "episode"
        return f"{title} - {suffix}" if title else suffix

    def health_report(self, include_title=True):
        """Build a local panel or a redacted, shareable diagnostic report."""
        try:
            snapshot = trakt_sync.get_sync_health()
        except trakt_sync.TraktSyncError as exc:
            logger.warning("Could not read Trakt sync health: %s", exc)
            snapshot = {"latest_event": None, "pending": 0, "health": {}}

        latest = snapshot.get("latest_event")
        simkl_pending = snapshot.get("simkl_pending", 0)
        health = snapshot.get("health") or {}
        last_ok = health.get("last_ok")
        trakt_status = "not run" if last_ok is None else ("OK" if last_ok else "ERROR")
        if simkl_pending:
            simkl_status = "pending retry"
        else:
            simkl_status = "accepted" if latest else "not run"
        lines = [
            "MEDIA SYNC HEALTH",
            "",
            f"Watcher: {'running' if self.running else 'stopped'}",
            f"Configuration: {'ready' if self.configured else 'not configured'}",
            "",
            "SIMKL",
            f"Status: {simkl_status}",
            f"Latest: {self._media_label(latest, include_title)}",
            f"Completed: {self._format_time(latest.get('watched_at') if latest else None)}",
            f"Pending retries: {simkl_pending}",
            "",
            "TRAKT",
            f"Status: {trakt_status}",
        ]
        if include_title:
            lines.append(f"Last result: {health.get('last_summary') or self.last_summary}")
        if health.get("last_http_status") is not None:
            lines.extend(
                [
                    f"Last response: HTTP {health['last_http_status']}",
                    "Added: "
                    f"{health.get('last_added_episodes', 0)} episode(s), "
                    f"{health.get('last_added_movies', 0)} movie(s)",
                    f"Not found: {health.get('last_not_found', 0)}",
                ]
            )
        if health.get("last_retry_after"):
            lines.append(f"Retry after: {health['last_retry_after']} second(s)")
        lines.extend(
            [
                f"Pending retries: {snapshot.get('pending', 0)}",
                f"Last success: {self._format_time(health.get('last_success_at'))}",
                f"Last attempt: {self._format_time(health.get('last_attempt_at'))}",
            ]
        )
        return "\n".join(lines)

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
        self._history_saved.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=self.poll_seconds + 1)
        logger.info("Trakt sync watcher stopped")

    def notify_history_saved(self):
        """Wake the watcher after a completed-watch event is safely on disk."""
        self._history_saved.set()

    def set_result_callback(self, callback):
        """Set a callback that receives the exact Trakt result and latest event."""
        self._result_callback = callback

    def _latest_event(self):
        history = trakt_sync.load_json(trakt_sync.HISTORY_FILE, []) or []
        events = trakt_sync.collect_history_events(history, None)
        return events[-1] if events else None

    def _emit_result(self, result):
        if not self._result_callback:
            return
        event = self._latest_event()
        if not event:
            return
        receipt_key = (
            event.get("kind"),
            event.get("simkl_id"),
            event.get("season"),
            event.get("episode"),
            event.get("watched_at"),
            bool(result.ok and result.pending == 0),
        )
        if receipt_key == self._last_emitted_receipt_key:
            logger.debug("Trakt sync receipt unchanged; suppressing duplicate overlay")
            return
        try:
            self._result_callback(result, event)
        except Exception:
            logger.exception("Trakt sync result callback failed")
        else:
            self._last_emitted_receipt_key = receipt_key

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

    def dismiss_pending_events(self):
        """Serialize pending-event dismissal against any active Trakt sync."""
        with self._sync_lock:
            return trakt_sync.dismiss_pending_events()

    def _mtime(self):
        try:
            if trakt_sync.HISTORY_FILE.exists():
                return trakt_sync.HISTORY_FILE.stat().st_mtime
            return 0.0
        except OSError:
            return None

    def _retry_delay(self, result):
        return max(self.retry_seconds, int(result.retry_after or 0))

    def _next_retry(self, result):
        if result.ok and not result.pending:
            return None
        return time.monotonic() + self._retry_delay(result)

    def _watch_loop(self):
        last_mtime = self._mtime() or 0.0
        result = self.sync_now()  # catch up and retry pending events after startup
        next_retry = self._next_retry(result)
        while not self._stop.is_set():
            wait_seconds = self.poll_seconds
            if next_retry is not None:
                wait_seconds = min(wait_seconds, max(0.0, next_retry - time.monotonic()))
            notified = self._history_saved.wait(wait_seconds)
            if self._stop.is_set():
                return
            if notified:
                self._history_saved.clear()
                last_mtime = self._mtime() or last_mtime
                logger.info("Trakt sync: completed-watch event saved; syncing now")
                result = self.sync_now()
                self._emit_result(result)
                next_retry = self._next_retry(result)
                continue

            current_mtime = self._mtime()
            if current_mtime is not None and current_mtime != last_mtime:
                if self._stop.wait(self.debounce_seconds):
                    return
                last_mtime = self._mtime() or current_mtime
                logger.info("Trakt sync: watch_history.json changed; syncing exact local events")
                result = self.sync_now()
                self._emit_result(result)
                next_retry = self._next_retry(result)
                continue

            if next_retry is not None and time.monotonic() >= next_retry:
                logger.info("Trakt sync: retrying a failed or pending sync without a new history change")
                result = self.sync_now()
                next_retry = self._next_retry(result)
