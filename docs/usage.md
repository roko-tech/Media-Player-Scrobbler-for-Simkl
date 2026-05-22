# 🎮 Usage Guide

This guide explains how to use MPS for SIMKL to track your media and sync with your SIMKL profile.

- For installation, see the [Windows Guide](windows-guide.md), [Linux Guide](linux-guide.md), or [macOS Guide](mac-guide.md).
- For player setup, see the [Media Players Guide](media-players.md).

## 🏁 Getting Started

1. Install the app for your platform (see guides above).
2. Authenticate with SIMKL on first run.
3. **Configure your media players** (see [Media Players Guide](media-players.md)).
4. Play media in your configured player. The app tracks and syncs progress automatically.

## 🖥️ Windows (EXE)
- Just install and launch. The app runs in the tray —no commands needed.
- Use the tray icon for status and controls.

## 🐧 Linux (pipx)
- Install with pipx (see [Linux Guide](linux-guide.md)).
- Start with `simkl-mps tray` or `simkl-mps start`.
- Tray icon provides controls and status.

## 🍏 Mac (pip, experimental)
- Install with pip (see [macOS Guide](mac-guide.md)).
- Start with `simkl-mps tray` or `simkl-mps start`.
- Tray icon provides controls and status.
- 
> Note: Mac support is experimental.

## 📺 TV Shows & Anime Tracking

MPS for SIMKL supports movies, TV shows, and anime.

- Episode detection and scrobbling work in supported players.
- All media types appear in local and online history.
- Use clear filenames for best matching (e.g. `Show.Name.S01E02.mkv`, `Anime.Title.12.mp4`).

---

## 📊 Local Watch History Viewer

A new local watch history page is available! Open the `watch-history-viewer` folder in your browser to:
- Browse all your watched movies, TV shows, and anime
- Search, filter, and sort your history
- View statistics and trends (charts, breakdowns)
- Switch between grid/list views and dark/light themes

See [Local Watch History](watch-history.md) for full details.

## 🔔 Tray Status Icons

MPS for SIMKL uses the system tray/notification area to show its current status across all platforms:

| Icon | Status | Description |
|------|--------|-------------|
| ![Running](../simkl_mps/assets/simkl-mps-running.png) | **Running** | App is actively monitoring and ready to track media playback |
| ![Paused](../simkl_mps/assets/simkl-mps-paused.png) | **Paused** | Tracking is temporarily paused, no new media will be scrobbled |
| ![Stopped](../simkl_mps/assets/simkl-mps-stopped.png) | **Stopped** | App is inactive and not tracking (but still running in tray) |
| ![Error](../simkl_mps/assets/simkl-mps-error.png) | **Error** | There's an issue with the app (authentication, API, etc.) |

Right-click the tray icon to access the app menu with the following sections:

**Main Actions:**
- **Start/Pause Tracking:** Toggle tracking on demand
- **Status:** View current monitoring and connection state

**Scrobbling** - Recovery and threshold controls:
- **Retry Last Scrobble:** Clears cache for the active file and attempts to re-identify and scrobble it. Use when the wrong title/episode appears.
- **Sync Backlog Now:** Immediately processes any offline scrobbles waiting in backlog.
- **Completion Threshold:** Quickly switch between preset watch thresholds (65%, 80%, 90%) or define a custom percentage.
- **Turn Notifications Off:** Disable all desktop notifications from the app (checkmark means notifications are off).
- **Open Local Watch History:** Browse your tracked movies, shows, and anime in the local viewer.

**SIMKL** - Account and service management:
- **Authenticate / Re-authenticate:** Sign in to SIMKL or refresh an expired token.
- **Open Website:** Visit the SIMKL website.
- **Open Watch History:** View your watch history on SIMKL.

**Maintenance** - Logs, data, and cache management:
- **Open Logs:** View application and playback logs.
- **Open Data Folder:** Access the application data directory.
- **Clear Backlog:** Delete pending offline scrobbles.
- **Clear Cache:** Remove media identification cache while preserving logs and settings.
- **Clear Watch History:** Remove the local `watch_history.json` file and viewer data without affecting your SIMKL account.
- **Clear Logs:** Reset application and playback logs to capture a fresh session for debugging.
- **Reset App Data (Danger):** Perform a full reset. Use only when you need a clean re-authentication; the app will exit afterward.

**More** - Additional utilities:
- **Donate ❤️:** Support the project.
- **Check for Updates:** Check if a newer version is available.
- **Help:** Open help documentation.
- **About:** View application information.

**Exit:** Close the application

## 🛠️ Common Operations

- **Tray:** Right-click for menu, status, and controls.
- **CLI:**
  ```bash
  simkl-mps start        # Start in background
  simkl-mps tray         # Start with tray UI
  simkl-mps status       # Check status
  simkl-mps stop         # Stop the app
  simkl-mps --help       # Help
  ```

## 📝 Tips
- Always configure your media players for best results ([Media Players Guide](media-players.md)).
- Use clear filenames: `Movie Title (Year).ext`.
- Use `allow_dirs` / `deny_dirs` in `settings.json` to whitelist or blacklist folders (see [Advanced & Developer Guide](configuration.md)).
- Glob patterns like `**/*.mkv` are supported in allow/deny lists.
- For troubleshooting, see the [Troubleshooting Guide](troubleshooting.md).
- For advanced options, see [Advanced & Developer Guide](configuration.md).
- For planned features, see the [Todo List](todo.md).
