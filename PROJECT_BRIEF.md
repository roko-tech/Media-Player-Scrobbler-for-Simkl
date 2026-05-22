# Project Brief

## 1. Summary
Media Player Scrobbler for Simkl (MPS) is a cross-platform Python tray app that monitors media players and scrobbles playback to Simkl.

## 2. Goals
- Reliable detection and scrobbling for major media players.
- Clear, low-noise user notifications and tray controls.
- Offline backlog and retry behavior.

## 3. Non-Goals
- Building a full media library manager.
- Deep video player control beyond read-only playback state.

## 4. Target Platforms
- Windows (primary)
- Linux
- macOS (experimental)

## 5. Architecture Notes
- Tray app orchestrates Monitor and MediaScrobbler.
- Window detection + player integrations provide position/duration.
- Simkl API client handles auth/search/scrobble; backlog for offline.
- Settings stored in settings.json under app data directory.

## 6. Risks / Constraints
- Player web interfaces can be flaky or unavailable.
- Cross-platform tray/notification support varies by OS.
- Avoid notification spam while still surfacing important errors.

## 7. Current Focus
- Sprint 1: make notification on/off toggle reliable and predictable.

## 8. Success Criteria
- User can disable notifications and see no toasts.
- Re-enabling notifications resumes without long throttle delays.
- Settings persist across restarts.
