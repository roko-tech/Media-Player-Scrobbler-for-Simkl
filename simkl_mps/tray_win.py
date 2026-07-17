"""
Windows-specific System tray implementation for Media Player Scrobbler for SIMKL.
Uses pystray and tkinter for the UI elements.
"""

import io
import os
import re
import sys
import time
import threading
import logging
import webbrowser
import subprocess # Added for running updater script
import queue
from pathlib import Path
from PIL import Image # Keep PIL.Image for loading
from PIL import ImageTk
import pystray
import requests
from plyer import notification
import ctypes # Added for native Windows dialogs
import tkinter as tk
from tkinter import simpledialog, messagebox

# Import Base Class and common functions/constants
from simkl_mps.tray_base import TrayAppBase, get_simkl_scrobbler
from simkl_mps.config_manager import get_setting, DEFAULT_THRESHOLD # Keep for menu state check
from simkl_mps.main import APP_DATA_DIR # Keep for log/config paths

logger = logging.getLogger(__name__)

_ERROR_ALREADY_EXISTS = 183
_INSTANCE_MUTEX_NAME = "Local\\MediaPlayerScrobblerForSimkl-7f2c0b63"
_instance_mutex = None


_SIMKL_POSTER_ID = re.compile(r"^[A-Za-z0-9]+/[A-Za-z0-9]+$")
_SIMKL_POSTER_PREFIX = "https://simkl.in/posters/"
_MAX_POSTER_BYTES = 5 * 1024 * 1024


def _resolve_poster_url(poster):
    """Resolve only trusted Simkl poster references."""
    if not isinstance(poster, str):
        return None
    value = poster.strip()
    if _SIMKL_POSTER_ID.fullmatch(value):
        return f"{_SIMKL_POSTER_PREFIX}{value}_m.webp"
    if value.startswith(_SIMKL_POSTER_PREFIX) and ".." not in value:
        return value
    return None


def _cache_poster(app_data_dir, poster, simkl_id):
    """Download and validate a Simkl poster into the local display cache."""
    url = _resolve_poster_url(poster)
    if not url or simkl_id is None:
        return None

    cache_dir = Path(app_data_dir) / "poster-cache"
    cache_path = cache_dir / f"{simkl_id}.webp"
    if cache_path.is_file():
        return cache_path

    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        content = response.content
        if not content or len(content) > _MAX_POSTER_BYTES:
            raise ValueError("poster response is empty or too large")
        with Image.open(io.BytesIO(content)) as image:
            image.verify()
        cache_dir.mkdir(parents=True, exist_ok=True)
        temp_path = cache_path.with_suffix(".tmp")
        temp_path.write_bytes(content)
        temp_path.replace(cache_path)
        return cache_path
    except Exception as exc:
        logger.warning("Could not cache Simkl poster for receipt: %s", exc)
        return None


def _acquire_single_instance():
    """Hold one tray instance per Windows login session."""
    global _instance_mutex
    kernel32 = ctypes.windll.kernel32
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    handle = kernel32.CreateMutexW(None, False, _INSTANCE_MUTEX_NAME)
    if not handle:
        raise ctypes.WinError()
    if kernel32.GetLastError() == _ERROR_ALREADY_EXISTS:
        kernel32.CloseHandle(handle)
        return False
    _instance_mutex = handle
    return True


def _release_single_instance():
    global _instance_mutex
    if _instance_mutex:
        ctypes.windll.kernel32.CloseHandle(_instance_mutex)
        _instance_mutex = None


class TrayAppWin(TrayAppBase):
    """Windows System tray application for simkl-mps using pystray"""

    def __init__(self):
        super().__init__() # Call base class constructor
        self.tray_icon = None # Initialize tray_icon attribute
        self._exit_requested = False
        self._update_check_running = False # Initialize update check flag
        self._tk_queue: "queue.Queue[tuple[callable, queue.Queue]] | None" = None
        self._tk_thread: threading.Thread | None = None
        self._tk_root: tk.Tk | None = None
        self._receipt_window: tk.Toplevel | None = None
        self._receipt_after_id = None
        self._receipt_photo = None
        self._receipt_generation = 0
        self._setup_auto_update_if_needed() # Run platform-specific setup
        self._init_tk_thread()
        self.setup_icon()

    def setup_icon(self):
        """Setup the pystray system tray icon"""
        try:
            image = self.load_icon_for_status()
            
            self.tray_icon = pystray.Icon(
                "simkl-mps",
                image,
                "MPS for SIMKL",
                menu=self.create_menu()
            )
            logger.info("Tray icon setup successfully")
        except Exception as e:
            # Log exception type and full traceback for better debugging
            logger.error(f"Error setting up tray icon: {type(e).__name__} - {e}", exc_info=True)
            raise
    
    def load_icon_for_status(self):
        """Load the appropriate icon PIL.Image for the current status using the base class path finder."""
        icon_path_str = self._get_icon_path(status=self.status) # Use base class method

        if icon_path_str:
            try:
                icon_path = Path(icon_path_str)
                if icon_path.exists():
                    logger.debug(f"Loading tray icon from base path: {icon_path}")
                    # Ensure the image is loaded correctly, especially for ICO on Windows
                    img = Image.open(icon_path)
                    img.load() # Explicitly load image data
                    return img
                else:
                    logger.error(f"Icon path returned by base class does not exist: {icon_path}")
            except FileNotFoundError:
                logger.error(f"Icon file not found at path from base class: {icon_path_str}", exc_info=True)
            except Exception as e:
                # Catch potential PIL errors (e.g., UnidentifiedImageError)
                logger.error(f"Error loading icon from path {icon_path_str} with PIL: {type(e).__name__} - {e}", exc_info=True)
        else:
             logger.warning(f"Base class _get_icon_path did not return a path for status '{self.status}'.")

        # Fallback if base method fails or loading fails
        logger.warning("Falling back to generated image for tray icon.")
        return self._create_fallback_image()
    
    # _create_fallback_image is now in base class

    # get_status_text is now in base class

    def create_menu(self):
        """Create the pystray menu using the base class helper."""
        # Build the list of menu items using the base class method
        menu_items = self._build_pystray_menu_items()
        # Create the pystray Menu object from the list
        return pystray.Menu(*menu_items)

    def update_icon(self):
        """Update the tray icon and menu to reflect the current status"""
        if self.tray_icon:
            try:
                new_icon = self.load_icon_for_status()
                self.tray_icon.icon = new_icon
                self.tray_icon.menu = self.create_menu()
                status_map = {
                    "running": "Active", 
                    "paused": "Paused", 
                    "stopped": "Stopped", 
                    "error": "Error"
                }
                status_text = status_map.get(self.status, "Unknown")
                if self.status_details:
                    status_text += f" - {self.status_details}"
                
                self.tray_icon.title = f"MPS for SIMKL - {status_text}"
                
                logger.debug(f"Updated tray icon to status: {self.status}")
            except Exception as e:
                logger.error(f"Failed to update tray icon: {e}", exc_info=True)

    # open_config_dir is now in base class

    def show_about(self, _=None):
        """Show application information using Tkinter dialog."""
        try:
            about_text = self._build_about_text()
            self._show_info_dialog("About MPS for SIMKL", about_text)

        except Exception as e:
            logger.error(f"Error showing about dialog: {e}")
            self.show_notification("About", "Media Player Scrobbler for SIMKL")
        return 0

    def _init_tk_thread(self):
        """Start a dedicated Tkinter thread to safely run dialogs."""
        if self._tk_thread and self._tk_thread.is_alive():
            return

        self._tk_queue = queue.Queue()
        ready_event = threading.Event()

        def _tk_mainloop():
            try:
                root = tk.Tk()
                root.withdraw()
                root.attributes("-topmost", True)
                self._apply_tk_icon(root)
                self._tk_root = root
                ready_event.set()

                def _poll_queue():
                    if not self._tk_queue:
                        return
                    try:
                        while True:
                            func, result_queue = self._tk_queue.get_nowait()
                            try:
                                result = func()
                                result_queue.put((True, result))
                            except Exception as exc:
                                result_queue.put((False, exc))
                    except queue.Empty:
                        pass
                    root.after(50, _poll_queue)

                root.after(0, _poll_queue)
                root.mainloop()
            except Exception as exc:
                logger.error(f"Tkinter dialog thread failed: {exc}", exc_info=True)
            finally:
                self._tk_root = None

        self._tk_thread = threading.Thread(target=_tk_mainloop, daemon=True)
        self._tk_thread.start()
        ready_event.wait(timeout=5)

    def _apply_tk_icon(self, root: tk.Tk) -> None:
        """Apply the simkl icon to Tk dialogs so they don't show the default Tk name/icon."""
        try:
            icon_path = self._get_icon_path(status=self.status)
            if not icon_path:
                return

            icon_path_lower = str(icon_path).lower()
            if icon_path_lower.endswith(".ico"):
                root.iconbitmap(icon_path)
                return

            try:
                image = Image.open(icon_path)
                image.load()
                tk_image = ImageTk.PhotoImage(image)
                root.iconphoto(True, tk_image)
                # Keep a reference to prevent garbage collection
                self._tk_icon_image = tk_image
            except Exception as img_err:
                logger.debug(f"Failed to set Tk icon from {icon_path}: {img_err}")
        except Exception as e:
            logger.debug(f"Unable to apply Tk icon: {e}")

    def _run_on_tk_thread(self, func, default=None):
        """Execute a callable on the Tk thread and return its result."""
        if not self._tk_queue or not self._tk_thread or not self._tk_thread.is_alive():
            self._init_tk_thread()

        if not self._tk_queue:
            return default

        result_queue: "queue.Queue[tuple[bool, object]]" = queue.Queue()
        self._tk_queue.put((func, result_queue))

        try:
            ok, result = result_queue.get(timeout=30)
            if ok:
                return result
            raise result
        except Exception as exc:
            logger.error(f"Tkinter dialog execution failed: {exc}", exc_info=True)
            return default

    def handle_identification_receipt(self, receipt):
        self._last_receipt = dict(receipt)
        self._show_receipt_async(self._last_receipt)

    def handle_trakt_sync_result(self, result, event):
        media_info = {}
        media_scrobbler = self._get_media_scrobbler()
        if media_scrobbler and event.get("simkl_id"):
            _, media_info = media_scrobbler.media_cache.get_by_simkl_id(event["simkl_id"])
            media_info = media_info or {}

        receipt = {
            "kind": "completion",
            "title": event.get("title") or media_info.get("movie_name") or "Unknown media",
            "year": media_info.get("year"),
            "media_type": "anime" if event.get("is_anime") else event.get("kind"),
            "season": event.get("season"),
            "episode": event.get("episode"),
            "display_season": media_info.get("season_display") or event.get("season"),
            "display_episode": media_info.get("episode_display") or event.get("episode"),
            "simkl_id": event.get("simkl_id"),
            "poster_url": media_info.get("poster_url") or media_info.get("poster"),
            "simkl_status": "Accepted",
            "trakt_status": "Accepted" if result.ok and result.pending == 0 else "Pending retry",
            "summary": result.summary,
        }
        self._last_receipt = receipt
        self._show_receipt_async(receipt)

    def show_last_receipt(self, _=None):
        if not self._last_receipt:
            self.show_notification("Watch Sync Receipt", "No media receipt is available yet.")
            return
        self._show_receipt_async(dict(self._last_receipt))

    def _show_receipt_async(self, receipt):
        self._receipt_generation += 1
        generation = self._receipt_generation
        threading.Thread(
            target=self._prepare_and_show_receipt,
            args=(receipt, generation),
            name="receipt-poster-loader",
            daemon=True,
        ).start()

    def _prepare_and_show_receipt(self, receipt, generation):
        poster_path = _cache_poster(
            APP_DATA_DIR,
            receipt.get("poster_url"),
            receipt.get("simkl_id"),
        )
        if generation != self._receipt_generation:
            return
        prepared = dict(receipt)
        prepared["poster_path"] = poster_path
        self._run_on_tk_thread(lambda: self._render_receipt(prepared, generation))

    @staticmethod
    def _episode_code(season, episode):
        if season is None or episode is None:
            return None
        try:
            return f"S{int(season):02d}E{int(episode):02d}"
        except (TypeError, ValueError):
            return None

    def _episode_mapping_text(self, receipt):
        if receipt.get("media_type") in ("movie", None):
            return "Movie"
        display_code = self._episode_code(
            receipt.get("display_season"), receipt.get("display_episode")
        )
        tracker_code = self._episode_code(receipt.get("season"), receipt.get("episode"))
        if display_code and tracker_code and display_code != tracker_code:
            return f"File: {display_code}   Simkl: {tracker_code}"
        return display_code or tracker_code or "Episode"

    def _dismiss_receipt(self):
        if self._receipt_after_id and self._tk_root:
            try:
                self._tk_root.after_cancel(self._receipt_after_id)
            except tk.TclError:
                pass
        self._receipt_after_id = None
        if self._receipt_window:
            try:
                self._receipt_window.destroy()
            except tk.TclError:
                pass
        self._receipt_window = None
        self._receipt_photo = None

    def _accept_receipt(self):
        media_scrobbler = self._get_media_scrobbler()
        if media_scrobbler:
            media_scrobbler.accept_current_identification()
        self._dismiss_receipt()

    def _reject_receipt(self):
        media_scrobbler = self._get_media_scrobbler()
        rejected = bool(media_scrobbler and media_scrobbler.reject_current_identification())
        self._dismiss_receipt()
        if rejected:
            threading.Thread(
                target=self.set_current_file_override,
                name="receipt-match-correction",
                daemon=True,
            ).start()

    def _render_receipt(self, receipt, generation):
        if generation != self._receipt_generation or not self._tk_root:
            return
        self._dismiss_receipt()

        window = tk.Toplevel(self._tk_root)
        self._receipt_window = window
        window.overrideredirect(True)
        window.attributes("-topmost", True)
        window.configure(background="#334155")

        width, height = 480, 236
        screen_width = window.winfo_screenwidth()
        window.geometry(f"{width}x{height}+{max(12, screen_width - width - 24)}+24")

        body = tk.Frame(window, bg="#111827", padx=10, pady=10)
        body.pack(fill="both", expand=True, padx=1, pady=1)

        poster_frame = tk.Frame(body, width=128, height=192, bg="#1f2937")
        poster_frame.pack(side="left", fill="y")
        poster_frame.pack_propagate(False)
        poster_path = receipt.get("poster_path")
        if poster_path:
            try:
                with Image.open(poster_path) as source:
                    poster = source.convert("RGB")
                    poster.thumbnail((128, 192), Image.Resampling.LANCZOS)
                self._receipt_photo = ImageTk.PhotoImage(poster)
                tk.Label(poster_frame, image=self._receipt_photo, bg="#1f2937").pack(
                    expand=True
                )
            except Exception as exc:
                logger.warning("Could not display cached receipt poster: %s", exc)
        if self._receipt_photo is None:
            tk.Label(
                poster_frame,
                text="NO\nPOSTER",
                fg="#94a3b8",
                bg="#1f2937",
                font=("Segoe UI", 10, "bold"),
            ).pack(expand=True)

        details = tk.Frame(body, bg="#111827", padx=14)
        details.pack(side="left", fill="both", expand=True)
        heading = "IDENTIFIED" if receipt.get("kind") == "identification" else "WATCH SYNC RECEIPT"
        tk.Label(
            details,
            text=heading,
            fg="#60a5fa",
            bg="#111827",
            font=("Segoe UI", 9, "bold"),
            anchor="w",
        ).pack(fill="x")

        year = f" ({receipt['year']})" if receipt.get("year") else ""
        tk.Label(
            details,
            text=f"{receipt.get('title', 'Unknown media')}{year}",
            fg="#f8fafc",
            bg="#111827",
            font=("Segoe UI Semibold", 13),
            wraplength=300,
            justify="left",
            anchor="w",
        ).pack(fill="x", pady=(5, 3))
        tk.Label(
            details,
            text=self._episode_mapping_text(receipt),
            fg="#cbd5e1",
            bg="#111827",
            font=("Segoe UI", 10),
            anchor="w",
        ).pack(fill="x")

        if receipt.get("kind") == "identification":
            tk.Label(
                details,
                text=f"Matched by: {receipt.get('match_method', 'Simkl')}",
                fg="#94a3b8",
                bg="#111827",
                font=("Segoe UI", 9),
                anchor="w",
            ).pack(fill="x", pady=(7, 0))
            tk.Label(
                details,
                text="Playback and syncing continue if untouched.",
                fg="#64748b",
                bg="#111827",
                font=("Segoe UI", 8),
                anchor="w",
            ).pack(fill="x", pady=(2, 8))
            buttons = tk.Frame(details, bg="#111827")
            buttons.pack(side="bottom", fill="x")
            tk.Button(
                buttons,
                text="Looks Right",
                command=self._accept_receipt,
                bg="#166534",
                fg="white",
                activebackground="#15803d",
                activeforeground="white",
                relief="flat",
                padx=12,
            ).pack(side="left")
            tk.Button(
                buttons,
                text="Wrong Match",
                command=self._reject_receipt,
                bg="#7f1d1d",
                fg="white",
                activebackground="#991b1b",
                activeforeground="white",
                relief="flat",
                padx=12,
            ).pack(side="left", padx=(8, 0))
        else:
            accepted = receipt.get("trakt_status") == "Accepted"
            tk.Label(
                details,
                text=f"Simkl: {receipt.get('simkl_status', 'Unknown')}",
                fg="#4ade80",
                bg="#111827",
                font=("Segoe UI Semibold", 10),
                anchor="w",
            ).pack(fill="x", pady=(10, 1))
            tk.Label(
                details,
                text=f"Trakt: {receipt.get('trakt_status', 'Unknown')}",
                fg="#4ade80" if accepted else "#fbbf24",
                bg="#111827",
                font=("Segoe UI Semibold", 10),
                anchor="w",
            ).pack(fill="x")
            tk.Label(
                details,
                text=receipt.get("summary") or "",
                fg="#94a3b8",
                bg="#111827",
                font=("Segoe UI", 8),
                wraplength=300,
                justify="left",
                anchor="w",
            ).pack(fill="x", pady=(5, 0))
            tk.Button(
                details,
                text="Dismiss",
                command=self._dismiss_receipt,
                bg="#334155",
                fg="white",
                activebackground="#475569",
                activeforeground="white",
                relief="flat",
                padx=12,
            ).pack(side="bottom", anchor="w")

        self._receipt_after_id = self._tk_root.after(8000, self._dismiss_receipt)
        window.lift()

    def _show_info_dialog(self, title, message):
        """Windows override: show informational dialog via Tk thread."""
        def _dialog():
            parent = self._tk_root
            if parent:
                parent.lift()
                parent.focus_force()
            return messagebox.showinfo(str(title), str(message), parent=parent)

        self._run_on_tk_thread(_dialog)

    def _show_confirmation_dialog(self, title, message):
        """Windows override: show Yes/No confirmation via Tk thread."""
        def _dialog():
            parent = self._tk_root
            if parent:
                parent.lift()
                parent.focus_force()
            return messagebox.askyesno(str(title), str(message), parent=parent)

        return bool(self._run_on_tk_thread(_dialog, default=False))

    def show_help(self, _=None):
        """Show help information and fallback to native Windows dialog."""
        try:
            # Open documentation or show help dialog
            help_url = "https://github.com/ByteTrix/Media-Player-Scrobbler-for-Simkl/wiki"
            webbrowser.open(help_url)
        except Exception as e:
            logger.error(f"Error showing help: {e}")
            
            # Fallback help text if browser doesn't open
            help_text = """Media Player Scrobbler for SIMKL

This application automatically tracks what you watch in supported media players and updates your SIMKL account.

Supported players:
- VLC
- MPV
- MPC-HC

Tips:
- Make sure you've authorized with SIMKL
- The app runs in your system tray
- Check logs if you encounter problems"""

            self._show_info_dialog("Help", help_text)
        return 0

    # open_simkl is now in base class
    # open_simkl_history is now in base class

    def check_updates_thread(self, _=None):
        """Wrapper to run the Windows update check logic in a separate thread"""
        # Prevent multiple checks running simultaneously
        if hasattr(self, '_update_check_running') and self._update_check_running:
            logger.warning("Update check already in progress.")
            return
        self._update_check_running = True
        threading.Thread(target=self._check_updates_logic, daemon=True).start()

    def _check_updates_logic(self):
        """Check for updates using the PowerShell script (Windows specific)"""
        logger.info("Checking for updates...")
        self.show_notification("Checking for Updates", "Looking for updates to MPS for SIMKL...")

        current_version = self._get_app_version()

        system = sys.platform.lower()
        updater_script = 'updater.ps1' if system == 'win32' else 'updater.sh' # Adapt for other OS if needed
        updater_path = self._get_updater_path(updater_script)

        if not updater_path or not updater_path.exists():
            logger.error(f"Updater script not found: {updater_path}")
            self.show_notification("Update Error", "Updater script not found.")
            self.update_icon() # Refresh menu
            self._update_check_running = False
            return

        try:
            if system == 'win32':
                # Add -Silent parameter to prevent the updater from showing its own notifications
                command = [
                    "powershell.exe",
                    "-NoProfile",
                    "-NonInteractive",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(updater_path),
                    "-CheckOnly",
                    "-Silent"
                ]
                # Hide PowerShell window
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                startupinfo.wShowWindow = 0 # SW_HIDE
                creationflags = subprocess.CREATE_NO_WINDOW
            else:
                # Basic command for sh script (adapt if needed)
                command = ["bash", str(updater_path), "--check-only", "--silent"] # Assuming sh script supports --silent
                startupinfo = None
                creationflags = 0

            process = subprocess.run(
                command,
                capture_output=True,
                text=True,
                check=False, # Don't raise exception on non-zero exit code
                startupinfo=startupinfo,
                creationflags=creationflags,
                encoding='utf-8' # Ensure correct decoding
            )

            stdout = process.stdout.strip()
            stderr = process.stderr.strip()
            exit_code = process.returncode

            parsed_output = ""
            if stdout:
                for line in stdout.splitlines():
                    line = line.strip()
                    if line.startswith("UPDATE_AVAILABLE:") or line.startswith("NO_UPDATE:"):
                        parsed_output = line
                        break

            logger.info(f"Update check script exited with code: {exit_code}")
            logger.debug(f"Update check stdout: {stdout}")
            if stderr:
                logger.error(f"Update check stderr: {stderr}")

            # Process based on exit code first
            if exit_code != 0:
                 # Exit code 1 from PS script means check failed
                if exit_code == 1 and system == 'win32':
                    logger.error("Update check failed (script exit code 1).")
                    self.show_notification("Update Check Failed", "Could not check for updates. Please try again later or check logs.")
                else:
                    # General script execution error
                    logger.error(f"Update check script failed with exit code {exit_code}. Stderr: {stderr}")
                    self.show_notification("Update Error", f"Failed to run update check script (Code: {exit_code}).")

            # Process stdout if exit code was 0
            elif parsed_output.startswith("UPDATE_AVAILABLE:"):
                try:
                    parts = parsed_output.split(" ", 2) # UPDATE_AVAILABLE: <version> <url>
                    new_version = parts[1]
                    url = parts[2]
                    logger.info(f"Update found: Version {new_version}")
                    
                    # First show notification that update is available with both versions
                    self.show_notification("Update Available", 
                        f"New version available!\nCurrent: {current_version}\nNew: {new_version}\n\nOpening download page...")
                    
                    # Short delay to ensure notification appears before browser opens
                    time.sleep(1)
                    
                    # Then open the release page automatically
                    webbrowser.open(url)
                    
                except IndexError:
                    logger.error(f"Could not parse UPDATE_AVAILABLE string: {parsed_output}")
                    self.show_notification("Update Error", "Failed to parse update information.")
            elif parsed_output.startswith("NO_UPDATE:"):
                try:
                    version = parsed_output.split(" ", 1)[1]
                    logger.info(f"No update available. Current version: {version}")
                    self.show_notification("No Updates Available", f"You are already running the latest version ({version}).")
                except IndexError:
                     logger.error(f"Could not parse NO_UPDATE string: {parsed_output}")
                     self.show_notification("No Updates Available", "You are already running the latest version.")
            else:
                # Unexpected output
                logger.warning(f"Unexpected output from update check script: {stdout}")
                self.show_notification("Update Check Info", "Update check completed with unclear results. Check logs.")

        except FileNotFoundError:
             logger.error(f"Error running update check: Command not found (powershell/bash?).")
             self.show_notification("Update Error", "Required command (powershell/bash) not found.")
        except Exception as e:
            logger.error(f"Error during update check: {e}", exc_info=True)
            self.show_notification("Update Error", f"An error occurred during update check: {e}")
        finally:
            self.update_icon() # Refresh menu state
            self._update_check_running = False

    # _get_updater_path is now in base class
    # _get_icon_path is now in base class

    def show_notification(self, title, message):
        """Show a desktop notification using winotify (persistent) or plyer as fallback on Windows, with cross-platform support."""
        if get_setting('disable_notifications', False):
            logger.debug(f"Windows notification suppressed by user setting: {title}")
            return 0

        logger.debug(f"Attempting to show notification: {title} - {message}")
        
        # Try winotify for persistent Action Center notifications
        if sys.platform == 'win32':
            try:
                from winotify import Notification
                # icon_path = self._get_icon_path(self.status)  # Use the same icon logic as tray
                toast = Notification(
                    app_id="kavin.simkl-mps",  # Must match AppUserModelID in installer
                    title=title,
                    msg=message,
                    # icon=icon_path if icon_path else None
                )
                toast.show()
                logger.debug("Notification sent via winotify with AppUserModelID and icon")
                return
            except ImportError:
                logger.info("winotify not installed, falling back to plyer/other methods.")
            except Exception as e:
                logger.warning(f"winotify notification failed: {e}")
        
        # Fallback: plyer (not persistent in Action Center)
        try:
            from plyer import notification
            notification.notify(
                title=title,
                message=message,
                app_name="MPS for SIMKL",
                timeout=10
            )
            logger.debug("Icon-less notification sent successfully via plyer")
            return
        except Exception as plyer_err:
            logger.warning(f"Basic notification failed: {plyer_err}")
        
        # Fallback: PowerShell or Windows Forms
        try:
            if sys.platform == 'win32':
                # Windows: Try PowerShell with no icon references
                try:
                    import subprocess
                    script = f'''
                    Add-Type -AssemblyName System.Windows.Forms
                    $notification = New-Object System.Windows.Forms.NotifyIcon
                    $notification.Text = "MPS for SIMKL"
                    $notification.Visible = $true
                    $notification.BalloonTipTitle = "{title}"
                    $notification.BalloonTipText = "{message}"
                    $notification.ShowBalloonTip(10000)
                    Start-Sleep -Seconds 5
                    $notification.Dispose()
                    '''
                    with open("temp_notify.ps1", "w") as f:
                        f.write(script)
                    subprocess.Popen(
                        ["powershell", "-ExecutionPolicy", "Bypass", "-File", "temp_notify.ps1"],
                        shell=True,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        creationflags=subprocess.CREATE_NO_WINDOW
                    )
                    logger.debug("Windows System.Windows.Forms notification sent")
                    return
                except Exception as win_err:
                    logger.warning(f"Alternative Windows notification failed: {win_err}")
                # Windows MessageBox fallback
                try:
                    import ctypes
                    MessageBox = ctypes.windll.user32.MessageBoxW
                    MB_ICONINFORMATION = 0x40
                    MessageBox(None, message, title, MB_ICONINFORMATION)
                    logger.debug("Windows MessageBox notification shown")
                    return
                except Exception as mb_err:
                    logger.warning(f"Windows MessageBox notification failed: {mb_err}")
        except Exception as native_err:
            logger.error(f"All native notification methods failed: {native_err}")
        
        # Final fallback: Print to console
        print(f"\n🔔 NOTIFICATION: {title}\n{message}\n")
        logger.info(f"Notification displayed in console: {title} - {message}")
        return 0

    def run(self):
        """Run the pystray application"""
        logger.info("Starting Media Player Scrobbler for SIMKL in tray mode")
        self.scrobbler = get_simkl_scrobbler()()
        initialized = self.scrobbler.initialize()
        if initialized:
            started = self.start_monitoring()
            if not started:
                self.update_status("error", "Failed to start monitoring")
        else:
            self.update_status("error", "Failed to initialize")
            
        self._run_tray_loop()

    def _run_tray_loop(self, retry_delay=2):
        """Keep the tray available unless the user explicitly exits."""
        while not self._exit_requested:
            try:
                self.tray_icon.run()
            except Exception as e:
                logger.error(f"Error running tray icon: {e}", exc_info=True)

            if self._exit_requested:
                break

            logger.error(
                "Tray event loop exited unexpectedly; recreating the tray icon in %ss.",
                retry_delay,
            )
            time.sleep(retry_delay)
            if not self._exit_requested:
                self.setup_icon()

    # start_monitoring is now in base class
    # stop_monitoring is now in base class
    # process_backlog is now in base class
    # open_logs is now in base class

    # --- Watch Threshold Implementation ---

    def _ask_custom_threshold_dialog(self, current_threshold: int) -> int | None:
        """Windows implementation to ask for threshold using Tkinter dialog."""
        def _dialog():
            parent = self._tk_root
            if parent:
                parent.lift()
                parent.focus_force()
            return simpledialog.askinteger(
                "Set Watch Threshold",
                f"Enter watch completion threshold (%):\n(Current: {current_threshold}%)",
                parent=parent,
                minvalue=1,
                maxvalue=100,
                initialvalue=current_threshold
            )

        return self._run_on_tk_thread(_dialog, default=None)

    def _ask_directory_filter_dialog(self, title: str, current_value: str, help_text: str) -> str | None:
        """Windows implementation to ask for allow/deny directory filters."""
        def _dialog():
            parent = self._tk_root
            if parent:
                parent.lift()
                parent.focus_force()
            initial_value = current_value.replace("\n", "; ") if current_value else ""
            prompt = f"{help_text}\n\nSeparate entries with commas or semicolons."
            return simpledialog.askstring(
                str(title),
                prompt,
                parent=parent,
                initialvalue=initial_value
            )

        return self._run_on_tk_thread(_dialog, default=None)

    # _set_preset_threshold is now in base class
    # set_custom_watch_threshold is now in base class

    # --- End Watch Threshold Implementation ---

    def exit_app(self, _=None):
        """Exit the pystray application"""
        logger.info("Exiting application from tray")
        self._exit_requested = True
        try:
            if self.monitoring_active:
                self.stop_monitoring()
        finally:
            if self.tray_icon:
                self.tray_icon.stop()
        return 0

    def _setup_auto_update_if_needed(self):
        """Set up auto-updates if this is the first run"""
        try:
            import platform
            import subprocess
            import os
            from pathlib import Path
            
            config_dir = Path.home() / ".config" / "simkl-mps"
            first_run_file = config_dir / "first_run"
            
            # Only run if the first_run file exists
            if first_run_file.exists():
                system = platform.system().lower()
                
                if system == 'darwin':  # macOS
                    # The LaunchAgent should already be set up by the installer
                    # Just run the updater with the first-run check flag
                    updater_path = self._get_updater_path('updater.sh')
                    if updater_path.exists():
                        subprocess.Popen(['bash', str(updater_path), '--check-first-run'])
                
                elif system.startswith('linux'):
                    # For Linux, check if systemd is available and if the timer is set up
                    updater_path = self._get_updater_path('updater.sh')
                    setup_script_path = self._get_updater_path('setup-auto-update.sh')
                    
                    if updater_path.exists():
                        # Run the updater with the first-run check flag
                        subprocess.Popen(['bash', str(updater_path), '--check-first-run'])
                    
                    # If setup script exists and systemd is available but timer not set up,
                    # ask the user if they want to enable auto-updates
                    if setup_script_path.exists():
                        import tkinter as tk
                        from tkinter import messagebox
                        
                        systemd_user_dir = Path.home() / ".config" / "systemd" / "user"
                        timer_file = systemd_user_dir / "simkl-mps-updater.timer"
                        
                        if not timer_file.exists():
                            def show_auto_update_dialog():
                                dialog_root = tk.Tk()
                                dialog_root.withdraw()
                                dialog_root.attributes("-topmost", True)
                                
                                # Add protocol handler for window close button
                                dialog_root.protocol("WM_DELETE_WINDOW", lambda: dialog_root.destroy())
                                
                                # Ask user about enabling auto-updates
                                result = messagebox.askyesno(
                                    "MPSS Auto-Update", 
                                    "Would you like to enable weekly automatic update checks?",
                                    parent=dialog_root
                                )
                                
                                # Process the result before destroying the root
                                if result:
                                    # Run the setup script
                                    subprocess.run(['bash', str(setup_script_path)])
                                
                                # Ensure dialog is destroyed
                                dialog_root.destroy()
                            
                            # Run dialog in a separate thread to avoid blocking
                            dialog_thread = threading.Thread(target=show_auto_update_dialog, daemon=True)
                            dialog_thread.start()
                            dialog_thread.join(timeout=10)  # Wait for dialog with timeout
                
                # Remove the first_run file regardless of outcome
                first_run_file.unlink(missing_ok=True)
        
        except Exception as e:
            logger.error(f"Error setting up auto-updates: {e}")

    # _setup_auto_update_if_needed remains platform-specific for now

    def check_first_run(self):
        """Windows-specific check for first run using registry"""
        try:
            # Create a registry key to track app states on Windows
            if sys.platform == 'win32':
                import winreg
                try:
                    # Try to open the registry key
                    registry_path = r"Software\kavin\Media Player Scrobbler for SIMKL"
                    key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, registry_path, 0, 
                                        winreg.KEY_READ | winreg.KEY_WRITE)
                    
                    # Check if this is the first run
                    try:
                        # If we can read the FirstRun value, it's not the first run
                        first_run = winreg.QueryValueEx(key, "FirstRun")[0]
                        self.is_first_run = False
                    except FileNotFoundError:
                        # If FirstRun value doesn't exist, this is the first run
                        self.is_first_run = True
                        winreg.SetValueEx(key, "FirstRun", 0, winreg.REG_DWORD, 1)
                    except WindowsError:
                        # If there's any other error, assume it's not first run
                        self.is_first_run = False
                        
                    winreg.CloseKey(key)
                    
                except FileNotFoundError:
                    # If the key doesn't exist, create it and mark as first run
                    key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, registry_path)
                    winreg.SetValueEx(key, "FirstRun", 0, winreg.REG_DWORD, 1)
                    winreg.CloseKey(key)
                    self.is_first_run = True
                except Exception as e:
                    logger.warning(f"Error checking first run status in registry: {e}")
                    # Assume not first run on error
                    self.is_first_run = False
            
            logger.debug(f"First run check result: {self.is_first_run}")
            
        except Exception as e:
            logger.error(f"Unexpected error in first run check: {e}")
            self.is_first_run = False  # Default to not showing the notification on error

def run_tray_app():
    """Run the Windows tray application"""
    if not _acquire_single_instance():
        logger.warning("Another MPS tray instance is already running; exiting duplicate launch.")
        return 0
    try:
        app = TrayAppWin()
        app.run()
    except Exception as e:
        # Log the full traceback for critical startup errors
        logger.error(f"Critical error preventing tray app startup: {type(e).__name__} - {e}", exc_info=True)
        print(f"Failed to start in tray mode: {e}")
        print("Falling back to console mode.")
        
        # Only import SimklScrobbler here to avoid circular imports
        from simkl_mps.main import SimklScrobbler
        
        scrobbler = SimklScrobbler()
        if scrobbler.initialize():
            print("Scrobbler initialized. Press Ctrl+C to exit.")
            if scrobbler.start():
                try:
                    while scrobbler.running:
                        time.sleep(1)
                except KeyboardInterrupt:
                    scrobbler.stop()
                    print("Stopped monitoring.")
    finally:
        _release_single_instance()

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, 
                      format='%(asctime)s - %(levelname)s - %(name)s - %(message)s')
    sys.exit(run_tray_app())
