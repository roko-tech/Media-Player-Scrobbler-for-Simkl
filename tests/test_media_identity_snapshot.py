import time

from simkl_mps.media_cache import MediaCache
from simkl_mps.media_identity import cache_key_for_media
from simkl_mps.media_scrobbler import MediaScrobbler
from simkl_mps.monitor import Monitor
from simkl_mps.player_snapshot import PlayerSnapshot


def test_same_basename_in_different_directories_has_distinct_cache_identity(tmp_path):
    cache = MediaCache(tmp_path)
    first = tmp_path / "Movie A" / "video.mkv"
    second = tmp_path / "Movie B" / "video.mkv"

    first_key = cache_key_for_media(first)
    second_key = cache_key_for_media(second)
    cache.set(first_key, {"simkl_id": 1, "type": "movie"})
    cache.set(second_key, {"simkl_id": 2, "type": "movie"})

    assert first_key != second_key
    assert cache.get(first_key)["simkl_id"] == 1
    assert cache.get(second_key)["simkl_id"] == 2
    assert MediaScrobbler._has_media_file_changed(str(first), str(second)) is True


def test_start_new_item_checks_override_before_cached_identity(monkeypatch):
    filepath = r"D:\Anime\Correct Show\Episode 01.mkv"
    actions = []
    scrobbler = MediaScrobbler.__new__(MediaScrobbler)
    scrobbler.media_overrides = type(
        "Overrides",
        (),
        {"find": lambda self, path: {"simkl_id": 200}},
    )()
    scrobbler.media_cache = type(
        "Cache",
        (),
        {"get": lambda self, key: {"simkl_id": 100, "movie_name": "Wrong"}},
    )()
    scrobbler._derive_display_season_episode = lambda: None
    scrobbler._send_notification = lambda *args, **kwargs: None
    scrobbler._apply_cached_info_to_state = (
        lambda info: actions.append(("cache", info["simkl_id"]))
    )
    scrobbler._identify_media_from_filepath = (
        lambda path, guessit_info=None: actions.append(("override", path))
    )

    monkeypatch.setattr(
        "simkl_mps.media_scrobbler.is_internet_connected",
        lambda: False,
    )

    scrobbler._start_new_media_item(
        "Correct Show - Episode 01",
        filepath,
        "episode",
        {"type": "episode", "season": 1, "episode": 1},
    )

    assert actions == [("override", filepath)]


def test_process_window_uses_one_immutable_snapshot(monkeypatch):
    snapshot = PlayerSnapshot(
        process_name="vlc.exe",
        filepath=r"D:\Media\Example.mkv",
        position_seconds=300.0,
        duration_seconds=600.0,
        captured_at=time.time(),
    )
    seen = []
    scrobbler = MediaScrobbler.__new__(MediaScrobbler)
    scrobbler.currently_tracking = None
    scrobbler.current_filepath = None
    scrobbler.simkl_id = None
    scrobbler.movie_name = None
    scrobbler._allow_dirs = []
    scrobbler._deny_dirs = []
    scrobbler._get_player_integration = lambda process: object()
    scrobbler.get_player_snapshot = lambda process: (_ for _ in ()).throw(
        AssertionError("the supplied snapshot must not be sampled again")
    )
    scrobbler._refresh_dir_filters = lambda: None

    def start(title, filepath, media_type, guessit_info):
        scrobbler.currently_tracking = title
        scrobbler.current_filepath = filepath

    scrobbler._start_new_media_item = start
    scrobbler._update_tracking = (
        lambda window_info, player_snapshot=None: seen.append(player_snapshot)
    )
    monkeypatch.setattr("simkl_mps.media_scrobbler.guessit", None)

    result = scrobbler.process_window(
        {"process_name": "vlc.exe", "title": "Example"},
        player_snapshot=snapshot,
    )

    assert seen == [snapshot]
    assert result["filepath"] == snapshot.filepath


def test_excluded_media_path_is_not_written_to_logs(caplog):
    private_path = r"D:\Private Library\Sensitive Show\S01E01.mkv"
    snapshot = PlayerSnapshot(
        process_name="vlc.exe",
        filepath=private_path,
        position_seconds=None,
        duration_seconds=None,
        captured_at=time.time(),
    )
    scrobbler = MediaScrobbler.__new__(MediaScrobbler)
    scrobbler.currently_tracking = None
    scrobbler.current_filepath = None
    scrobbler._allow_dirs = []
    scrobbler._deny_dirs = [r"D:\Private Library"]
    scrobbler.get_player_snapshot = lambda process: snapshot
    scrobbler._refresh_dir_filters = lambda: None

    with caplog.at_level("INFO"):
        assert (
            scrobbler.process_window(
                {"process_name": "vlc.exe", "title": "Sensitive Show"}
            )
            is None
        )

    assert private_path not in caplog.text
    assert r"D:\Private Library" not in caplog.text


def test_get_player_snapshot_samples_each_player_field_once():
    calls = {"filepath": 0, "position": 0}

    class Integration:
        def get_current_filepath(self, process_name):
            calls["filepath"] += 1
            return r"D:\Media\Example.mkv"

        def get_position_duration(self, process_name):
            calls["position"] += 1
            return 10, 100

    integration = Integration()
    scrobbler = MediaScrobbler.__new__(MediaScrobbler)
    scrobbler._last_connection_error_log = {}
    scrobbler._get_player_integration = lambda process: integration

    snapshot = scrobbler.get_player_snapshot("vlc.exe")

    assert snapshot.filepath == r"D:\Media\Example.mkv"
    assert snapshot.position_seconds == 10
    assert snapshot.duration_seconds == 100
    assert calls == {"filepath": 1, "position": 1}


def test_cache_keeps_distinct_path_aliases_for_same_simkl_id(tmp_path):
    first = str(tmp_path / "Source A" / "video.mkv")
    second = str(tmp_path / "Source B" / "video.mkv")
    scrobbler = MediaScrobbler.__new__(MediaScrobbler)
    scrobbler.media_cache = MediaCache(tmp_path)
    scrobbler.total_duration_seconds = None
    scrobbler.currently_tracking = None
    scrobbler.current_filepath = None

    for filepath in (first, second):
        scrobbler.cache_media_info(
            original_title_key="video.mkv",
            simkl_id=42,
            display_name="Example",
            media_type="movie",
            original_filepath_if_any=filepath,
        )

    assert scrobbler.media_cache.get(cache_key_for_media(first))["simkl_id"] == 42
    assert scrobbler.media_cache.get(cache_key_for_media(second))["simkl_id"] == 42
    assert len(scrobbler.media_cache.get_all()) == 2


def test_player_arbitration_is_stable_and_preserves_current_player():
    windows = [
        {"process_name": "vlc.exe", "title": "Zulu", "pid": 20},
        {"process_name": "mpv.exe", "title": "Alpha", "pid": 10},
    ]

    assert Monitor.select_player_window(windows, preferred_process=None)["process_name"] == "vlc.exe"
    assert (
        Monitor.select_player_window(windows, preferred_process="mpv.exe")[
            "process_name"
        ]
        == "mpv.exe"
    )


def test_player_observation_arbitration_prefers_active_usable_playback():
    idle_vlc = (
        {"process_name": "vlc.exe", "title": "VLC", "pid": 20},
        PlayerSnapshot(
            process_name="vlc.exe",
            filepath=r"D:\Media\Idle.mkv",
            position_seconds=10,
            duration_seconds=100,
            captured_at=time.time(),
            playback_state="paused",
        ),
    )
    active_potplayer = (
        {
            "process_name": "potplayermini64.exe",
            "title": "Active",
            "pid": 10,
            "is_active": True,
        },
        PlayerSnapshot(
            process_name="potplayermini64.exe",
            filepath=r"D:\Media\Active.mkv",
            position_seconds=50,
            duration_seconds=100,
            captured_at=time.time(),
            playback_state="playing",
        ),
    )

    for observations in (
        [idle_vlc, active_potplayer],
        [active_potplayer, idle_vlc],
    ):
        window, snapshot = Monitor.select_player_observation(
            observations,
            preferred_process="vlc.exe",
        )
        assert window["process_name"] == "potplayermini64.exe"
        assert snapshot.filepath == r"D:\Media\Active.mkv"


def test_monitor_samples_each_player_candidate_once():
    calls = []
    monitor = Monitor.__new__(Monitor)
    monitor.scrobbler = type(
        "Scrobbler",
        (),
        {
            "get_player_snapshot": lambda self, process: calls.append(process)
            or PlayerSnapshot(
                process_name=process,
                filepath=rf"D:\Media\{process}.mkv",
                position_seconds=1,
                duration_seconds=10,
                captured_at=time.time(),
            )
        },
    )()
    windows = [
        {"process_name": "vlc.exe", "title": "VLC"},
        {"process_name": "mpv.exe", "title": "MPV"},
    ]

    observations = monitor.collect_player_observations(windows)

    assert calls == ["vlc.exe", "mpv.exe"]
    assert len(observations) == 2
