# Automatic Trakt sync

This fork can keep Trakt current from the same tray process that scrobbles to
Simkl. After Simkl accepts a completed watch, the app reads the exact event from
its local `watch_history.json` and immediately sends it to Trakt. Startup and
file polling remain as recovery paths. Events Trakt cannot match are retained in
`trakt_sync_state.json` and retried later.

## One-time setup

1. Create a Trakt API application at <https://trakt.tv/oauth/applications>.
2. In the app data folder shown by `simkl-mps version`, create
   `trakt_config.json`:

   ```json
   {
     "client_id": "YOUR_TRAKT_CLIENT_ID",
     "client_secret": "YOUR_TRAKT_CLIENT_SECRET"
   }
   ```

3. Authorize it once:

   ```text
   simkl-mps trakt-auth
   ```

The config, OAuth token, sync state, and watch history live in the app data
folder. On Windows, the client secret and OAuth tokens are encrypted for the
current Windows user with DPAPI. Existing plaintext files migrate on first read.
They are never stored in this Git repository.

## Commands

```text
simkl-mps trakt-sync
simkl-mps trakt-sync --dry-run
simkl-mps trakt-sync --since 2026-07-14
```

The tray menu also contains **Trakt → Retry / Sync Now** and **Sync Health**.
Sync Health shows the latest Simkl-accepted watch, separate Simkl and Trakt
pending counts, the last Trakt HTTP response, added/not-found counts, and the
last attempt and success times. **Copy Safe Diagnostics** omits media titles,
service IDs, file paths, and credentials so its output can be shared safely.

Automatic syncing starts with the normal Simkl tray; no second watcher process
or tray icon is required.

## Correcting a media match

While the media is playing, use **Scrobbling → Media Identification** to save an
override for either the exact file or its folder. Enter the correct Simkl ID. For
an episode, add the target Simkl season after a comma, such as `529392, 1` for a
split-cour title that Simkl stores as season 1. Exact-file overrides take
precedence over folder overrides, and the nearest matching folder wins. Use
**Remove Current Override** to return to automatic matching.

## Letterboxd

Letterboxd exposes write endpoints only to approved API clients. Its API access
form says private/personal projects are not currently accepted, so this project
does not include an unsupported browser-scraping login. The old CSV importer can
remain as a separate manual archive if needed.
