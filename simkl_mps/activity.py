"""User-facing summaries of current playback and durable delivery state."""

from __future__ import annotations


_PENDING_STATUSES = {
    "network_error",
    "pending_retry",
    "rate_limited",
    "server_error",
    "unexpected_exception",
}
_ATTENTION_STATUSES = {
    "conflict",
    "forbidden",
    "invalid_request",
    "not_found",
    "precondition_failed",
    "rejected",
    "unauthorized",
}


def _provider_status(event, provider):
    outcomes = [
        outcome
        for outcome in event.get("provider_outcomes", [])
        if outcome.get("provider") == provider
    ]
    if not outcomes:
        if provider == "simkl" and event.get("simkl_synced"):
            return "Accepted (audit pending)"
        return "Not attempted"
    status = str(outcomes[-1].get("status") or "").lower()
    if status == "accepted":
        return "Accepted"
    if status in _PENDING_STATUSES or outcomes[-1].get("retryable"):
        return "Pending retry"
    if status in _ATTENTION_STATUSES:
        return "Needs attention"
    return status.replace("_", " ").title() or "Unknown"


def _episode_label(event):
    season = event.get("season")
    episode = event.get("episode")
    if episode is None:
        return ""
    if season is None:
        return f" E{episode}"
    return f" S{int(season):02d}E{int(episode):02d}"


def _local_status(event):
    if event.get("delivery_state") == "delivered" or event.get("local_history_saved"):
        return "Saved"
    if event.get("delivery_state") == "failed":
        return "Needs attention"
    return "Pending"


def format_delivery_activity(current, events, trakt_configured=False):
    """Format a path-safe snapshot of playback and provider delivery."""
    lines = ["CURRENT PLAYBACK"]
    if current:
        title = current.get("title") or "Identifying media"
        media_bits = [title + _episode_label(current)]
        if current.get("progress") is not None:
            media_bits.append(f"{int(current['progress'])}%")
        if current.get("simkl_id"):
            media_bits.append(f"Simkl {current['simkl_id']}")
        if current.get("identification_rejected"):
            media_bits.append("match rejected")
        elif current.get("state"):
            media_bits.append(str(current["state"]).title())
        lines.append(" · ".join(media_bits))
    else:
        lines.append("No supported local media is active.")

    lines.extend(["", "RECENT COMPLETIONS"])
    if not events:
        lines.append("No completion events have been recorded yet.")
        return "\n".join(lines)

    for event in events:
        title = event.get("title") or event.get("original_title") or "Unknown media"
        when = str(event.get("watched_at") or event.get("timestamp") or "")[:19]
        event_id = str(event.get("event_id") or "")[:8]
        heading = f"• {when}  {title}{_episode_label(event)}".rstrip()
        if event_id:
            heading += f"  [event {event_id}]"
        trakt_status = (
            _provider_status(event, "trakt") if trakt_configured else "Not configured"
        )
        lines.extend(
            [
                heading,
                "  "
                + " · ".join(
                    (
                        f"Simkl {_provider_status(event, 'simkl')}",
                        f"Local {_local_status(event)}",
                        f"Trakt {trakt_status}",
                    )
                ),
            ]
        )
    return "\n".join(lines)


def format_setup_health(
    *,
    authenticated,
    monitoring_status,
    current_title,
    delivery_counts,
    trakt_configured,
    allow_dir_count,
    deny_dir_count,
    first_run=False,
):
    """Format a concise setup checklist from current persisted/runtime state."""
    heading = "WELCOME TO MPS FOR SIMKL\n\n" if first_run else ""
    pending = int(delivery_counts.get("pending", 0))
    failed = int(delivery_counts.get("failed", 0))
    auth = "Ready" if authenticated else "Action needed — connect Simkl from the SIMKL menu"
    monitoring = monitoring_status.title() if monitoring_status else "Stopped"
    playback = current_title or "Waiting — play a local file to test detection"
    delivery = f"{pending} pending, {failed} need attention"
    trakt = "Configured" if trakt_configured else "Optional — not configured"
    filters = f"{allow_dir_count} allowed, {deny_dir_count} denied"

    return (
        heading
        + "SETUP & HEALTH\n"
        + f"Simkl account: {auth}\n"
        + f"Monitoring: {monitoring}\n"
        + f"Player test: {playback}\n"
        + f"Delivery queue: {delivery}\n"
        + f"Trakt: {trakt}\n"
        + f"Directory filters: {filters}\n\n"
        + "NEXT STEPS\n"
        + "1. Connect Simkl if it is not ready.\n"
        + "2. Configure your player from More > Help.\n"
        + "3. Play one local file, then reopen Setup & Health.\n"
        + "4. Use Playback & Delivery Activity to verify each completion."
    )
