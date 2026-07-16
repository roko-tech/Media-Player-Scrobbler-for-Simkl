"""Persistent exact-file and folder media identification overrides."""

import json
import logging
import os
from pathlib import Path


logger = logging.getLogger(__name__)
OVERRIDE_VERSION = 1


class MediaOverrides:
    def __init__(self, app_data_dir, filename="media_overrides.json"):
        self.path = Path(app_data_dir) / filename
        self.data = self._load()

    @staticmethod
    def _normalize(path):
        return os.path.normcase(os.path.abspath(os.path.normpath(str(path)))).casefold()

    def _load(self):
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if data.get("version") == OVERRIDE_VERSION:
                return {
                    "version": OVERRIDE_VERSION,
                    "files": data.get("files", {}),
                    "folders": data.get("folders", {}),
                }
        except FileNotFoundError:
            pass
        except (OSError, ValueError, AttributeError) as exc:
            logger.warning("Could not load media overrides: %s", exc)
        return {"version": OVERRIDE_VERSION, "files": {}, "folders": {}}

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
        self.data[collection][self._normalize(path)] = entry
        self._save()

    def find(self, filepath):
        normalized_file = self._normalize(filepath)
        exact = self.data["files"].get(normalized_file)
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
        return None

    def remove(self, scope, path):
        collection = "files" if scope == "file" else "folders"
        removed = self.data[collection].pop(self._normalize(path), None)
        if removed is not None:
            self._save()
            return True
        return False

    def remove_match(self, filepath):
        match = self.find(filepath)
        if not match:
            return None
        collection = "files" if match["scope"] == "file" else "folders"
        self.data[collection].pop(match["path"], None)
        self._save()
        return match
