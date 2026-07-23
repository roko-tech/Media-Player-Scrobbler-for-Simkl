"""Persistent exact-file and folder media identification overrides."""

import json
import logging
import os
from pathlib import Path

from simkl_mps.media_identity import normalize_media_path


logger = logging.getLogger(__name__)
OVERRIDE_VERSION = 2
_LEGACY_FILES = "legacy_casefold_files"
_LEGACY_FOLDERS = "legacy_casefold_folders"


class MediaOverrides:
    def __init__(self, app_data_dir, filename="media_overrides.json"):
        self.path = Path(app_data_dir) / filename
        self._migration_pending = False
        self.data = self._load()
        if self._migration_pending:
            self._save()

    @staticmethod
    def _normalize(path):
        return normalize_media_path(path)

    def _load(self):
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            version = data.get("version")
            if version in {1, OVERRIDE_VERSION}:
                files = data.get("files", {})
                folders = data.get("folders", {})
                if not isinstance(files, dict) or not isinstance(folders, dict):
                    raise ValueError("Invalid media override collections")
                if version == 1:
                    self._migration_pending = True
                    legacy_files = sorted(files)
                    legacy_folders = sorted(folders)
                else:
                    legacy_files = [
                        key
                        for key in data.get(_LEGACY_FILES, [])
                        if key in files
                    ]
                    legacy_folders = [
                        key
                        for key in data.get(_LEGACY_FOLDERS, [])
                        if key in folders
                    ]
                return {
                    "version": OVERRIDE_VERSION,
                    "files": files,
                    "folders": folders,
                    _LEGACY_FILES: legacy_files,
                    _LEGACY_FOLDERS: legacy_folders,
                }
        except FileNotFoundError:
            pass
        except (OSError, ValueError, AttributeError) as exc:
            logger.warning("Could not load media overrides: %s", exc)
        return {
            "version": OVERRIDE_VERSION,
            "files": {},
            "folders": {},
            _LEGACY_FILES: [],
            _LEGACY_FOLDERS: [],
        }

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        temp_path.write_text(json.dumps(self.data, indent=2), encoding="utf-8")
        temp_path.replace(self.path)

    def set(self, scope, path, simkl_id, season=None, title=None, media_type=None):
        if scope not in {"file", "folder"}:
            raise ValueError("Override scope must be 'file' or 'folder'.")
        entry = {"simkl_id": int(simkl_id)}
        if season is not None:
            entry["season"] = int(season)
        if title:
            entry["title"] = str(title)
        if media_type:
            entry["media_type"] = str(media_type)
        collection = "files" if scope == "file" else "folders"
        legacy_collection = _LEGACY_FILES if scope == "file" else _LEGACY_FOLDERS
        normalized = self._normalize(path)
        legacy_key = normalized.casefold()
        if legacy_key in self.data[legacy_collection]:
            self.data[collection].pop(legacy_key, None)
            self.data[legacy_collection].remove(legacy_key)
        self.data[collection][normalized] = entry
        self._save()

    def find(self, filepath):
        normalized_file = self._normalize(filepath)
        exact = self.data["files"].get(normalized_file)
        if not exact and os.name != "nt":
            legacy_key = normalized_file.casefold()
            if legacy_key in self.data[_LEGACY_FILES]:
                exact = self.data["files"].get(legacy_key)
                normalized_file = legacy_key
        if exact:
            return {**exact, "scope": "file", "path": normalized_file}

        directory = self._normalize(os.path.dirname(filepath))
        for folder in sorted(self.data["folders"], key=len, reverse=True):
            prefix = folder.rstrip(os.sep) + os.sep
            if directory == folder or directory.startswith(prefix):
                return {
                    **self.data["folders"][folder],
                    "scope": "folder",
                    "path": folder,
                }
        if os.name != "nt":
            folded_directory = directory.casefold()
            for folder in sorted(
                self.data[_LEGACY_FOLDERS],
                key=len,
                reverse=True,
            ):
                prefix = folder.rstrip(os.sep) + os.sep
                if folded_directory == folder or folded_directory.startswith(prefix):
                    return {
                        **self.data["folders"][folder],
                        "scope": "folder",
                        "path": folder,
                    }
        return None

    def remove(self, scope, path):
        collection = "files" if scope == "file" else "folders"
        legacy_collection = _LEGACY_FILES if scope == "file" else _LEGACY_FOLDERS
        normalized = self._normalize(path)
        removed = self.data[collection].pop(normalized, None)
        if removed is None and os.name != "nt":
            legacy_key = normalized.casefold()
            if legacy_key in self.data[legacy_collection]:
                removed = self.data[collection].pop(legacy_key, None)
                self.data[legacy_collection].remove(legacy_key)
        if removed is not None:
            self._save()
            return True
        return False

    def remove_match(self, filepath):
        match = self.find(filepath)
        if not match:
            return None
        collection = "files" if match["scope"] == "file" else "folders"
        legacy_collection = (
            _LEGACY_FILES if match["scope"] == "file" else _LEGACY_FOLDERS
        )
        self.data[collection].pop(match["path"], None)
        if match["path"] in self.data[legacy_collection]:
            self.data[legacy_collection].remove(match["path"])
        self._save()
        return match
