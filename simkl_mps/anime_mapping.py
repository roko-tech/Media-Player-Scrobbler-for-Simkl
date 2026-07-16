"""Offline anime ID / season mapping via Fribb/anime-lists.

Gives a deterministic (tvdb_id, tvdb_season) <-> simkl_id map derived from the
same data Sonarr uses, so split-cours anime (each cour a separate Simkl title but
one combined TVDB series on Trakt) resolve without API guessing or relation
walks. The dataset is cached in the app-data dir and refreshed weekly; every
lookup returns None if the data isn't available yet, so callers can fall back.

Loading happens on a background thread so the first anime scrobble never blocks
on the ~17 MB download; until it's ready, lookups return None.
"""

import json
import logging
import threading
import time
from pathlib import Path

import requests

try:
    from .migration import get_app_data_dir
except Exception:  # pragma: no cover - migration always present in the app
    def get_app_data_dir():
        return Path(".")

logger = logging.getLogger(__name__)

_URL = "https://raw.githubusercontent.com/Fribb/anime-lists/master/anime-list-full.json"
_MAX_AGE = 7 * 24 * 3600  # refresh weekly
_lock = threading.Lock()
_indices = None      # (by_tvdb_season, by_simkl) once built
_loading = False


def _cache_path():
    try:
        return get_app_data_dir() / "anime-list-full.json"
    except Exception:
        return Path("anime-list-full.json")


def _download(path):
    try:
        r = requests.get(_URL, timeout=60)
        r.raise_for_status()
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(r.content)
        tmp.replace(path)
        logger.info(f"anime-lists: downloaded {len(r.content) // 1024} KB to {path}")
        return True
    except Exception as e:
        logger.warning(f"anime-lists: download failed: {e}")
        return False


def _ensure_data():
    path = _cache_path()
    fresh = path.exists() and (time.time() - path.stat().st_mtime) < _MAX_AGE
    if not fresh:
        _download(path)  # best-effort; a stale copy is kept on failure
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning(f"anime-lists: parse failed: {e}")
    return None


def _build_indices(data):
    by_tvdb_season, by_simkl = {}, {}
    for e in (data or []):
        tvdb, simkl = e.get("tvdb_id"), e.get("simkl_id")
        season = (e.get("season") or {}).get("tvdb")
        if tvdb is None or season is None or not simkl:
            continue
        key = (int(tvdb), int(season))
        # prefer a TV entry over OVA/movie when a (tvdb, season) collides
        if key not in by_tvdb_season or e.get("type") == "TV":
            by_tvdb_season[key] = int(simkl)
        by_simkl.setdefault(int(simkl), (int(tvdb), int(season)))
    return by_tvdb_season, by_simkl


def _log_index_size(indices):
    logger.info(
        "anime-lists: indexed %s (tvdb,season) keys, %s simkl ids",
        len(indices[0]),
        len(indices[1]),
    )


def _load_worker():
    global _indices, _loading
    try:
        indices = _build_indices(_ensure_data())
        if indices[0] or indices[1]:
            _indices = indices
            _log_index_size(indices)
    finally:
        _loading = False


def _get():
    """Return cached indices immediately; refresh stale data in the background."""
    global _indices, _loading
    if _indices is None:
        with _lock:
            path = _cache_path()
            if _indices is None and path.exists():
                try:
                    cached = _build_indices(json.loads(path.read_text(encoding="utf-8")))
                    if cached[0] or cached[1]:
                        _indices = cached
                        _log_index_size(cached)
                except (OSError, ValueError, TypeError) as exc:
                    logger.warning("anime-lists: cached map unavailable: %s", exc)
            stale = not path.exists() or (time.time() - path.stat().st_mtime) >= _MAX_AGE
            if stale and not _loading:
                _loading = True
                threading.Thread(target=_load_worker, name="anime-lists-load",
                                 daemon=True).start()
    return _indices


def simkl_to_tvdb_season(simkl_id):
    idx = _get()
    if not idx:
        return None
    try:
        return idx[1].get(int(simkl_id))
    except (TypeError, ValueError):
        return None


def tvdb_season_to_simkl(tvdb_id, season):
    idx = _get()
    if not idx:
        return None
    try:
        return idx[0].get((int(tvdb_id), int(season)))
    except (TypeError, ValueError):
        return None


def resolve_split_season(base_simkl_id, file_season):
    """Given the base show's Simkl id and the filename's TVDB season, return the
    Simkl id of the entry that owns that TVDB season (or None)."""
    ts = simkl_to_tvdb_season(base_simkl_id)
    if not ts:
        return None
    return tvdb_season_to_simkl(ts[0], file_season)
