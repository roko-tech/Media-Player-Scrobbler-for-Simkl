from simkl_mps.media_overrides import MediaOverrides
from simkl_mps.media_scrobbler import MediaScrobbler


def test_exact_file_override_beats_folder_override(tmp_path):
    overrides = MediaOverrides(tmp_path)
    folder = tmp_path / "Series" / "Season 02"
    first = folder / "Episode 01.mkv"
    second = folder / "Episode 02.mkv"
    overrides.set("folder", folder, 100, season=1, media_type="anime")
    overrides.set("file", first, 200, season=3, media_type="show")

    exact = overrides.find(first)
    inherited = overrides.find(second)

    assert exact["scope"] == "file"
    assert exact["simkl_id"] == 200
    assert exact["season"] == 3
    assert inherited["scope"] == "folder"
    assert inherited["simkl_id"] == 100


def test_nearest_parent_folder_override_wins(tmp_path):
    overrides = MediaOverrides(tmp_path)
    series = tmp_path / "Series"
    season = series / "Season 02"
    episode = season / "Episode.mkv"
    overrides.set("folder", series, 100)
    overrides.set("folder", season, 200)

    assert overrides.find(episode)["simkl_id"] == 200


def test_override_persists_and_can_be_removed(tmp_path):
    episode = tmp_path / "Series" / "Episode.mkv"
    overrides = MediaOverrides(tmp_path)
    overrides.set("file", episode, 123)

    reloaded = MediaOverrides(tmp_path)
    assert reloaded.find(episode)["simkl_id"] == 123
    assert reloaded.remove_match(episode)["scope"] == "file"
    assert reloaded.find(episode) is None


def test_override_is_applied_before_credentials_cache_or_network():
    processed = {}
    scrobbler = object.__new__(MediaScrobbler)
    scrobbler.client_id = None
    scrobbler.media_overrides = type(
        "Overrides",
        (),
        {
            "find": lambda self, path: {
                "scope": "folder",
                "simkl_id": 529392,
                "season": 1,
                "media_type": "anime",
                "title": "Correct Show",
            }
        },
    )()
    scrobbler._process_simkl_search_result = (
        lambda result, original, cache_key, source: processed.update(
            result=result, source=source
        )
    )

    scrobbler._identify_media_from_filepath(
        r"D:\Anime\Show\Season 02\Episode.mkv",
        {"type": "episode", "title": "Show", "season": 2, "episode": 6},
    )

    assert processed["source"] == "manual_folder_override"
    assert processed["result"]["show"]["ids"]["simkl"] == 529392
    assert processed["result"]["episode"] == {"season": 1, "episode": 6}
