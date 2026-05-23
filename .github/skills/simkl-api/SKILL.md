---
name: simkl-api
description: "Use when: implementing, debugging, or extending features that interact with the Simkl API. Provides a step-by-step workflow for authentication, searching, scrobbling, and syncing media data."
---

# Simkl API Implementation Skill

This skill provides a structured workflow for integrating the Simkl API into the application. It ensures that authentication, request formatting, and media tracking logic adhere to Simkl's specifications.

## Core Knowledge Base

### 1. Connection Basics
- **Base URL**: `https://api.simkl.com`
- **CDN URL (Trending/Calendar)**: `https://data.simkl.in`
- **Required URL Parameters** (Must be in EVERY request):
  - `client_id`: Your developer client ID.
  - `app-name`: Short, lowercase identifier.
  - `app-version`: Current app version (e.g., `1.0`).
- **Required Headers**:
  - `User-Agent`: `app-name/app-version`
  - `Authorization`: `Bearer ACCESS_TOKEN` (for authenticated endpoints)
  - `Content-Type`: `application/json` (for POST requests)

### 2. Authentication Flows
Choose the flow based on the client type:
- **OAuth 2.0**: Server-side apps (requires `client_secret`).
- **PKCE**: Public clients (Desktop binaries, Mobile, SPAs). No secret needed.
- **PIN**: Limited-input devices (CLI, TVs, Plugins). User enters code at `simkl.com/pin`.

### 3. Media Tracking Logic (Scrobbling)
- **Lifecycle**: `start` $\rightarrow$ `pause` $\rightarrow$ `stop`.
- **Completion Threshold**: $\ge 80\%$ progress marks an item as "Watched" in history. Below $80\%$ saves it as a resumable playback.
- **Playback**: Saved pause points are stored per-user and can be resumed across devices.

---

## Implementation Workflow

Follow these steps when adding a new Simkl API feature:

### Step 1: Define the Goal & Endpoint
Identify the required action and find the corresponding endpoint in the API reference:
- **Search**: `/search/file` (for filenames) or `/search/{type}` (text).
- **Scrobble**: `/scrobble/start`, `/scrobble/pause`, `/scrobble/stop`.
- **Sync**: `/sync/history` (mark watched), `/sync/add-to-list` (watchlist status).
- **Details**: `/tv/{id}`, `/anime/{id}`, `/movies/{id}`.

### Step 2: Handle Authentication
Ensure the `access_token` is valid. If implementing a new auth flow:
1. Generate the authorization URL (with `client_id`, `redirect_uri`, etc.).
2. Handle the redirect/callback to obtain the `code`.
3. Exchange the `code` for an `access_token` via `POST /oauth/token`.

### Step 3: Construct the Request
Build the request following the "Connection Basics" above. 
- **Example URL**: `https://api.simkl.com/sync/activities?client_id=XYZ&app-name=mps&app-version=1.0`
- **Example Header**: `Authorization: Bearer <token>`

### Step 4: Implement Media Resolution
If you have a filename or external ID:
1. Use `/search/file` to get a Simkl ID.
2. If you have an IMDb/TMDB ID, use `/redirect` to resolve it to a Simkl ID.
3. Use the resolved ID for all subsequent scrobble/sync calls.

### Step 5: Implement the Action
- **For Scrobbling**: Implement the state machine (Playing $\rightarrow$ Paused $\rightarrow$ Stopped).
- **For Syncing**: Map local status (e.g., "Completed") to Simkl status (`completed`, `watching`, `plantowatch`, `hold`, `dropped`).

### Step 6: Validation & Error Handling
- **Rate Limits**: Handle `429 Too Many Requests` with exponential backoff.
- **Auth Errors**: Handle `401 Unauthorized` by triggering the re-authentication flow.
- **Data Validation**: Verify that the response JSON matches the expected shape for the specific media type (Movie vs TV vs Anime).

## Quality Checklist
- [ ] Does every request include `client_id`, `app-name`, and `app-version`?
- [ ] Is the `User-Agent` header set correctly?
- [ ] Is the correct auth flow used for the platform?
- [ ] Does the scrobbling logic respect the 80% completion threshold?
- [ ] Are external IDs resolved via `/redirect` before use?
