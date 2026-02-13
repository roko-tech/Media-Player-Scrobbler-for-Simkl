# 🍏 macOS Guide (Experimental)

> macOS support is experimental. Some features may be limited.

## 🏁 Installation

- Requires Python 3.9+ and pip.
- Recommended: Use a virtual environment or pipx.

```bash
pip install "simkl-mps[macos]"
# or with pipx
pipx install "simkl-mps[macos]"
```

## 🚀 First Run

1. Start the app:
   ```bash
   simkl-mps tray
   # or
   simkl-mps start
   ```
2. Authenticate with SIMKL when prompted.
3. The app runs in the system tray (menu bar).

## 🎛️ Media Player Configuration
- **Critical:** Configure your media players for accurate tracking.
- See the [Media Players Guide](media-players.md) for setup steps for VLC, MPV, and others.

## 📁 Directory Filtering (Optional)

To restrict tracking to specific folders, set `allow_dirs` / `deny_dirs` in `settings.json` inside your app data folder. See the [Advanced & Developer Guide](configuration.md) for details.

## 🛠️ Tray Usage
- Use the tray icon in the menu bar for status and controls.
- After setup, daily usage is tray-first (CLI is optional).

## 🐞 Troubleshooting
- Grant accessibility and notification permissions in System Preferences if tray or notifications do not work.
- For issues, see the [Troubleshooting Guide](troubleshooting.md).

## ⚠️ Limitations
- Some features are still in progress on macOS.
- If you hit issues, please report them on GitHub with logs.
