"""
Backlog cleaner module for Media Player Scrobbler for SIMKL.
Handles tracking of watched movies to sync when connection is restored.
"""

import os
import copy
import json
import logging
import pathlib
import shutil
import threading
import uuid
from datetime import datetime

# Configure module logging
logger = logging.getLogger(__name__)

class BacklogCleaner:
    """Manages a backlog of watched movies to sync when connection is restored"""

    def __init__(self, app_data_dir: pathlib.Path, backlog_file="backlog.json"):
        self.app_data_dir = app_data_dir
        self.backlog_file = self.app_data_dir / backlog_file # Use app_data_dir
        self.backup_file = self.backlog_file.with_suffix(self.backlog_file.suffix + ".bak")
        self._lock = threading.RLock()
        self.backlog = self._load_backlog()
        # threshold_days parameter removed as it was unused

    def _load_backlog(self):
        """Load the backlog from file, creating the file if it does not exist."""
        if not os.path.exists(self.app_data_dir):
            try:
                os.makedirs(self.app_data_dir, exist_ok=True)
                logger.info(f"Created app data directory: {self.app_data_dir}")
            except Exception as e:
                logger.error(f"Failed to create app data directory: {e}")
                return {} # Return empty dict on error
        if os.path.exists(self.backlog_file):
            try:
                with open(self.backlog_file, 'r', encoding='utf-8') as f:
                    content = f.read().strip()
                    if content:
                        f.seek(0)
                        # Load data and ensure it's a dictionary
                        loaded_data = json.load(f)
                        if isinstance(loaded_data, list):
                            logger.warning("Backlog file contained a list. Attempting conversion to dictionary format.")
                            converted_backlog = {}
                            malformed_items = 0
                            for item in loaded_data:
                                if isinstance(item, dict) and 'simkl_id' in item:
                                    item_key = str(item['simkl_id'])
                                    if item_key in converted_backlog:
                                        logger.warning(
                                            "Duplicate simkl_id '%s' found during backlog list "
                                            "conversion. Preserving it as a distinct watch event.",
                                            item_key,
                                        )
                                        item_key = f"watch_{uuid.uuid4().hex}"
                                    converted_backlog[item_key] = item
                                else:
                                    malformed_items += 1
                                    logger.warning(f"Skipping malformed item during backlog list conversion: {item}")
                            if malformed_items > 0:
                                logger.error(f"Found {malformed_items} malformed items during backlog conversion.")
                            # Save the converted format back to the file immediately
                            # Note: This requires self.backlog to be set before calling _save_backlog
                            # We'll handle this by returning the converted dict and letting __init__ assign it.
                            # If we were calling this method outside __init__, we'd need:
                            # self.backlog = converted_backlog
                            # self._save_backlog()
                            return converted_backlog
                        elif isinstance(loaded_data, dict):
                            return loaded_data # Already a dictionary
                        else:
                            raise TypeError(
                                f"Backlog must be a JSON object, got {type(loaded_data).__name__}"
                            )
                    else:
                        logger.debug("Backlog file exists but is empty. Starting with empty backlog.")
                        return {} # Return empty dict for empty file
            except (json.JSONDecodeError, TypeError) as e:
                logger.error(f"Error loading backlog: {e}")
                return self._recover_backlog()
            except Exception as e:
                logger.error(f"Error loading backlog: {e}")
                return {} # Return empty dict on other load errors
        else:
            # File does not exist, create it
            try:
                with open(self.backlog_file, 'w', encoding='utf-8') as f:
                    json.dump({}, f) # Dump empty dict for new file
                logger.info(f"Created new backlog file: {self.backlog_file}")
            except Exception as e:
                logger.error(f"Failed to create backlog file: {e}")
            return {} # Return empty dict if creation failed or after creation
        # This return statement should ideally not be reached if logic above is correct,
        # but return {} for safety.
        return {}

    def _recover_backlog(self):
        """Preserve a corrupt primary file and recover the last valid backup."""
        recovered = {}
        try:
            backup_data = json.loads(self.backup_file.read_text(encoding='utf-8'))
            if isinstance(backup_data, dict):
                recovered = backup_data
            else:
                logger.error("Backlog backup has an unexpected data type.")
        except FileNotFoundError:
            logger.error("No backlog backup is available for recovery.")
        except (OSError, json.JSONDecodeError) as exc:
            logger.error("Could not read backlog backup: %s", exc)

        if self.backlog_file.exists():
            stamp = datetime.now().strftime('%Y%m%d-%H%M%S-%f')
            corrupt = self.backlog_file.with_name(f"{self.backlog_file.name}.corrupt-{stamp}")
            try:
                self.backlog_file.replace(corrupt)
                logger.error("Preserved corrupt backlog as %s", corrupt.name)
            except OSError as exc:
                logger.error("Could not preserve corrupt backlog: %s", exc)
                return recovered

        self.backlog = recovered
        if self._save_backlog(create_backup=False):
            logger.warning("Recovered %d backlog event(s) from backup.", len(recovered))
        return recovered

    def _save_backlog(self, create_backup=True):
        """Save the backlog to file"""
        temp = self.backlog_file.with_suffix(self.backlog_file.suffix + '.tmp')
        with self._lock:
            try:
                self.backlog_file.parent.mkdir(parents=True, exist_ok=True)
                with open(temp, 'w', encoding='utf-8') as f:
                    json.dump(self.backlog, f, indent=4)
                    f.flush()
                    os.fsync(f.fileno())
                if create_backup and self.backlog_file.exists():
                    try:
                        current = json.loads(self.backlog_file.read_text(encoding='utf-8'))
                        if isinstance(current, dict):
                            backup_temp = self.backup_file.with_suffix(self.backup_file.suffix + '.tmp')
                            shutil.copy2(self.backlog_file, backup_temp)
                            backup_temp.replace(self.backup_file)
                    except (OSError, json.JSONDecodeError):
                        logger.warning("Skipped backup of an invalid backlog file.")
                temp.replace(self.backlog_file)
                return True
            except Exception as e:
                logger.error(f"Error saving backlog: {e}")
                try:
                    temp.unlink(missing_ok=True)
                except OSError:
                    pass
                return False

    def add(self, simkl_id, title, additional_data=None, unique_event=False):
        with self._lock:
            return self._add_unlocked(simkl_id, title, additional_data, unique_event)

    def _add_unlocked(self, simkl_id, title, additional_data=None, unique_event=False):
        """
        Add or update a media item in the backlog dictionary.

        Args:
            simkl_id: The Simkl ID (can be temporary string or actual int).
            title: Title of the media.
            additional_data: Dictionary with additional data (type, season, episode, etc.).
        """
        item_key = f"watch_{uuid.uuid4().hex}" if unique_event else str(simkl_id)

        # Check if item already exists
        existing_entry = self.backlog.get(item_key)
        previous_entry = copy.deepcopy(existing_entry)

        if existing_entry:
            # Update existing entry - prioritize new data but keep old tracking info
            logger.debug(f"Updating existing backlog entry for ID: {item_key}")
            existing_entry['title'] = title # Update title
            existing_entry['timestamp'] = datetime.now().isoformat() # Update timestamp
            if additional_data and isinstance(additional_data, dict):
                existing_entry.update(additional_data) # Merge additional data
            # Ensure tracking fields are preserved or initialized
            existing_entry.setdefault('attempt_count', 0)
            existing_entry.setdefault('last_attempt_timestamp', None)
            existing_entry.setdefault('last_error', None)
        else:
            # Create new entry
            logger.info(f"Adding '{title}' (ID: {item_key}) to backlog.")
            entry = {
                "simkl_id": simkl_id, # Store original ID (could be temp)
                "title": title,
                "timestamp": datetime.now().isoformat(),
                "attempt_count": 0, # Initialize attempt count
                "last_attempt_timestamp": None, # Initialize timestamp
                "last_error": None # Initialize error field
            }
            # Add additional data if provided
            if additional_data and isinstance(additional_data, dict):
                entry.update(additional_data)

            self.backlog[item_key] = entry

        if not self._save_backlog():
            if previous_entry is None:
                self.backlog.pop(item_key, None)
            else:
                self.backlog[item_key] = previous_entry
            return None
        return item_key

    def get_pending(self) -> dict:
        """Get all pending backlog entries as a dictionary."""
        with self._lock:
            if not isinstance(self.backlog, dict):
                 self.backlog = self._load_backlog()
            return copy.deepcopy(self.backlog)

    def update_item(self, simkl_id, updates: dict):
         """
         Update specific fields of a backlog item.

         Args:
             simkl_id: The ID (key) of the item to update.
             updates: A dictionary containing the fields and values to update.
         """
         item_key = str(simkl_id)
         with self._lock:
             if item_key in self.backlog:
                 previous = copy.deepcopy(self.backlog[item_key])
                 self.backlog[item_key].update(updates)
                 if self._save_backlog():
                     logger.debug(f"Updated backlog item {item_key} with: {updates}")
                     return True
                 self.backlog[item_key] = previous
                 return False
             logger.warning(f"Attempted to update non-existent backlog item: {item_key}")
             return False

    def remove(self, simkl_id):
        """
        Remove an entry from the backlog dictionary using its key.

        Args:
            simkl_id: The Simkl ID (key) of the item to remove.
        """
        item_key = str(simkl_id) # Ensure key is string
        with self._lock:
            if item_key in self.backlog:
                try:
                    previous = self.backlog.pop(item_key)
                    if self._save_backlog():
                        logger.info(f"Removed item '{item_key}' from backlog.")
                        return True
                    self.backlog[item_key] = previous
                    return False
                except KeyError:
                     logger.warning(f"KeyError trying to remove '{item_key}' though it was present initially.")
                     return False # Should not happen if check passes, but handle defensively
                except Exception as e:
                     logger.error(f"Error removing item '{item_key}' from backlog: {e}", exc_info=True)
                     return False
            logger.debug(f"Attempted to remove non-existent item '{item_key}' from backlog.")
            return False # Item wasn't there

    def clear(self):
        """Clear the entire backlog dictionary."""
        with self._lock:
            previous = self.backlog
            self.backlog = {}
            if self._save_backlog():
                logger.info("Cleared the entire backlog.")
                return True
            self.backlog = previous
            return False

    def has_pending_items(self) -> bool:
        """Check if there are any pending items in the backlog."""
        with self._lock:
            return bool(self.backlog)

    # Removed the internal process_backlog method as it's handled by MediaScrobbler
