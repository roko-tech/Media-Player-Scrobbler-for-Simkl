"""
Main application module for the Media Player Scrobbler for SIMKL.

Sets up logging, defines the main application class (SimklScrobbler),
handles initialization, monitoring loop, and graceful shutdown.
"""
import time
import sys
import signal
import threading
import pathlib
import logging
from logging.handlers import TimedRotatingFileHandler
from simkl_mps.monitor import Monitor
from simkl_mps.credentials import bootstrap_credentials, get_credentials
from simkl_mps.config_manager import get_app_data_dir, initialize_paths, get_setting, APP_NAME
from simkl_mps.runtime_lock import RuntimeInstanceLock, retain_failed_runtime
from simkl_mps.trakt_watcher import TraktSyncWatcher

# Import platform-specific tray implementation
# Only import get_tray_app, do not import TrayApp or run_tray_app directly

def get_tray_app():
    """Get the correct tray app implementation and runner based on platform"""
    if sys.platform == 'win32':
        from simkl_mps.tray_win import TrayAppWin as TrayApp, run_tray_app
    elif sys.platform == 'darwin':
        from simkl_mps.tray_mac import TrayAppMac as TrayApp, run_tray_app
    else:  # Linux and other platforms
        from simkl_mps.tray_linux import TrayAppLinux as TrayApp, run_tray_app
    return TrayApp, run_tray_app

class ConfigurationError(Exception):
    """Custom exception for configuration loading errors."""
    pass

# Use the configuration manager to get our app data directory
APP_DATA_DIR = get_app_data_dir()

try:
    APP_DATA_DIR.mkdir(parents=True, exist_ok=True)
except Exception as e:
    print(f"CRITICAL: Failed to create application data directory: {e}", file=sys.stderr)
    sys.exit(1)

log_file_path = APP_DATA_DIR / "simkl_mps.log"

stream_handler = logging.StreamHandler()
stream_handler.setLevel(logging.WARNING)
stream_formatter = logging.Formatter('%(levelname)s: %(message)s')
stream_handler.setFormatter(stream_formatter)

try:
    file_handler = TimedRotatingFileHandler(
        log_file_path,
        when='W0',  # Rotate weekly (Monday 00:00)
        interval=1,
        backupCount=6,
        encoding='utf-8'
    )
    file_handler.setLevel(logging.INFO)
    file_formatter = logging.Formatter('%(asctime)s [%(levelname)-8s] %(name)s: %(message)s')
    file_handler.setFormatter(file_formatter)
except Exception as e:
    print(f"CRITICAL: Failed to configure file logging: {e}", file=sys.stderr)
    file_handler = None

logging.basicConfig(
    level=logging.INFO,
    handlers=[h for h in [stream_handler, file_handler] if h]
)

logger = logging.getLogger(__name__)
logger.info("="*20 + " Application Start " + "="*20)
logger.info(f"Using Application Data Directory: {APP_DATA_DIR}")
logger.info(f"User subdirectory: {get_setting('user_subdir')}")
if file_handler:
    logger.info(f"Logging to file: {log_file_path}")
else:
    logger.warning("File logging is disabled due to setup error.")


def load_configuration():
    """Load the public Simkl client ID and the user's access token.

    Raises:
        ConfigurationError: If the Client ID or Access Token is missing.

    Returns:
        dict: The credentials dictionary.
    """
    logger.info("Loading application configuration...")
    creds = get_credentials()
    client_id = creds.get("client_id")
    access_token = creds.get("access_token")

    if not client_id:
        msg = "Client ID not found. Check installation/build or dev environment."
        logger.critical(f"Configuration Error: {msg}")
        raise ConfigurationError(msg)
    if not access_token:
        msg = "Access Token not found. Please run 'simkl-mps init' to authenticate."
        logger.critical(f"Configuration Error: {msg}")
        raise ConfigurationError(msg)

    logger.info("Application configuration loaded successfully.")
    return creds # Return the whole dictionary

class SimklScrobbler:
    """
    Main application class orchestrating media monitoring and Simkl scrobbling.
    """
    def __init__(self):
        """Initializes the SimklScrobbler instance."""
        bootstrap_credentials()
        self._lifecycle_lock = threading.RLock()
        self._starting = False
        self._stop_requested = False
        self.running = False
        self.client_id = None
        self.access_token = None
        self.monitor = Monitor(app_data_dir=APP_DATA_DIR)
        self.watch_history_manager = None # Added instance variable
        self.trakt_watcher = TraktSyncWatcher()
        logger.debug("SimklScrobbler instance created.")

    def initialize(self):
        """
        Initializes the scrobbler by loading configuration and processing backlog.

        Returns:
            bool: True if initialization is successful, False otherwise.
        """
        logger.info("Initializing Media Player Scrobbler for SIMKL core components...")
        try:
            # Load configuration - raises ConfigurationError on failure
            creds = load_configuration()
            self.client_id = creds.get("client_id")
            self.access_token = creds.get("access_token")

        except ConfigurationError as e:
             logger.error(f"Initialization failed: {e}")
             # Print user-friendly message based on the specific error
             print(f"ERROR: {e}", file=sys.stderr)
             return False
        except Exception as e:
            # Catch any other unexpected errors during loading
            logger.exception(f"Unexpected error during configuration loading: {e}")
            print(f"CRITICAL ERROR: An unexpected error occurred during initialization. Check logs.", file=sys.stderr)
            return False

        # Set credentials in the monitor using the loaded values
        self.monitor.set_credentials(
            self.client_id,
            self.access_token,
            account_type=creds.get("account_type"),
            settings_all=creds.get("settings_all")
        )

        # Initialize Watch History Manager early
        try:
            self.watch_history_manager = self.monitor.scrobbler.watch_history
            self.watch_history_manager.set_on_saved(
                self.trakt_watcher.notify_history_saved
            )
            logger.info("Watch History Manager initialized.")
        except Exception as e:
            logger.error(f"Failed to initialize Watch History Manager: {e}", exc_info=True)
            # Non-critical for core scrobbling, log and continue

        logger.info("Media Player Scrobbler for SIMKL core initialization complete.")
        return True

    def start(self):
        """Serialize startup against stop requests and signal handlers."""
        with self._lifecycle_lock:
            self._starting = True
            self._stop_requested = False
            try:
                try:
                    started = self._start_locked()
                except BaseException:
                    try:
                        self._stop_locked()
                    except BaseException:
                        logger.exception(
                            "Startup cleanup raised while stopping workers."
                        )
                    raise
                if self._stop_requested:
                    self._stop_locked()
                    return False
                return started
            finally:
                self._starting = False

    def _worker_is_alive(self, owner, attribute):
        worker = getattr(owner, attribute, None)
        return bool(worker and worker.is_alive())

    def _has_live_workers(self):
        monitor = getattr(self, "monitor", None)
        media_scrobbler = getattr(monitor, "scrobbler", None)
        trakt_watcher = getattr(self, "trakt_watcher", None)
        return any(
            (
                self._worker_is_alive(monitor, "monitor_thread"),
                self._worker_is_alive(media_scrobbler, "_offline_sync_thread"),
                self._worker_is_alive(trakt_watcher, "_thread"),
            )
        )

    def _start_locked(self):
        """
        Starts the media monitoring process in a separate thread.

        Returns:
            bool: True if the monitor thread starts successfully, False otherwise.
        """
        if self.running:
            logger.warning("Attempted to start scrobbler monitor, but it is already running.")
            return False
        if self._has_live_workers():
            logger.error(
                "Cannot restart while a prior runtime worker is still alive."
            )
            return False

        if (
            hasattr(self, "_runtime_instance_lock")
            and self._runtime_instance_lock is None
        ):
            runtime_lock = RuntimeInstanceLock(APP_DATA_DIR)
            if not runtime_lock.acquire():
                logger.error(
                    "Another simkl-mps runtime already owns this data directory."
                )
                return False
            self._runtime_instance_lock = runtime_lock

        self.running = True
        logger.info("Starting media player monitor...")

        if threading.current_thread() is threading.main_thread():
            logger.debug("Setting up signal handlers (SIGINT, SIGTERM).")
            signal.signal(signal.SIGINT, self._signal_handler)
            signal.signal(signal.SIGTERM, self._signal_handler)
        else:
             logger.warning("Not running in main thread, skipping signal handler setup.")

        if not self.monitor.start():
             logger.error("Failed to start the monitor thread.")
             self.stop()
             return False

        logger.info("Media player monitor thread started successfully.")
        if self._stop_requested:
            return False

        try:
            # Start background backlog sync *after* monitor is running
            logger.info("Starting background backlog synchronization thread...")
            self.monitor.scrobbler.start_offline_sync_thread() # Use default interval
            if self._stop_requested:
                return False

            # Trakt is optional. When configured, the same tray process watches the
            # local history file and pushes exact completed-watch events.
            self.trakt_watcher.start()
        except Exception:
            logger.exception("Failed to start a background provider worker.")
            self.stop()
            return False

        return True

    def stop(self):
        """Serialize shutdown against startup and other stop requests."""
        with self._lifecycle_lock:
            if self._starting:
                self._stop_requested = True
                self.running = False
                return False
            return self._stop_locked()

    def _stop_locked(self):
        """Stop monitoring, provider watchers, and the completion queue worker."""
        logger.info("Initiating scrobbler shutdown...")
        self.running = False
        workers_stopped = True
        trakt_watcher = getattr(self, "trakt_watcher", None)
        if trakt_watcher:
            try:
                if trakt_watcher.stop() is False:
                    workers_stopped = False
            except Exception:
                workers_stopped = False
                logger.exception("Failed to stop the Trakt sync watcher.")

        monitor = getattr(self, "monitor", None)
        if monitor:
            try:
                monitor.stop()
            except Exception:
                workers_stopped = False
                logger.exception("Failed to stop the media monitor.")
            monitor_thread = getattr(monitor, "monitor_thread", None)
            if monitor_thread and monitor_thread.is_alive():
                workers_stopped = False

            media_scrobbler = getattr(monitor, "scrobbler", None)
            if media_scrobbler and hasattr(
                media_scrobbler,
                "stop_offline_sync_thread",
            ):
                try:
                    if media_scrobbler.stop_offline_sync_thread() is False:
                        workers_stopped = False
                except Exception:
                    workers_stopped = False
                    logger.exception("Failed to stop the completion queue worker.")

        runtime_lock = getattr(self, "_runtime_instance_lock", None)
        if runtime_lock is not None and workers_stopped:
            runtime_lock.release()
            self._runtime_instance_lock = None
        elif runtime_lock is not None:
            logger.error(
                "Runtime ownership retained because a worker is still alive."
            )
        logger.info("Scrobbler shutdown complete.")
        return workers_stopped

    def _signal_handler(self, sig, frame):
        """Handles termination signals (SIGINT, SIGTERM) for graceful shutdown."""
        logger.warning(f"Received signal {signal.Signals(sig).name}. Initiating graceful shutdown...")
        self.running = False
        threading.Thread(
            target=self.stop,
            name="SignalShutdown",
            daemon=True,
        ).start()

def run_as_background_service():
    """
    Runs the Media Player Scrobbler for SIMKL as a background service.
    
    Similar to main() but designed for daemon/service operation without
    keeping the main thread active with a sleep loop.
    
    Returns:
        SimklScrobbler: The running scrobbler instance for the service manager to control.
    """
    logger.info("Starting Media Player Scrobbler for SIMKL as a background service.")
    runtime_lock = RuntimeInstanceLock(APP_DATA_DIR)
    if not runtime_lock.acquire():
        logger.error("Another simkl-mps runtime already owns this data directory.")
        return None

    scrobbler_instance = None
    try:
        bootstrap_credentials()
        scrobbler_instance = SimklScrobbler()
        scrobbler_instance._runtime_instance_lock = runtime_lock

        if not scrobbler_instance.initialize():
            logger.critical("Background service initialization failed.")
            runtime_lock.release()
            scrobbler_instance._runtime_instance_lock = None
            return None

        if not scrobbler_instance.start():
            logger.critical("Failed to start the scrobbler monitor thread in background mode.")
            if scrobbler_instance._runtime_instance_lock is not None:
                logger.critical(
                    "Background service retains runtime ownership because startup "
                    "cleanup left a worker alive."
                )
                return scrobbler_instance
            return None
    except BaseException:
        workers_stopped = True
        if scrobbler_instance is not None:
            try:
                workers_stopped = scrobbler_instance.stop() is not False
            except BaseException:
                workers_stopped = False
                logger.exception(
                    "Background startup cleanup raised while stopping workers."
                )
        if workers_stopped:
            runtime_lock.release()
        else:
            retain_failed_runtime(scrobbler_instance, runtime_lock)
            logger.critical(
                "Background startup failed with a live worker; runtime ownership "
                "was retained."
            )
        raise

    logger.info("simkl-mps background service started successfully.")
    return scrobbler_instance

def _run_foreground(scrobbler_instance):
    """
    Main entry point for running the Media Player Scrobbler for SIMKL directly.

    Initializes and starts the scrobbler, keeping the main thread alive
    until interrupted (e.g., by Ctrl+C).
    """
    logger.info("simkl-mps application starting in foreground mode.")

    if not scrobbler_instance.initialize():
        logger.critical("Application initialization failed. Exiting.")
        return 1, None

    if not scrobbler_instance.start():
        logger.critical("Failed to start the scrobbler monitor thread. Exiting.")
        if scrobbler_instance.stop() is False:
            return 1, scrobbler_instance
        return 1, None

    logger.info("Application running. Press Ctrl+C to stop.")
    
    while scrobbler_instance.running:
        try:
            time.sleep(1)
        except KeyboardInterrupt:
            logger.info("KeyboardInterrupt detected in main loop. Initiating shutdown...")
            break

    if scrobbler_instance.stop() is False:
        logger.critical(
            "Foreground shutdown timed out; runtime ownership remains held."
        )
        return 1, scrobbler_instance

    logger.info("simkl-mps application stopped.")
    return 0, None


def main():
    """Run the foreground application while owning its data directory."""
    runtime_lock = RuntimeInstanceLock(APP_DATA_DIR)
    if not runtime_lock.acquire():
        logger.critical("Another simkl-mps runtime already owns this data directory.")
        return 1
    ownership_transferred = False
    scrobbler_instance = None
    try:
        bootstrap_credentials()
        scrobbler_instance = SimklScrobbler()
        exit_code, failed_runtime = _run_foreground(scrobbler_instance)
        if failed_runtime is not None:
            retain_failed_runtime(failed_runtime, runtime_lock)
            ownership_transferred = True
        return exit_code
    except BaseException:
        workers_stopped = True
        if scrobbler_instance is not None:
            try:
                workers_stopped = scrobbler_instance.stop() is not False
            except BaseException:
                workers_stopped = False
                logger.exception(
                    "Foreground cleanup raised while stopping workers."
                )
        if not workers_stopped:
            retain_failed_runtime(scrobbler_instance, runtime_lock)
            ownership_transferred = True
            logger.critical(
                "Foreground failure left a live worker; runtime ownership was retained."
            )
        raise
    finally:
        if not ownership_transferred:
            runtime_lock.release()

if __name__ == "__main__":
    sys.exit(main())
