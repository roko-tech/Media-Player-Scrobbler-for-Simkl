"""
Monitor module for Media Player Scrobbler for SIMKL.
Handles continuous window monitoring and scrobbling.
"""

import os
import time
import logging
import threading
import platform
from datetime import datetime

from .window_detection import (
    get_active_window_info, 
    get_all_windows_info,
    is_video_player
)
from simkl_mps.media_scrobbler import MediaScrobbler # Updated import

logger = logging.getLogger(__name__)

PLATFORM = platform.system().lower()

class Monitor:
    """Continuously monitors windows for movie playback"""

    def __init__(self, app_data_dir, client_id=None, access_token=None, poll_interval=10, 
                 testing_mode=False, backlog_check_interval=300):
        self.app_data_dir = app_data_dir
        self.client_id = client_id
        self.access_token = access_token
        self.poll_interval = poll_interval
        self.testing_mode = testing_mode
        self.running = False
        self.monitor_thread = None
        self._lock = threading.RLock()
        self.scrobbler = MediaScrobbler( # Updated instantiation
            app_data_dir=self.app_data_dir,
            client_id=self.client_id,
            access_token=self.access_token,
            testing_mode=self.testing_mode
        )
        self.last_backlog_check = 0
        self.backlog_check_interval = backlog_check_interval
        self.search_callback = None
        # Add a dictionary to track when we last searched for each title
        self._last_search_attempts = {}
        # Search cooldown period when offline (60 seconds)
        self.offline_search_cooldown = 60
        # Debug field to track cycles without detection
        self._debug_cycles = 0
        # State tracking for logging verbosity
        self.last_known_player_process = None
        self.last_known_filepath = None
 
    def set_search_callback(self, callback):
        """Set the callback function for movie search"""
        self.search_callback = callback

    def start(self):
        """Start monitoring"""
        if self.running:
            logger.warning("Monitor already running")
            return False

        self.running = True
        self.monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self.monitor_thread.start()
        logger.info("Monitor started")
        return True

    def stop(self):
        """Stop monitoring"""
        if not self.running:
            logger.warning("Monitor not running")
            return False

        self.running = False
        
        if self.monitor_thread and self.monitor_thread.is_alive():
            try:
                self.monitor_thread.join(timeout=2)
            except RuntimeError:
                logger.warning("Could not join monitor thread")
        
        with self._lock:
            if self.scrobbler.currently_tracking:
                self.scrobbler.stop_tracking()
        
        logger.info("Monitor stopped")
        return True

    @staticmethod
    def select_player_window(windows, preferred_process=None):
        """Select one supported player deterministically, preserving active-player continuity."""
        candidates = [window for window in windows if is_video_player(window)]
        if not candidates:
            return None

        preferred = (preferred_process or '').casefold()
        player_priority = {
            'vlc.exe': 0,
            'potplayermini64.exe': 1,
            'potplayermini.exe': 1,
            'mpc-hc64.exe': 2,
            'mpc-hc.exe': 2,
            'mpv.exe': 3,
        }

        def sort_key(window):
            process_name = str(window.get('process_name') or '').casefold()
            return (
                0 if preferred and process_name == preferred else 1,
                player_priority.get(process_name, 100),
                process_name,
                int(window.get('pid') or 0),
                str(window.get('title') or '').casefold(),
            )

        return min(candidates, key=sort_key)


    def collect_player_observations(self, windows):
        """Capture one immutable snapshot for every recognized player window."""
        observations = []
        for window in windows:
            if not is_video_player(window):
                continue
            process_name = str(window.get("process_name") or "")
            try:
                snapshot = self.scrobbler.get_player_snapshot(process_name)
            except Exception as exc:
                logger.debug("Could not sample %s: %s", process_name, exc)
                snapshot = None
            observations.append((window, snapshot))
        return observations

    @staticmethod
    def select_player_observation(observations, preferred_process=None):
        """Choose the strongest usable observation with stable tie-breaking."""
        if not observations:
            return None, None
        preferred = (preferred_process or "").casefold()
        player_priority = {
            "potplayermini64.exe": 0,
            "potplayermini.exe": 0,
            "vlc.exe": 1,
            "mpc-hc64.exe": 2,
            "mpc-hc.exe": 2,
            "mpv.exe": 3,
        }

        def sort_key(observation):
            window, snapshot = observation
            process_name = str(window.get("process_name") or "").casefold()
            usable = bool(snapshot and snapshot.filepath)
            playing = bool(snapshot and snapshot.playback_state == "playing")
            foreground = bool(
                window.get("is_active")
                or window.get("active")
                or window.get("foreground")
            )
            has_progress = bool(
                snapshot
                and snapshot.position_seconds is not None
                and snapshot.duration_seconds
            )
            return (
                0 if usable and playing else 1 if usable else 2,
                0 if foreground else 1,
                0 if preferred and process_name == preferred else 1,
                0 if has_progress else 1,
                0 if snapshot and not snapshot.error else 1,
                player_priority.get(process_name, 100),
                process_name,
                int(window.get("pid") or 0),
                str(window.get("title") or "").casefold(),
            )

        return min(observations, key=sort_key)

    def _monitor_loop(self):
        """Main monitoring loop."""
        logger.info("Media monitoring service initialized and running")
        last_processed_titles = {}

        while self.running:
            try:
                all_windows = get_all_windows_info()
                self._debug_cycles += 1
                if self._debug_cycles % 3 == 0:
                    logger.debug(
                        "Monitor loop cycle: %s, Found %s windows",
                        self._debug_cycles,
                        len(all_windows),
                    )

                observations = self.collect_player_observations(all_windows)
                window_info, player_snapshot = self.select_player_observation(
                    observations,
                    preferred_process=self.last_known_player_process,
                )
                found_player = window_info is not None

                if window_info:
                    process_name = window_info.get('process_name', '')
                    if process_name != self.last_known_player_process:
                        logger.info(
                            "Found video player: %s - '%s'",
                            process_name,
                            window_info.get('title', 'Unknown'),
                        )

                    with self._lock:
                        scrobble_info = self.scrobbler.process_window(
                            window_info,
                            player_snapshot=player_snapshot,
                        )

                    filepath = scrobble_info.get('filepath') if scrobble_info else None
                    if filepath and (
                        filepath != self.last_known_filepath
                        or process_name != self.last_known_player_process
                    ):
                        logger.info("Retrieved media file from player: %s", os.path.basename(filepath))
                    elif (
                        not filepath
                        and self.last_known_filepath is not None
                        and process_name == self.last_known_player_process
                    ):
                        logger.info("Filepath no longer available from %s", process_name)

                    if scrobble_info:
                        title = scrobble_info.get("title", "Unknown")
                        source = scrobble_info.get("source", "unknown")
                        if last_processed_titles.get(process_name) != title:
                            last_processed_titles[process_name] = title
                            logger.debug(
                                "Active media player detected: %s",
                                window_info.get('title', 'Unknown'),
                            )
                            logger.info("Detected media '%s' using %s", title, source)
                    else:
                        logger.debug(
                            "No scrobble info returned from process_window for %s",
                            process_name,
                        )

                    self.last_known_player_process = process_name
                    self.last_known_filepath = filepath

                if not found_player and self.last_known_player_process is not None:
                    logger.info(
                        "Media playback ended or player '%s' closed.",
                        self.last_known_player_process,
                    )
                    with self._lock:
                        if self.scrobbler.currently_tracking:
                            self.scrobbler.stop_tracking()
                        last_processed_titles.clear()
                    self.last_known_player_process = None
                    self.last_known_filepath = None
                elif not found_player and self._debug_cycles % 10 == 0:
                    logger.debug(
                        "No video players detected (cycle %s)",
                        self._debug_cycles,
                    )

                current_time = time.time()
                if current_time - self.last_backlog_check > self.backlog_check_interval:
                    self.scrobbler.request_backlog_sync()
                    self.last_backlog_check = current_time

                time.sleep(self.poll_interval)

            except Exception as exc:
                logger.error(
                    "Monitoring service encountered an error: %s",
                    exc,
                    exc_info=True,
                )
                time.sleep(max(5, self.poll_interval))

        logger.info("Media monitoring service stopped")

    def set_credentials(self, client_id, access_token, account_type=None, settings_all=None):
        """Set API credentials"""
        self.client_id = client_id
        self.access_token = access_token
        self.scrobbler.set_credentials(client_id, access_token, account_type=account_type, settings_all=settings_all)

    def cache_media_info(self, title, simkl_id, display_name, media_type='movie', season=None, episode=None,
                         year=None, runtime=None, season_display=None, episode_display=None):
        """Cache media info to avoid repeated searches for any media type"""
        logger.info(f"Caching media info for '{title}': ID={simkl_id}, Display='{display_name}', Type={media_type}" +
                   (f", Season={season}" if season is not None else "") +
                   (f", Episode={episode}" if episode is not None else ""))
        self.scrobbler.cache_media_info(
            title,
            simkl_id,
            display_name,
            media_type,
            season,
            episode,
            year,
            runtime,
            season_display=season_display,
            episode_display=episode_display
        )
        
    def cache_movie_info(self, title, simkl_id, movie_name, runtime=None):
        """Legacy method for backward compatibility, delegates to cache_media_info"""
        logger.debug(f"Using legacy cache_movie_info method for '{movie_name}'")
        self.cache_media_info(title, simkl_id, movie_name, 'movie', runtime=runtime)