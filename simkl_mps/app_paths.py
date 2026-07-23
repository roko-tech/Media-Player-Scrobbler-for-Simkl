"""Explicit ownership manifest for application-created local data."""

from dataclasses import dataclass
from pathlib import Path
import shutil


_GROUP_PATTERNS = {
    "configuration": (
        ".simkl_mps.env*",
        ".env*",
        "settings.json*",
        "trakt_config.json*",
        "trakt_token.json*",
        "trakt_sync_state.json*",
        ".migration-v2-complete.json*",
        ".first_run_complete",
        "first_run",
    ),
    "identity": (
        "media_cache.json*",
        "media_overrides.json*",
        "anime-list-full.json*",
    ),
    "completion": (
        "backlog.json*",
        "completion_ledger.sqlite3*",
    ),
    "history": (
        "watch_history.json*",
    ),
    "logs": (
        "simkl_mps.log*",
        "playback_log.jsonl*",
        "*updater*.log*",
    ),
}

_GROUP_DIRECTORIES = {
    "configuration": (".migration-conflicts",),
    "history": ("watch-history-viewer",),
}


@dataclass(frozen=True)
class PurgeResult:
    removed: tuple[Path, ...]
    retained_empty: tuple[Path, ...]
    failed: tuple[tuple[Path, str], ...]
    remaining: tuple[Path, ...]

    @property
    def success(self):
        return not self.failed and not self.remaining


class AppPathManifest:
    """Enumerate and purge only paths beneath one owned application-data root."""

    def __init__(self, root):
        self.root = Path(root).resolve()

    def _assert_owned(self, path):
        candidate = Path(path).resolve(strict=False)
        if candidate == self.root or not candidate.is_relative_to(self.root):
            raise ValueError(f"Refusing non-owned path: {candidate}")
        return candidate

    def owned_paths(self, groups=None):
        selected = set(groups or _GROUP_PATTERNS)
        paths = set()
        for group in selected:
            for pattern in _GROUP_PATTERNS.get(group, ()):
                for path in self.root.glob(pattern):
                    paths.add(self._assert_owned(path))
            for directory in _GROUP_DIRECTORIES.get(group, ()):
                path = self._assert_owned(self.root / directory)
                if path.exists():
                    paths.add(path)
        return tuple(
            sorted(
                paths,
                key=lambda path: (len(path.parts), str(path).casefold()),
                reverse=True,
            )
        )

    def purge(self, groups=None):
        removed = []
        retained_empty = []
        failed = []
        log_paths = set(self.owned_paths(("logs",)))

        for path in self.owned_paths(groups):
            try:
                if path.is_dir():
                    shutil.rmtree(path)
                else:
                    path.unlink()
                removed.append(path)
            except (OSError, PermissionError) as purge_exc:
                error = purge_exc
                if path in log_paths and path.is_file():
                    try:
                        path.write_text("", encoding="utf-8")
                        retained_empty.append(path)
                        continue
                    except OSError as truncate_exc:
                        error = truncate_exc
                failed.append((path, str(error)))

        retained = set(retained_empty)
        remaining = tuple(
            path
            for path in self.owned_paths(groups)
            if path not in retained
        )
        return PurgeResult(
            removed=tuple(removed),
            retained_empty=tuple(retained_empty),
            failed=tuple(failed),
            remaining=remaining,
        )
