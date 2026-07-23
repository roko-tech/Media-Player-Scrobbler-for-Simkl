"""Cross-platform ownership lock for one simkl-mps runtime per data store."""

import os
from pathlib import Path


_RETAINED_FAILED_RUNTIMES = []


class RuntimeInstanceLock:
    """Hold an advisory lock for the lifetime of one application runtime."""

    def __init__(self, app_data_dir, filename=".runtime.lock"):
        self.path = Path(app_data_dir) / filename
        self._handle = None

    @staticmethod
    def _lock(handle):
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            return

        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    @staticmethod
    def _unlock(handle):
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            return

        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def acquire(self):
        if self._handle is not None:
            return True

        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+b")
        if handle.seek(0, os.SEEK_END) == 0:
            handle.write(b"\0")
            handle.flush()

        try:
            self._lock(handle)
        except OSError:
            handle.close()
            return False

        self._handle = handle
        return True

    def release(self):
        handle = self._handle
        if handle is None:
            return
        self._handle = None
        try:
            self._unlock(handle)
        finally:
            handle.close()

    def __enter__(self):
        return self.acquire()

    def __exit__(self, exc_type, exc_value, traceback):
        self.release()


def retain_failed_runtime(runtime, runtime_lock):
    """Keep fail-closed ownership when a runtime worker cannot be joined."""
    runtime._runtime_instance_lock = runtime_lock
    _RETAINED_FAILED_RUNTIMES.append(runtime)
