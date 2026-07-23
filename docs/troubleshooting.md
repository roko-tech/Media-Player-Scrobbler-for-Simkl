# 🛠️ Troubleshooting Guide

This guide helps you solve common problems with MPS for SIMKL.

- For installation issues, see the [Windows Guide](windows-guide.md), [Linux Guide](linux-guide.md), or [macOS Guide](mac-guide.md).
- For player setup, see the [Media Players Guide](media-players.md).

## Common Issues

### Authentication
- Run `simkl-mps init --force` to reset authentication.
- Check your internet connection.
- For **Windows EXE**: restart the app from the Start menu.

### Media Detection
- Use clear filenames: `Movie Title (Year).ext`.
- Configure your player ([Media Players Guide](media-players.md)).
- Some players may hide titles in fullscreen.
- Run with debug logging: `simkl-mps tray --debug`.

### Player Configuration
- See [Media Players Guide](media-players.md) for all player setup steps.
- For VLC, MPV, MPC-HC/BE, etc: follow the exact configuration steps.

### Tray/App Issues
- Tray icon missing: check hidden icons or restart app.
- For Windows, see [Windows Guide](windows-guide.md) for common errors.
- For Linux, see [Linux Guide](linux-guide.md) for tray troubleshooting.
- For macOS, see the [macOS Guide](mac-guide.md) for permissions and tray details.

### Windows Installer
- Run installer as Administrator.
- Check antivirus if blocked.
- Try reinstalling if issues persist.

### Linux/Mac
- Ensure all dependencies are installed (see guides).
- For tray issues, see desktop environment notes in [Linux Guide](linux-guide.md) or [macOS Guide](mac-guide.md).

## Diagnostics
- Run with debug logging: `simkl-mps tray --debug`.
- Check logs for errors:
  - Windows: `%USERPROFILE%\kavin\simkl-mps\simkl_mps.log`
  - macOS: `~/kavin/simkl-mps/simkl_mps.log`
  - Linux: `~/kavin/simkl-mps/simkl_mps.log`

## Still Need Help?
- Check [GitHub Issues](https://github.com/roko-tech/Media-Player-Scrobbler-for-Simkl/issues).
- Open a new issue with OS, app version, logs, and steps to reproduce.
