# 🪟 Windows Installation & Configuration Guide

This comprehensive guide provides detailed instructions specifically for Windows users of Media Player Scrobbler for Simkl, focusing on the Windows installer, system integration, and media player configuration.

> **Important Note**: Currently, the application supports **movie tracking only**. TV show tracking is planned for future updates.

## 📥 Windows Installer

The Windows installer provides the most streamlined experience with automatic updates and system integration.

### Download and Installation Process

```mermaid
flowchart LR
    A[Download Installer] --> B[Run as Administrator]
    B --> C[Accept License Agreement]
    C --> D[Choose Install Location]
    D --> E[Select Components]
    E --> F[Configure Settings]
    F --> G[Complete Installation]
    G --> H[Launch Application]
    H --> I[Authenticate with Simkl]
    I --> J[Configure Media Players]
    style A fill:#4285f4,stroke:#333,stroke-width:2px,color:#fff
    style G fill:#34a853,stroke:#333,stroke-width:2px,color:#fff
    style J fill:#fbbc05,stroke:#333,stroke-width:2px,color:#fff
```

1. [Download the latest installer](https://github.com/ByteTrix/Media-Player-Scrobbler-for-Simkl/releases/latest)
2. **Right-click** the installer and select **"Run as administrator"** (recommended)
3. Follow the setup wizard:
   - Accept the license agreement
   - Choose your preferred installation location (optional)
   - Select components to install:
     - Desktop shortcut
     - Start menu shortcuts
     - **Auto-start on login** (recommended)
     - **Auto-update checking** (recommended)

### Installer Features

- **All-in-one package**: Includes all dependencies (no separate Python installation required)
- **System integration**: Creates desktop and start menu shortcuts
- **Auto-start capability**: Option to run automatically when Windows starts
- **Auto-update system**: Checks for updates weekly
- **Clean uninstallation**: Properly removes all components

### Silent & Automated Deployment

- The installer now honors common enterprise parameters: use `/quiet`, `/silent`, `/s`, `/qn`, or `/passive` when automating deployments
- Each alias maps internally to Inno Setup's `/VERYSILENT /SUPPRESSMSGBOXES /NORESTART` flags, ensuring the Microsoft Store silent install check succeeds
- Combine additional switches such as `/LOG="C:\\temp\\mpss-install.log"` as needed; they will be preserved when silent aliases are used
- When running interactively, the traditional `/SILENT` or `/VERYSILENT` options continue to function unchanged

### Post-Installation

After installation completes:
1. The application launches automatically
2. Authenticate with your Simkl account when prompted
3. A system tray icon appears in the notification area
4. **CRITICAL STEP**: Configure your media players for optimal tracking (see below)

## ⚙️ Media Player Configuration on Windows (recommended)

For the best movie tracking experience, configure your preferred media players:
[Media Players](media-players.md)


## 🖥️ Windows System Integration

### System Tray Features

Right-click the system tray icon to access:

```mermaid
flowchart TD
    A[System Tray Icon] -->|Right-click| B[Context Menu]
    B --> C[Status Information]
    B --> D[Start/Pause Tracking]
    B --> E[Scrobbling]
    E --> E1[Retry Last Scrobble]
    E --> E2[Sync Backlog Now]
    E --> E3[Completion Threshold]
    E --> E4[Open Local Watch History]
    B --> E5[SIMKL]
    E5 --> E5A[Authenticate/Re-auth]
    E5 --> E5B[Open Website]
    E5 --> E5C[Open Watch History]
    B --> F[Maintenance]
    F --> F1[Open Logs]
    F --> F2[Open Data Folder]
    F --> F3[Clear Backlog]
    F --> F4[Clear Cache]
    F --> F5[Clear Watch History]
    F --> F6[Clear Logs]
    F --> F7[Reset App Data]
    B --> G[More]
    G --> G1[Donate ❤️]
    G --> G2[Check for Updates]
    G --> G3[Help]
    G --> G4[About]
    B --> H[Exit]
    style A fill:#4285f4,stroke:#333,stroke-width:2px,color:#fff
    style D fill:#34a853,stroke:#333,stroke-width:2px,color:#fff
    style F fill:#fbbc05,stroke:#333,stroke-width:2px
```

- **Status information**: Current monitoring state and connection status
- **Start/Pause Tracking**: Pause or resume tracking on demand
- **Scrobbling**: Recovery and threshold controls
  - **Retry Last Scrobble**: Clears cache for the active file and attempts to re-identify and scrobble it. Use when the wrong title/episode appears.
  - **Sync Backlog Now**: Immediately processes any offline scrobbles waiting in backlog.
  - **Completion Threshold**: Quickly switch between preset watch thresholds (65%, 80%, 90%) or define a custom percentage.
  - **Open Local Watch History**: Open the local watch history viewer in your web browser.
- **SIMKL**: Account and service management
  - **Authenticate / Re-authenticate**: Launch the Simkl login flow if you are signing in for the first time or need to refresh an expired token.
  - **Open Website**: Visit the SIMKL website.
  - **Open Watch History**: View your watch history on SIMKL.
- **Maintenance**: Logs, data, and cache management
  - **Open Logs**: Jump straight to app diagnostics from the tray.
  - **Open Data Folder**: Open the application data directory.
  - **Clear Backlog**: Deletes pending offline scrobbles to stop repeated sync prompts.
  - **Clear Cache**: Removes local media cache data while keeping logs and settings intact.
  - **Clear Watch History**: Removes the local `watch_history.json` file and viewer data without touching your SIMKL account.
  - **Clear Logs**: Truncates application and playback logs so you can capture a fresh session before debugging.
  - **Reset App Data (Danger)**: Performs a full reset—use only when you want a clean re-authentication; the app exits afterward.
- **More**: Additional utilities
  - **Donate ❤️**: Support the project.
  - **Check for Updates**: Check if a newer version is available.
  - **Help**: Open help documentation.
  - **About**: View application information.
- **Exit**: Close the application

### Windows Auto-Start

If you selected auto-start during installation, the application will launch automatically when you log in to Windows. To change this setting:

1. Right-click the system tray icon
2. Select **Settings**
3. Toggle the **Launch on startup** option

### Windows-Specific File Locations

- **Configuration file**: `%%USERPROFILE%\kavin\simkl-mps\.simkl_mps.env`
- **Log files**: `%USERPROFILE%\kavin\simkl-mps\simkl_mps.log`
- **Backlog**: `%USERPROFILE%\kavin\simkl-mps\backlog.json`

## 🔄 Windows Update System

The Windows installer version includes an automatic update system:

### How Updates Work

1. If auto-check is enabled, the application checks for updates weekly
2. When an update is available, a notification appears
3. Click the update option in the tray menu
4. Your default browser opens and the update downloads automatically
5. Install the Setup
6. The application restarts with the new version


## 🚀 Optimizing for Windows

### Performance Tips

1. **Use the Windows installer** for the best experience
2. Enable **auto-start** for convenience
3. Configure **VLC** or **MCP-HC** for the most accurate tracking
4. Use the **system tray menu** for quick access to functions
5. **Proper filenames** significantly improve movie identification:
   - Best format: `Movie Title (Year).extension`
   - Example: `Inception (2010).mkv`

### Resource Usage

- **Memory**: 30-60 MB (typical)
- **CPU**: Minimal (1-2% during active tracking)
- **Disk**: ~20 MB for installation + small database (~1 MB)
- **Network**: Only when syncing with Simkl

## 🔍 Windows Troubleshooting

### Common Issues

| Issue | Solution |
|-------|----------|
| Installation fails | Run as administrator, check Windows Defender settings |
| Application doesn't start | Check Event Viewer for errors, verify .NET Framework installation |
| Tray icon missing | Check if app is running in Task Manager, restart app |
| VLC connection fails | Verify web interface is enabled and password is correct |
| MPV not detected | Check if socket path in config matches expectations |
| Movie not identified | Use clearer filename, check log for details |

### Checking Logs on Windows

1. Right-click the system tray icon
2. Select **Maintenance → Open Logs**
3. The log file opens in your default text editor

### Running with Debug Logging

For advanced troubleshooting:
1. Open Command Prompt or PowerShell
2. Navigate to the installation directory
3. Run: `simkl-mps tray --debug`

## 📲 Uninstallation

To remove the application from your Windows system:

1. Open **Settings → Apps → Apps & features**
2. Find "MPS for SIMKL"
3. Click **Uninstall**
4. Follow the uninstallation wizard
5. Choose whether to remove user data (settings, logs, backlog) ("No" is Recommended)

## 🔔 Final Checklist for Windows Users

1. ✅ Install using the Windows installer
2. ✅ Enable auto-start for convenience
3. ✅ Configure your media players (critical step!)
4. ✅ Use proper filenames for your movies
5. ✅ Play a movie and verify it's detected and tracked
6. ✅ Check your Simkl profile to confirm movies are marked as watched
