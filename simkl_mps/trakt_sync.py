"""Push local simkl-mps watch events to Trakt.

Simkl remains the first tracker.  Once simkl-mps records a successful watch in
``watch_history.json``, this module sends that exact event to Trakt.  Unmatched
events are retained in the app-data state file and retried later.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from simkl_mps.config_manager import get_app_data_dir
from simkl_mps.credentials import get_credentials
from simkl_mps.secure_store import is_protected, protect_secret, unprotect_secret


logger = logging.getLogger(__name__)

TRAKT_API = "https://api.trakt.tv"
SIMKL_API = "https://api.simkl.com"

APP_DATA_DIR = get_app_data_dir()
CONFIG_FILE = APP_DATA_DIR / "trakt_config.json"
TOKEN_FILE = APP_DATA_DIR / "trakt_token.json"
STATE_FILE = APP_DATA_DIR / "trakt_sync_state.json"
HISTORY_FILE = APP_DATA_DIR / "watch_history.json"
SIMKL_BACKLOG_FILE = APP_DATA_DIR / "backlog.json"
FRIBB_FILE = APP_DATA_DIR / "anime-list-full.json"


class TraktSyncError(RuntimeError):
    """A recoverable Trakt setup, authentication, or network failure."""


@dataclass(frozen=True)
class SyncResult:
    ok: bool
    summary: str
    pushed: bool = False
    pending: int = 0
    retry_after: int = 0


def load_json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default
    except (OSError, json.JSONDecodeError) as exc:
        raise TraktSyncError(f"Could not read {path.name}: {exc}") from exc


def save_json(path: Path, data: Any):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    temp.replace(path)


def save_state(data, create_backup=True):
    backup = STATE_FILE.with_suffix(STATE_FILE.suffix + ".bak")
    if create_backup and STATE_FILE.exists():
        try:
            current = load_json(STATE_FILE)
            if isinstance(current, dict):
                backup_temp = backup.with_suffix(backup.suffix + ".tmp")
                shutil.copy2(STATE_FILE, backup_temp)
                backup_temp.replace(backup)
        except TraktSyncError:
            logger.warning("Trakt sync: skipped backup of an invalid state file")
    save_json(STATE_FILE, data)


def load_state(default=None):
    try:
        state = load_json(STATE_FILE, default)
        if state is not None and not isinstance(state, dict):
            raise TraktSyncError("Trakt state must be a JSON object")
        return state
    except TraktSyncError as exc:
        logger.error("Trakt sync: state recovery required: %s", exc)

    backup = STATE_FILE.with_suffix(STATE_FILE.suffix + ".bak")
    recovered = default
    try:
        recovered = load_json(backup, default)
        if recovered is not None and not isinstance(recovered, dict):
            raise TraktSyncError("Trakt state backup must be a JSON object")
    except TraktSyncError as exc:
        logger.error("Trakt sync: state backup recovery failed: %s", exc)
        recovered = default

    if STATE_FILE.exists():
        stamp = now_utc().strftime("%Y%m%d-%H%M%S-%f")
        corrupt = STATE_FILE.with_name(f"{STATE_FILE.name}.corrupt-{stamp}")
        try:
            STATE_FILE.replace(corrupt)
            logger.error("Trakt sync: preserved corrupt state as %s", corrupt.name)
        except OSError as exc:
            logger.error("Trakt sync: could not preserve corrupt state: %s", exc)
            return recovered

    save_state(recovered, create_backup=False)
    logger.warning("Trakt sync: recovered state from the last valid backup")
    return recovered


def load_secret_json(path, secret_keys, default=None):
    stored = load_json(path, default)
    if not isinstance(stored, dict):
        return stored
    runtime = dict(stored)
    migrate = False
    for key in secret_keys:
        value = stored.get(key)
        if not value:
            continue
        runtime[key] = unprotect_secret(value)
        if os.name == "nt" and not is_protected(value):
            migrate = True
    if migrate:
        try:
            save_secret_json(path, runtime, secret_keys)
            logger.info("Protected plaintext secrets in %s with Windows DPAPI.", path.name)
        except OSError as exc:
            logger.warning("Could not migrate secrets in %s yet: %s", path.name, exc)
    return runtime


def save_secret_json(path, data, secret_keys):
    stored = dict(data)
    for key in secret_keys:
        if stored.get(key):
            stored[key] = protect_secret(stored[key])
    save_json(path, stored)


def now_utc():
    return datetime.now(timezone.utc)


def parse_dt(value):
    if not value:
        return None
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.astimezone()
    return parsed.astimezone(timezone.utc)


def iso(value):
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def _record_health(
    state,
    summary,
    ok,
    *,
    http_status=None,
    added_movies=0,
    added_episodes=0,
    not_found=0,
    pending=0,
    retry_after=0,
):
    """Persist secret-free sync diagnostics alongside the durable queue state."""
    health = dict(state.get("health") or {})
    health.update(
        {
            "last_attempt_at": iso(now_utc()),
            "last_ok": bool(ok),
            "last_summary": summary,
            "last_pending": int(pending),
        }
    )
    if http_status is not None:
        health.update(
            {
                "last_http_status": int(http_status),
                "last_added_movies": int(added_movies),
                "last_added_episodes": int(added_episodes),
                "last_not_found": int(not_found),
                "last_retry_after": int(retry_after or 0),
            }
        )
        if ok:
            health["last_success_at"] = iso(now_utc())
    state["health"] = health
    save_state(state)


def get_sync_health():
    """Return structured health data without credentials, IDs, or file paths."""
    state = load_state({}) or {}
    history = load_json(HISTORY_FILE, []) or []
    backlog = load_json(SIMKL_BACKLOG_FILE, {}) or {}
    events = collect_history_events(history, None)
    latest = events[-1] if events else None
    if latest:
        latest = {
            key: latest.get(key)
            for key in ("kind", "title", "season", "episode", "watched_at", "is_anime")
        }
    return {
        "latest_event": latest,
        "simkl_pending": len(backlog) if isinstance(backlog, (dict, list)) else 0,
        "pending": len(state.get("pending") or []),
        "synced_through": state.get("synced_through"),
        "health": dict(state.get("health") or {}),
    }


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _movie_ids(ids):
    result = {}
    if ids.get("imdb"):
        result["imdb"] = ids["imdb"]
    if _int(ids.get("tmdb")):
        result["tmdb"] = _int(ids["tmdb"])
    if ids.get("traktmslug"):
        result["slug"] = ids["traktmslug"]
    return result


def _show_ids(ids):
    result = {}
    if ids.get("imdb"):
        result["imdb"] = ids["imdb"]
    if _int(ids.get("tvdb")):
        result["tvdb"] = _int(ids["tvdb"])
    tmdb = _int(ids.get("tmdbtv")) or _int(ids.get("tmdb"))
    if tmdb:
        result["tmdb"] = tmdb
    if ids.get("trakttvslug"):
        result["slug"] = ids["trakttvslug"]
    return result


_DETAIL_CACHE = {}
_FRIBB = None
_FRIBB_MTIME = None


def _fribb_map():
    """Return ``simkl_id -> Trakt-usable IDs + TVDB season``."""
    global _FRIBB, _FRIBB_MTIME
    try:
        current_mtime = FRIBB_FILE.stat().st_mtime_ns
    except OSError:
        current_mtime = None
    if _FRIBB is not None and current_mtime == _FRIBB_MTIME:
        return _FRIBB

    mapping = {}
    try:
        for entry in load_json(FRIBB_FILE, []) or []:
            simkl_id = _int(entry.get("simkl_id"))
            if not simkl_id:
                continue
            tmdb = entry.get("themoviedb_id")
            if isinstance(tmdb, dict):
                tmdb = tmdb.get("tv") or tmdb.get("movie")
            imdb = entry.get("imdb_id")
            if isinstance(imdb, list):
                imdb = imdb[0] if imdb else None
            mapping.setdefault(
                simkl_id,
                {
                    "tvdb": entry.get("tvdb_id"),
                    "imdb": imdb,
                    "tmdb": tmdb,
                    "season": (entry.get("season") or {}).get("tvdb"),
                },
            )
    except TraktSyncError as exc:
        logger.warning("Trakt sync: Fribb mapping unavailable: %s", exc)
    _FRIBB = mapping
    _FRIBB_MTIME = current_mtime
    return mapping


def _simkl_detail(simkl_id, client_id):
    if not simkl_id or not client_id:
        return {}
    key = int(simkl_id)
    if key not in _DETAIL_CACHE:
        detail = {}
        try:
            response = requests.get(
                f"{SIMKL_API}/anime/{simkl_id}",
                headers={"simkl-api-key": client_id},
                params={"extended": "full", "client_id": client_id},
                timeout=20,
            )
            if response.ok:
                detail = response.json() or {}
        except requests.RequestException as exc:
            logger.warning("Trakt sync: Simkl detail lookup failed for %s: %s", simkl_id, exc)
        _DETAIL_CACHE[key] = detail
    return _DETAIL_CACHE[key]


def _anime_ids_and_season(event, client_id):
    simkl_id = _int(event.get("simkl_id"))
    ids = _show_ids(event.get("ids") or {})
    fribb = _fribb_map().get(simkl_id) if simkl_id else None

    if fribb:
        for key, value in (
            ("imdb", fribb.get("imdb")),
            ("tvdb", _int(fribb.get("tvdb"))),
            ("tmdb", _int(fribb.get("tmdb"))),
        ):
            if value and key not in ids:
                ids[key] = value

    detail = {}
    if not ids or not fribb or fribb.get("season") is None:
        detail = _simkl_detail(simkl_id, client_id)
        for key, value in _show_ids(detail.get("ids") or {}).items():
            ids.setdefault(key, value)

    season = _int(event.get("season")) or 1
    if fribb and _int(fribb.get("season")) is not None:
        season = _int(fribb["season"])
    else:
        mapped = detail.get("mapped_tvdb_seasons")
        if isinstance(mapped, list) and len(mapped) == 1 and _int(mapped[0]) is not None:
            season = _int(mapped[0])
    return ids, season


def _event_key(event):
    return "|".join(
        str(event.get(key) or "")
        for key in ("kind", "simkl_id", "season", "episode", "watched_at")
    )


def collect_history_events(history, since_dt):
    """Flatten local history entries into exact movie/episode watch events."""
    events = []
    for entry in history or []:
        media_type = str(entry.get("type") or "").lower()
        raw_events = entry.get("watch_events") or []
        if not isinstance(raw_events, list):
            raw_events = [raw_events]
        if not raw_events and entry.get("watched_at"):
            raw_events = [{"watched_at": entry["watched_at"]}]

        for raw_event in raw_events:
            watched_at = parse_dt(raw_event.get("watched_at") or entry.get("watched_at"))
            if not watched_at or (since_dt and watched_at <= since_dt):
                continue
            is_movie = media_type == "movie"
            event = {
                "kind": "movie" if is_movie else "episode",
                "title": entry.get("title"),
                "simkl_id": entry.get("simkl_id"),
                "watched_at": iso(watched_at),
                "ids": entry.get("ids") or {},
                "is_anime": media_type == "anime",
            }
            if not is_movie:
                event["season"] = raw_event.get("season") or entry.get("season") or 1
                event["episode"] = raw_event.get("episode") or entry.get("episode")
                if not _int(event["episode"]):
                    logger.warning(
                        "Trakt sync: local history event has no episode number: %s",
                        entry.get("title"),
                    )
                    continue
            events.append(event)
    events.sort(key=lambda event: parse_dt(event["watched_at"]))
    return events


def _deduplicate(events):
    unique = {}
    for event in events:
        unique[_event_key(event)] = event
    return list(unique.values())


def _not_found_count(not_found):
    if not isinstance(not_found, dict):
        return 0
    return sum(len(value) for value in not_found.values() if isinstance(value, list))


def _not_found_events(events, not_found):
    """Match Trakt's echoed not_found items back to exact local watch events."""
    echoed_times = set()

    def visit(value):
        if isinstance(value, dict):
            watched_at = value.get("watched_at")
            if watched_at:
                try:
                    echoed_times.add(iso(parse_dt(watched_at)))
                except (TypeError, ValueError):
                    pass
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(not_found)
    matched = [event for event in events if event.get("watched_at") in echoed_times]
    if matched or not _not_found_count(not_found):
        return matched
    logger.error(
        "Trakt sync: could not map not_found response to exact events; "
        "retaining the full batch to avoid data loss"
    )
    return list(events)


def build_payload(events, client_id=None):
    """Build a Trakt history payload and return ``(payload, unresolved)``."""
    movies = []
    grouped_shows = {}
    unresolved = []

    for event in events:
        if event.get("kind") == "movie":
            ids = _movie_ids(event.get("ids") or {})
            if not ids:
                unresolved.append(event)
                logger.warning(
                    "Trakt sync: no usable IDs for movie %r (simkl %s); queued",
                    event.get("title"),
                    event.get("simkl_id"),
                )
                continue
            movies.append({"watched_at": event["watched_at"], "ids": ids})
            continue

        if event.get("is_anime"):
            ids, season = _anime_ids_and_season(event, client_id)
        else:
            ids = _show_ids(event.get("ids") or {})
            season = _int(event.get("season")) or 1
        if not ids:
            unresolved.append(event)
            logger.warning(
                "Trakt sync: no usable IDs for episode %r S%sE%s (simkl %s); queued",
                event.get("title"),
                event.get("season"),
                event.get("episode"),
                event.get("simkl_id"),
            )
            continue

        show_key = tuple(sorted(ids.items()))
        show = grouped_shows.setdefault(show_key, {"ids": ids, "seasons": {}})
        episodes = show["seasons"].setdefault(season, [])
        episodes.append(
            {"number": _int(event["episode"]), "watched_at": event["watched_at"]}
        )

    shows = []
    for show in grouped_shows.values():
        seasons = [
            {"number": season, "episodes": episodes}
            for season, episodes in sorted(show["seasons"].items())
        ]
        shows.append({"ids": show["ids"], "seasons": seasons})
    return {"movies": movies, "shows": shows}, unresolved


def count_payload(payload):
    movies = len(payload["movies"])
    episodes = sum(
        len(season["episodes"])
        for show in payload["shows"]
        for season in show["seasons"]
    )
    return movies, episodes


def trakt_config():
    config = load_secret_json(CONFIG_FILE, ("client_secret",), {}) or {}
    if not config.get("client_id") or not config.get("client_secret"):
        raise TraktSyncError(
            f"Trakt is not configured. Add client_id/client_secret to {CONFIG_FILE}."
        )
    return config


def authenticate(config=None):
    config = config or trakt_config()
    response = requests.post(
        f"{TRAKT_API}/oauth/device/code",
        json={"client_id": config["client_id"]},
        timeout=30,
    )
    response.raise_for_status()
    device = response.json()
    print(f"\n  1. Open: {device['verification_url']}\n  2. Enter code: {device['user_code']}\n")
    print("Waiting for authorization", end="", flush=True)
    deadline = time.monotonic() + device["expires_in"]
    interval = device["interval"]
    while time.monotonic() < deadline:
        time.sleep(interval)
        print(".", end="", flush=True)
        token_response = requests.post(
            f"{TRAKT_API}/oauth/device/token",
            json={
                "code": device["device_code"],
                "client_id": config["client_id"],
                "client_secret": config["client_secret"],
            },
            timeout=30,
        )
        if token_response.status_code == 200:
            save_secret_json(
                TOKEN_FILE,
                token_response.json(),
                ("access_token", "refresh_token"),
            )
            print(f"\n\nAuthorized. Token saved to {TOKEN_FILE}")
            return
        if token_response.status_code == 400:
            continue
        if token_response.status_code == 429:
            interval += 1
            continue
        raise TraktSyncError(f"Trakt authorization failed (HTTP {token_response.status_code}).")
    raise TraktSyncError("Trakt authorization timed out. Run trakt-auth again.")


def trakt_token(config):
    token = load_secret_json(
        TOKEN_FILE, ("access_token", "refresh_token"), {}
    ) or {}
    if not token:
        raise TraktSyncError("Trakt is not authorized. Run: simkl-mps trakt-auth")
    if token["created_at"] + token["expires_in"] - 3600 < now_utc().timestamp():
        response = requests.post(
            f"{TRAKT_API}/oauth/token",
            json={
                "grant_type": "refresh_token",
                "refresh_token": token["refresh_token"],
                "client_id": config["client_id"],
                "client_secret": config["client_secret"],
                "redirect_uri": "urn:ietf:wg:oauth:2.0:oob",
            },
            timeout=30,
        )
        if response.status_code != 200:
            raise TraktSyncError("Trakt token refresh failed. Run: simkl-mps trakt-auth")
        token = response.json()
        save_secret_json(TOKEN_FILE, token, ("access_token", "refresh_token"))
    return token["access_token"]


def push_trakt(config, token, payload, retries=3):
    headers = {
        "Authorization": f"Bearer {token}",
        "trakt-api-version": "2",
        "trakt-api-key": config["client_id"],
        "Content-Type": "application/json",
    }
    for attempt in range(1, retries + 1):
        try:
            response = requests.post(
                f"{TRAKT_API}/sync/history",
                headers=headers,
                json=payload,
                timeout=45,
            )
            try:
                body = response.json()
            except ValueError:
                body = {}
            if response.status_code == 429:
                retry_after = _int(response.headers.get("Retry-After")) or 60
                logger.warning(
                    "Trakt sync: HTTP 429 rate limited | Retry-After=%ss | X-Ratelimit=%s",
                    retry_after,
                    response.headers.get("X-Ratelimit", "missing"),
                )
                return response.status_code, body, retry_after
            if response.status_code in (502, 503, 504) and attempt < retries:
                logger.warning(
                    "Trakt sync: HTTP %s; retrying in 30s (%d/%d)",
                    response.status_code,
                    attempt,
                    retries,
                )
                time.sleep(30)
                continue
            return response.status_code, body, 0
        except requests.RequestException as exc:
            logger.warning(
                "Trakt sync: push attempt %d/%d failed: %s", attempt, retries, exc
            )
            if attempt < retries:
                time.sleep(3)
    return None, None, 0


def _log_payload(payload):
    for show in payload["shows"]:
        for season in show["seasons"]:
            for episode in season["episodes"]:
                logger.info(
                    "Trakt sync: -> episode %s S%02dE%02d @ %s",
                    show["ids"],
                    season["number"],
                    episode["number"],
                    episode["watched_at"],
                )
    for movie in payload["movies"]:
        logger.info("Trakt sync: -> movie %s @ %s", movie["ids"], movie["watched_at"])


def ensure_state():
    if not STATE_FILE.exists():
        save_state({"synced_through": iso(now_utc()), "pending": []})
        logger.info("Trakt sync: initialized marker at current time")


def sync_history(since=None, dry_run=False):
    """Sync local history to Trakt and return a structured result."""
    state = load_state({}) or {}
    if since:
        marker = parse_dt(f"{since}T00:00:00Z")
    elif state.get("synced_through"):
        marker = parse_dt(state["synced_through"])
    else:
        if not dry_run:
            ensure_state()
        summary = "Trakt sync initialized. New watch events will sync automatically."
        logger.info(summary)
        if not dry_run:
            state = load_state({}) or {}
            _record_health(state, summary, True, pending=0)
        return SyncResult(True, summary)

    history = load_json(HISTORY_FILE, []) or []
    new_events = collect_history_events(history, marker)
    pending_events = [] if since else (state.get("pending") or [])
    events = _deduplicate(pending_events + new_events)
    logger.info(
        "Trakt sync: local window after %s | %d new event(s), %d pending",
        iso(marker),
        len(new_events),
        len(pending_events),
    )

    if not events:
        summary = f"Trakt: nothing new after {iso(marker)}."
        logger.info(summary)
        if not dry_run:
            _record_health(state, summary, True, pending=0)
        return SyncResult(True, summary)

    client_id = get_credentials().get("client_id")
    payload, unresolved = build_payload(events, client_id)
    movie_count, episode_count = count_payload(payload)
    _log_payload(payload)

    if dry_run:
        summary = (
            f"Trakt dry run: {movie_count} movie(s), {episode_count} episode(s), "
            f"{len(unresolved)} pending."
        )
        logger.info(summary)
        return SyncResult(True, summary, pending=len(unresolved))

    if movie_count == 0 and episode_count == 0:
        latest = max((parse_dt(event["watched_at"]) for event in new_events), default=marker)
        save_state({"synced_through": iso(max(marker, latest)), "pending": unresolved})
        summary = f"Trakt: no matchable events; {len(unresolved)} queued for retry."
        logger.warning(summary)
        state = load_state({}) or {}
        _record_health(state, summary, False, pending=len(unresolved))
        return SyncResult(False, summary, pending=len(unresolved))

    try:
        config = trakt_config()
        token = trakt_token(config)
    except (KeyError, TraktSyncError) as exc:
        logger.error("Trakt setup or authorization failed: %s", exc)
        summary = "Trakt setup or authorization failed. Open logs for details."
        _record_health(state, summary, False, pending=len(events))
        return SyncResult(False, summary, pending=len(events))

    push_result = push_trakt(config, token, payload)
    if len(push_result) == 2:  # Compatibility for callers/tests using the old shape.
        status, body = push_result
        retry_after = 0
    else:
        status, body, retry_after = push_result
    if status is None:
        summary = "Trakt push failed after retries; state was not advanced."
        logger.error(summary)
        _record_health(state, summary, False, pending=len(events))
        return SyncResult(False, summary, pending=len(events))

    added = (body or {}).get("added", {})
    not_found = (body or {}).get("not_found", {})
    not_found_count = _not_found_count(not_found)
    logger.info(
        "Trakt sync: HTTP %s | added movies=%s episodes=%s | not_found=%s",
        status,
        added.get("movies", 0),
        added.get("episodes", 0),
        not_found_count,
    )
    for category in ("shows", "episodes", "movies"):
        for item in not_found.get(category) or []:
            logger.warning("Trakt sync: NOT MATCHED (%s): %s", category, item)

    if status not in (200, 201):
        summary = f"Trakt returned HTTP {status}; state was not advanced."
        logger.error(summary)
        _record_health(
            state,
            summary,
            False,
            http_status=status,
            added_movies=added.get("movies", 0),
            added_episodes=added.get("episodes", 0),
            not_found=not_found_count,
            pending=len(events),
            retry_after=retry_after,
        )
        return SyncResult(
            False,
            summary,
            pending=len(events),
            retry_after=retry_after,
        )

    retry_events = unresolved + _not_found_events(events, not_found)
    retry_events = _deduplicate(retry_events)
    if not since:
        latest = max((parse_dt(event["watched_at"]) for event in new_events), default=marker)
        save_state({"synced_through": iso(max(marker, latest)), "pending": retry_events})

    summary = (
        f"Trakt: +{added.get('episodes', 0)} episode(s), "
        f"+{added.get('movies', 0)} movie(s); {len(retry_events)} pending."
    )
    logger.info(summary)
    state = load_state({}) or state
    fully_synced = not retry_events
    _record_health(
        state,
        summary,
        fully_synced,
        http_status=status,
        added_movies=added.get("movies", 0),
        added_episodes=added.get("episodes", 0),
        not_found=not_found_count,
        pending=len(retry_events),
    )
    return SyncResult(
        fully_synced,
        summary,
        pushed=True,
        pending=len(retry_events),
    )
