# Automatic Trakt sync

This fork can keep Trakt current from the same tray process that scrobbles to
Simkl. After Simkl accepts a completed watch, the app reads the exact event from
its local `watch_history.json` and immediately sends it to Trakt. Startup and
file polling remain as recovery paths. Events Trakt cannot match are retained in
`trakt_sync_state.json` and retried later.

The local watch history and Trakt state use atomic file replacement and
last-known-good backups. Simkl completion delivery uses the local
`completion_ledger.sqlite3` WAL database. Completed events have stable UUIDs, so
two offline episodes from the same show cannot overwrite one another, and the
ledger retains delivered events as an audit trail.

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
On Linux and macOS, secret-bearing files are explicitly restricted to owner
read/write permissions (`0600`). They are never stored in this Git repository.

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
For an event-by-event local view, open **Scrobbling → Playback & Delivery
Activity**. It shows current playback plus persisted Simkl, local-history, and
Trakt status without displaying full media paths.

The watcher retries pending work on a timer even when `watch_history.json` does
not change again. Trakt `429` responses honor the official `Retry-After` delay;
temporary `502`, `503`, and `504` responses are retried after 30 seconds. If a
batch is only partly matched, echoed watch timestamps are used to retain only
the rejected events, avoiding duplicate rewatch records for successful items.

Automatic syncing starts with the normal Simkl tray; no second watcher process
or tray icon is required.

## Correcting a media match

While the media is playing, use **Scrobbling → Correct Match** for either the
exact file or its folder. Search by title, choose a labeled Simkl result, and,
for split-cour titles, optionally add the target season to the result number
(for example, `2, 1`). Advanced users can still enter a known value as
`id: 529392, 1`. Exact-file corrections take precedence over folder
corrections, and the nearest matching folder wins. Use **Remove Current
Correction** to return to automatic matching.

## Letterboxd

Letterboxd documents write endpoints, but access is available by request and its
current [API access policy](https://letterboxd.com/api-beta/access/) says private
or personal projects are not currently accepted. This project therefore does
not automate a browser login or scrape the website. An official OAuth adapter
can be added if Letterboxd grants API access; until then, its supported
import/export tools are the safe option.
