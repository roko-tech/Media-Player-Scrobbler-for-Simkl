"""
Watch history manager module for Media Player Scrobbler for SIMKL.
Tracks and manages local history of watched media.
"""

import os
import copy
import json
import logging
import pathlib
from datetime import datetime
import webbrowser
import shutil
import sys
import mimetypes
import threading

from simkl_mps.app_paths import AppPathManifest
from simkl_mps.config_manager import get_setting

logger = logging.getLogger(__name__)

class WatchHistoryManager:
    """Manages a local history of watched movies and TV shows"""

    def __init__(self, app_data_dir: pathlib.Path, history_file="watch_history.json"):
        self.app_data_dir = app_data_dir
        self.history_file = self.app_data_dir / history_file
        self.backup_file = self.history_file.with_suffix(self.history_file.suffix + ".bak")
        self._lock = threading.RLock()
        self._on_saved = None
        self.history = self._load_history()
        if self.history is None:
            logger.error("Failed to load history, initializing to empty list.")
            self.history = []
        
        # Make sure we have the viewer files in the app_data_dir
        self._ensure_viewer_exists()

    def set_on_saved(self, callback):
        """Set a callback that runs after a completed-watch event is saved."""
        self._on_saved = callback

    def _notify_saved(self):
        if not self._on_saved:
            return
        try:
            self._on_saved()
        except Exception:
            logger.exception("Watch-history saved callback failed")
        
    def _load_history(self):
        """Load the history from file, creating the file if it does not exist."""
        if not os.path.exists(self.app_data_dir):
            try:
                os.makedirs(self.app_data_dir, exist_ok=True)
                logger.info(f"Created app data directory: {self.app_data_dir}")
            except Exception as e:
                logger.error(f"Failed to create app data directory: {e}")
                return []
                
        if os.path.exists(self.history_file):
            try:
                with open(self.history_file, 'r', encoding='utf-8') as f:
                    content = f.read().strip()
                    if content:
                        f.seek(0)
                        loaded = json.load(f)
                        if not isinstance(loaded, list):
                            raise TypeError("Watch history must be a JSON list")
                        return loaded
                    else:
                        logger.debug("History file exists but is empty. Starting with empty history.")
                        return []
            except (json.JSONDecodeError, TypeError) as e:
                logger.error(f"Error loading history: {e}")
                return self._recover_history()
            except Exception as e:
                logger.error(f"Error loading history: {e}")
        else:
            # File does not exist, create it
            try:
                with open(self.history_file, 'w', encoding='utf-8') as f:
                    json.dump([], f)
                logger.info(f"Created new history file: {self.history_file}")
            except Exception as e:
                logger.error(f"Failed to create history file: {e}")
            return []
        return []

    def _recover_history(self):
        """Preserve a corrupt primary file and recover the last valid backup."""
        recovered = []
        try:
            backup_data = json.loads(self.backup_file.read_text(encoding='utf-8'))
            if isinstance(backup_data, list):
                recovered = backup_data
            else:
                logger.error("Watch-history backup has an unexpected data type.")
        except FileNotFoundError:
            logger.error("No watch-history backup is available for recovery.")
        except (OSError, json.JSONDecodeError) as exc:
            logger.error("Could not read watch-history backup: %s", exc)

        if self.history_file.exists():
            stamp = datetime.now().strftime('%Y%m%d-%H%M%S-%f')
            corrupt = self.history_file.with_name(f"{self.history_file.name}.corrupt-{stamp}")
            try:
                self.history_file.replace(corrupt)
                logger.error("Preserved corrupt watch history as %s", corrupt.name)
            except OSError as exc:
                logger.error("Could not preserve corrupt watch history: %s", exc)
                return recovered

        self.history = recovered
        if self._save_history(create_backup=False):
            logger.warning("Recovered %d watch-history entries from backup.", len(recovered))
        return recovered

    def _save_history(self, create_backup=True):
        """Save the history to file"""
        temp = self.history_file.with_suffix(self.history_file.suffix + '.tmp')
        with self._lock:
            try:
                self.history_file.parent.mkdir(parents=True, exist_ok=True)
                with open(temp, 'w', encoding='utf-8') as f:
                    json.dump(self.history, f, indent=4)
                    f.flush()
                    os.fsync(f.fileno())
                if create_backup and self.history_file.exists():
                    try:
                        current = json.loads(self.history_file.read_text(encoding='utf-8'))
                        if isinstance(current, list):
                            backup_temp = self.backup_file.with_suffix(self.backup_file.suffix + '.tmp')
                            shutil.copy2(self.history_file, backup_temp)
                            backup_temp.replace(self.backup_file)
                    except (OSError, json.JSONDecodeError):
                        logger.warning("Skipped backup of an invalid watch-history file.")
                temp.replace(self.history_file)
                return True
            except Exception as e:
                logger.error(f"Error saving history: {e}")
                try:
                    temp.unlink(missing_ok=True)
                except OSError:
                    pass
                return False

    def add(self, media_info):
        with self._lock:
            previous = copy.deepcopy(self.history)
            saved = self._add_unlocked(media_info)
            if not saved:
                self.history = previous
            return saved

    def _add_unlocked(self, media_info):
        """
        Add a media item to the history
        
        Args:
            media_info (dict): Dictionary containing media metadata
                Required keys: simkl_id, title
                Optional keys: poster_url, year, type, overview, runtime
        """
        if not media_info or not media_info.get('simkl_id') or not media_info.get('title'):
            logger.error(f"Invalid media info for history: {media_info}")
            return False
            
        # Add watched timestamp
        media_info['watched_at'] = datetime.now().isoformat()
        
        # Set default media type if not provided
        if 'type' not in media_info:
            media_info['type'] = 'movie'
            
        # Check if this item already exists in history
        existing_idx = None
        for idx, item in enumerate(self.history):
            if item.get('simkl_id') == media_info.get('simkl_id') and item.get('type') == media_info.get('type'):
                existing_idx = idx
                break
        
        if existing_idx is not None:
            # Update existing entry
            self.history[existing_idx] = media_info
            logger.info(f"Updated '{media_info['title']}' in watch history")
        else:
            # Add new entry
            self.history.append(media_info)
            logger.info(f"Added '{media_info['title']}' to watch history")
            
        saved = self._save_history()
        if saved:
            self._notify_saved()
        return saved

    def get_history(self, limit=None, offset=0, sort_by="watched_at", sort_order="desc"):
        """
        Get history entries
        
        Args:
            limit (int, optional): Maximum number of entries to return
            offset (int, optional): Starting offset for pagination
            sort_by (str, optional): Field to sort by (watched_at, title)
            sort_order (str, optional): Sort order (asc, desc)
            
        Returns:
            list: History entries
        """
        if not self.history:
            return []
            
        # Make a copy of the history to avoid modifying the original
        sorted_history = list(self.history)
        
        # Sort the history
        if (sort_by == "title"):
            sorted_history.sort(key=lambda x: x.get('title', '').lower(), 
                                reverse=(sort_order.lower() == "desc"))
        else:  # Default sort by watched_at
            sorted_history.sort(key=lambda x: x.get('watched_at', ''), 
                                reverse=(sort_order.lower() == "desc"))
        
        # Apply pagination if requested
        if limit:
            return sorted_history[offset:offset+limit]
        return sorted_history[offset:]

    def get_entry(self, simkl_id, media_type="movie"):
        """Get a specific entry from history"""
        for item in self.history:
            if item.get('simkl_id') == simkl_id and item.get('type', 'movie') == media_type:
                return item
        return None

    def remove(self, simkl_id, media_type="movie"):
        """Remove a specific entry from history"""
        with self._lock:
            previous = copy.deepcopy(self.history)
            initial_length = len(self.history)
            self.history = [item for item in self.history
                            if not (item.get('simkl_id') == simkl_id and item.get('type', 'movie') == media_type)]

            if len(self.history) != initial_length:
                if self._save_history():
                    return True
                self.history = previous
            return False

    def clear(self):
        """Clear the entire history"""
        with self._lock:
            previous = self.history
            self.history = []
            if self._save_history():
                return True
            self.history = previous
            return False
        
    def purge_local_data(self):
        """Remove primary, backup, corrupt, and viewer history artifacts."""
        with self._lock:
            result = AppPathManifest(self.app_data_dir).purge(("history",))
            if result.success:
                self.history = []
            return result

    def _ensure_viewer_exists(self):
        """Ensure the watch history viewer files exist in the user's app data directory, always copying from the bundled source."""
        import shutil
        user_dir = self.app_data_dir / "watch-history-viewer"
        source_dir = self._get_source_dir()
        # Always copy all files from source_dir to user_dir, overwriting if needed
        if not user_dir.exists():
            user_dir.mkdir(parents=True, exist_ok=True)
        for item in source_dir.iterdir():
            dest = user_dir / item.name
            if item.is_dir():
                if dest.exists():
                    shutil.rmtree(dest)
                shutil.copytree(item, dest)
            else:
                shutil.copy2(item, dest)
        logger.info(f"Ensured watch-history-viewer is up-to-date in user directory: {user_dir}")

    def _get_source_dir(self):
        """Find the source directory for the watch history viewer files (cross-platform, bundled in temp when frozen)."""
        import sys
        from pathlib import Path
        if getattr(sys, 'frozen', False):
            # PyInstaller bundle: _MEIPASS points to the temp extraction dir
            # The folder is bundled as simkl_mps/watch-history-viewer
            return Path(sys._MEIPASS) / "simkl_mps" / "watch-history-viewer"
        else:
            # Development: use the source folder
            return Path(__file__).parent / "watch-history-viewer"

    def open_history(self):
        """Open the history page in the default web browser"""
        try:
            # Ensure the viewer exists
            self._ensure_viewer_exists()
            
            # Path to the viewer index.html
            viewer_path = self.app_data_dir / "watch-history-viewer" / "index.html"
            
            # If the viewer doesn't exist, show an error
            if not viewer_path.exists():
                logger.error(f"History viewer not found at: {viewer_path}")
                return False
                
            # Update the history data file next to the viewer
            self._update_history_data()
                
            # Open the viewer in the browser
            logger.info(f"Opening history viewer: {viewer_path}")
            webbrowser.open(f"file://{viewer_path}")
            return True
        except Exception as e:
            logger.error(f"Error opening history page: {e}")
            return False
            
    def _viewer_history(self):
        """Return a viewer projection with local paths redacted by default."""
        if get_setting("viewer_include_file_paths", False):
            return copy.deepcopy(self.history)

        private_keys = {"file_path", "media_file_path", "original_filepath"}

        def redact(value):
            if isinstance(value, dict):
                return {
                    key: redact(item)
                    for key, item in value.items()
                    if key not in private_keys
                }
            if isinstance(value, list):
                return [redact(item) for item in value]
            return value

        return redact(self.history)

    def _update_history_data(self):
        """Atomically generate the offline viewer's redacted data projection."""
        try:
            loaded_history = self._load_history()
            self.history = loaded_history if isinstance(loaded_history, list) else []
            viewer_dir = self.app_data_dir / "watch-history-viewer"
            viewer_dir.mkdir(parents=True, exist_ok=True)

            projection = self._viewer_history()
            history_json = json.dumps(
                projection,
                separators=(",", ":"),
                ensure_ascii=False,
            )
            data_file = viewer_dir / "data.js"
            temp_file = data_file.with_suffix(data_file.suffix + ".tmp")
            with open(temp_file, "w", encoding="utf-8") as handle:
                handle.write("// Auto-generated local viewer data.\n")
                handle.write(f"const HISTORY_DATA = {history_json};\n")
                handle.flush()
                os.fsync(handle.fileno())
            temp_file.replace(data_file)
            logger.info(
                "Updated offline viewer projection with %s history entries (paths included: %s).",
                len(projection),
                bool(get_setting("viewer_include_file_paths", False)),
            )
        except Exception as exc:
            logger.error("Error updating history data: %s", exc, exc_info=True)
    
    @staticmethod
    def _positive_int(value):
        try:
            count = int(value)
        except (TypeError, ValueError):
            return None
        return count if count > 0 else None

    def _build_watch_event(self, media_item, watched_at, media_file_path=None, watched_progress=100):
        event = {"watched_at": watched_at}
        if media_item.get("event_id"):
            event["event_id"] = media_item["event_id"]
        if media_item.get("season") is not None:
            event["season"] = media_item.get("season")
        if media_item.get("episode") is not None:
            event["episode"] = media_item.get("episode")
        if watched_progress is not None:
            event["progress"] = watched_progress
        if media_file_path:
            event["file_path"] = str(media_file_path)
        return event

    def _build_legacy_watch_event(self, history_entry):
        event = {"watched_at": history_entry.get("watched_at") or datetime.now().isoformat()}
        if history_entry.get("event_id"):
            event["event_id"] = history_entry["event_id"]
        if history_entry.get("season") is not None:
            event["season"] = history_entry.get("season")
        if history_entry.get("episode") is not None:
            event["episode"] = history_entry.get("episode")
        file_path = history_entry.get("file_path") or history_entry.get("filepath_at_watch")
        if file_path:
            event["file_path"] = file_path
        return event

    def _ensure_watch_events(self, history_entry):
        events = history_entry.get("watch_events")
        if not isinstance(events, list):
            events = []
        if not events:
            events.append(self._build_legacy_watch_event(history_entry))

        history_entry["watch_events"] = events
        existing_count = self._positive_int(history_entry.get("watch_count"))
        history_entry["watch_count"] = max(existing_count or 0, len(events))
        if "rewatch_count" not in history_entry:
            history_entry["rewatch_count"] = 0
        return events

    def _append_watch_event(self, history_entry, media_item, watched_at, media_file_path=None,
                            watched_progress=100, is_rewatch=False):
        events = self._ensure_watch_events(history_entry)
        previous_count = self._positive_int(history_entry.get("watch_count")) or len(events)
        events.append(self._build_watch_event(media_item, watched_at, media_file_path, watched_progress))
        history_entry["watch_count"] = max(previous_count + 1, len(events))

        existing_rewatch_count = self._positive_int(history_entry.get("rewatch_count")) or 0
        history_entry["rewatch_count"] = existing_rewatch_count + 1 if is_rewatch else existing_rewatch_count

    def _append_episode_watch_event(self, episode_entry, media_item, watched_at, media_file_path=None,
                                    watched_progress=100):
        events = episode_entry.get("watch_events")
        if not isinstance(events, list):
            events = []
        if not events:
            legacy_event = {
                "watched_at": episode_entry.get("watched_at") or watched_at,
                "season": episode_entry.get("season"),
                "episode": episode_entry.get("number")
            }
            if episode_entry.get("file_path"):
                legacy_event["file_path"] = episode_entry.get("file_path")
            events.append(legacy_event)

        previous_count = self._positive_int(episode_entry.get("watch_count")) or len(events)
        events.append(self._build_watch_event(media_item, watched_at, media_file_path, watched_progress))
        episode_entry["watch_events"] = events
        episode_entry["watch_count"] = max(previous_count + 1, len(events))
        episode_entry["rewatch_count"] = (self._positive_int(episode_entry.get("rewatch_count")) or 0) + 1

    def add_entry(self, media_item, media_file_path=None, watched_progress=100):
        with self._lock:
            previous = copy.deepcopy(self.history)
            saved = self._add_entry_unlocked(media_item, media_file_path, watched_progress)
            if not saved:
                self.history = previous
            return saved

    def _add_entry_unlocked(self, media_item, media_file_path=None, watched_progress=100):
        """Add a new entry to watch history, or update existing show entries with new episodes"""
        watched_at = media_item.get("watched_at") or datetime.now().isoformat()
        
        # Check if we already have this item in the history
        existing_entry = None
        existing_entry_index = -1

        # Find existing entry by simkl_id regardless of type
        # This ensures we always update existing shows rather than creating new entries
        for i, entry in enumerate(self.history):
            if entry.get("simkl_id") == media_item.get("simkl_id"):
                existing_entry = entry
                existing_entry_index = i
                break

        # --- Handle TV Shows/Anime ---
        if existing_entry and media_item.get("type") in ["tv", "show", "anime"] and "episode" in media_item:
            previous_entry_season = existing_entry.get("season")
            previous_entry_episode = existing_entry.get("episode")
            season_number = media_item.get("season")
            episode_number = media_item.get("episode")
            self._ensure_watch_events(existing_entry)

            # Always ensure season and episode are set at the top level with the latest watched
            if "season" in media_item:
                existing_entry["season"] = media_item.get("season")
            
            if "episode" in media_item:
                existing_entry["episode"] = media_item.get("episode")

            # Update basic metadata from the new media_item
            if "poster_url" in media_item and media_item.get("poster_url"): # Use poster_url
                existing_entry["poster_url"] = media_item.get("poster_url")
            if "year" in media_item and media_item.get("year"):
                existing_entry["year"] = media_item.get("year")
            if "overview" in media_item and media_item.get("overview"):
                existing_entry["overview"] = media_item.get("overview")
            if "runtime" in media_item and media_item.get("runtime"):
                existing_entry["runtime"] = media_item.get("runtime")
            if "ids" in media_item and media_item.get("ids"):
                existing_entry["ids"] = media_item.get("ids")
                # Ensure imdb_id is at the top level if present in ids (for consistency)
                if "imdb" in media_item.get("ids", {}):
                    existing_entry["imdb_id"] = media_item["ids"]["imdb"]

            # Ensure episodes list exists
            if "episodes" not in existing_entry:
                existing_entry["episodes"] = []

            # Older history entries did not store season per episode. Preserve the
            # latest known S/E before merging another same-number episode.
            if previous_entry_season is not None and previous_entry_episode is not None:
                for ep in existing_entry.get("episodes", []):
                    if ep.get("season") is None and ep.get("number") == previous_entry_episode:
                        ep["season"] = previous_entry_season

            # Check if this specific episode already exists
            episode_exists = False
            for ep in existing_entry.get("episodes", []):
                if ep.get("number") == episode_number and ep.get("season") == season_number:
                    # Update existing episode's watched_at and file info
                    self._append_episode_watch_event(ep, media_item, watched_at, media_file_path, watched_progress)
                    ep["season"] = season_number
                    ep["watched_at"] = watched_at
                    if media_file_path:
                        ep["file_path"] = str(media_file_path)
                        ep.update(self._get_file_metadata(media_file_path))
                    episode_exists = True
                    break

            # Add new episode if it doesn't exist
            if not episode_exists:
                episode_data = {
                    "season": season_number,
                    "number": episode_number,
                    "title": media_item.get("episode_title", f"Episode {episode_number}"),
                    "watched_at": watched_at,
                    "watch_count": 1,
                    "rewatch_count": 0,
                    "watch_events": [
                        self._build_watch_event(media_item, watched_at, media_file_path, watched_progress)
                    ]
                }
                if media_file_path:
                    episode_data["file_path"] = str(media_file_path)
                    episode_data.update(self._get_file_metadata(media_file_path))
                existing_entry["episodes"].append(episode_data)
                logger.info(f"Added episode {episode_number} to existing show '{existing_entry['title']}' (ID: {existing_entry['simkl_id']})")

            # Update overall show watched_at and episode count
            self._append_watch_event(
                existing_entry,
                media_item,
                watched_at,
                media_file_path,
                watched_progress,
                is_rewatch=episode_exists
            )
            existing_entry["watched_at"] = watched_at
            existing_entry["episodes_watched"] = len(existing_entry.get("episodes", [])) # Recalculate count

            # Move the updated show entry to the top of the list (most recently watched)
            if existing_entry_index != -1:
                self.history.pop(existing_entry_index)
                self.history.insert(0, existing_entry)

        # --- Handle Movies ---
        elif existing_entry and media_item.get("type") == "movie":
            # Update watched_at timestamp and file info for the existing movie
            self._append_watch_event(
                existing_entry,
                media_item,
                watched_at,
                media_file_path,
                watched_progress,
                is_rewatch=True
            )
            existing_entry["watched_at"] = watched_at
            
            # Update metadata from new media_item
            if "poster_url" in media_item and media_item.get("poster_url"): # Use poster_url
                existing_entry["poster_url"] = media_item.get("poster_url")
            if "year" in media_item and media_item.get("year"):
                existing_entry["year"] = media_item.get("year")
            if "overview" in media_item and media_item.get("overview"):
                existing_entry["overview"] = media_item.get("overview")
            if "runtime" in media_item and media_item.get("runtime"):
                existing_entry["runtime"] = media_item.get("runtime")
            if "ids" in media_item and media_item.get("ids"):
                existing_entry["ids"] = media_item.get("ids")
                # Ensure imdb_id is at the top level if present in ids
                if "imdb" in media_item.get("ids", {}):
                    existing_entry["imdb_id"] = media_item["ids"]["imdb"]
                    
            # Update file path and metadata if provided
            if media_file_path:
                existing_entry["file_path"] = str(media_file_path)
                existing_entry.update(self._get_file_metadata(media_file_path))

            # Move the updated movie entry to the top of the list
            if existing_entry_index != -1:
                self.history.pop(existing_entry_index)
                self.history.insert(0, existing_entry)
                logger.info(f"Updated movie '{existing_entry['title']}' (ID: {existing_entry['simkl_id']})")

        # --- Handle New Entries (Movie or TV Show not found) ---
        elif not existing_entry:
            # Create new history entry
            history_entry = {
                "simkl_id": media_item.get("simkl_id"),
                "title": media_item.get("title", "Unknown Title"),
                "type": media_item.get("type", "movie"),
                "watched_at": watched_at,
                "watch_count": 1,
                "rewatch_count": 0,
                "watch_events": [
                    self._build_watch_event(media_item, watched_at, media_file_path, watched_progress)
                ],
                "poster_url": media_item.get("poster_url", ""), # Use poster_url
                "year": media_item.get("year"),
                "runtime": media_item.get("runtime"),
                "overview": media_item.get("overview"),
                "ids": media_item.get("ids", {})
            }

            # Always add season/episode for TV shows
            if media_item.get("type") in ["tv", "show", "anime"]:
                if "season" in media_item:
                    history_entry["season"] = media_item.get("season")
                if "episode" in media_item:
                    history_entry["episode"] = media_item.get("episode")

            # Add file information if available
            if media_file_path:
                history_entry["file_path"] = str(media_file_path)
                history_entry.update(self._get_file_metadata(media_file_path))

            # Ensure imdb_id is at the top level if present in ids (for consistency)
            if "ids" in history_entry and "imdb" in history_entry["ids"]:
                 history_entry["imdb_id"] = history_entry["ids"]["imdb"]

            # For TV shows/anime, also add episode information to the episodes list
            if history_entry["type"] in ["tv", "show", "anime"] and "episode" in media_item:
                history_entry["episodes_watched"] = 1
                history_entry["total_episodes"] = media_item.get("total_episodes")

                # Initialize episodes list with the first episode
                history_entry["episodes"] = [{
                    "season": media_item.get("season"),
                    "number": media_item.get("episode"),
                    "title": media_item.get("episode_title", f"Episode {media_item.get('episode')}"),
                    "watched_at": watched_at,
                    "watch_count": 1,
                    "rewatch_count": 0,
                    "watch_events": [
                        self._build_watch_event(media_item, watched_at, media_file_path, watched_progress)
                    ]
                }]
                
                # Add file information to episode if available
                if media_file_path and "episodes" in history_entry:
                    history_entry["episodes"][0]["file_path"] = str(media_file_path)
                    history_entry["episodes"][0].update(self._get_file_metadata(media_file_path))
                
                logger.info(f"Created new show entry for '{history_entry['title']}' (ID: {history_entry['simkl_id']}) with episode {media_item.get('episode')}")
            else:
                logger.info(f"Created new movie entry for '{history_entry['title']}' (ID: {history_entry['simkl_id']})")
            
            # Add the new entry to the beginning of the list
            self.history.insert(0, history_entry)

        # Retention is unlimited by default; positive values are an explicit user policy.
        try:
            max_history = int(get_setting("history_retention_limit", 0) or 0)
        except (TypeError, ValueError):
            max_history = 0
        if max_history > 0 and len(self.history) > max_history:
            self.history = self.history[:max_history]
        
        saved = self._save_history()
        if saved:
            self._notify_saved()
        return saved
    
    def _get_file_metadata(self, file_path):
        """Extract metadata from media file"""
        from simkl_mps.window_detection import get_file_metadata
        
        # Use the window_detection module to get file metadata
        return get_file_metadata(file_path)
        
    def _format_file_size(self, size_bytes):
        """Format file size to human-readable format"""
        if size_bytes is None:
            return "Unknown"
            
        # Define units and thresholds
        units = ['B', 'KB', 'MB', 'GB', 'TB']
        if size_bytes == 0:
            return "0 B"
            
        # Calculate the appropriate unit
        i = 0
        while size_bytes >= 1024 and i < len(units) - 1:
            size_bytes /= 1024
            i += 1
            
        # Format with 2 decimal places if not bytes
        if i == 0:
            return f"{size_bytes} {units[i]}"
        else:
            return f"{size_bytes:.2f} {units[i]}"
