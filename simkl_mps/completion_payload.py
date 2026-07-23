"""Pure construction of Simkl completion payloads."""


def build_completion_payload(
    simkl_id,
    media_type,
    season=None,
    episode=None,
    watched_at=None,
):
    """Build a Simkl /sync/history payload without reading mutable playback state."""
    if not watched_at:
        return None
    try:
        item_ids = {"simkl": int(simkl_id)}
    except (TypeError, ValueError):
        return None

    if media_type == "movie":
        return {"movies": [{"ids": item_ids, "watched_at": watched_at}]}

    if media_type == "show":
        if season is None or episode is None:
            return None
        try:
            season_number = int(season)
            episode_number = int(episode)
        except (TypeError, ValueError):
            return None
        return {
            "shows": [
                {
                    "ids": item_ids,
                    "seasons": [
                        {
                            "number": season_number,
                            "episodes": [
                                {
                                    "number": episode_number,
                                    "watched_at": watched_at,
                                }
                            ],
                        }
                    ],
                }
            ]
        }

    if media_type == "anime":
        if episode is None:
            return None
        try:
            episode_item = {
                "number": int(episode),
                "watched_at": watched_at,
            }
            show_item = {"ids": item_ids}
            if season is None:
                show_item["episodes"] = [episode_item]
            else:
                show_item["seasons"] = [
                    {
                        "number": int(season),
                        "episodes": [episode_item],
                    }
                ]
        except (TypeError, ValueError):
            return None
        return {"shows": [show_item]}

    return None
