import pytest


pytestmark = pytest.mark.skipif(
    __import__("os").name != "nt", reason="Windows tray behavior"
)


def test_duplicate_tray_launch_exits_before_constructing_app(monkeypatch):
    from simkl_mps import tray_win

    monkeypatch.setattr(tray_win, "_acquire_single_instance", lambda: False)
    monkeypatch.setattr(
        tray_win,
        "TrayAppWin",
        lambda: (_ for _ in ()).throw(AssertionError("tray should not be constructed")),
    )

    assert tray_win.run_tray_app() == 0


def test_unexpected_tray_loop_exit_recreates_icon(monkeypatch):
    from simkl_mps import tray_win

    app = tray_win.TrayAppWin.__new__(tray_win.TrayAppWin)
    events = []

    class FirstIcon:
        def run(self):
            events.append("first run returned")

    class RecoveredIcon:
        def run(self):
            events.append("recovered run")
            app._exit_requested = True

    app._exit_requested = False
    app.tray_icon = FirstIcon()

    def setup_icon():
        events.append("icon recreated")
        app.tray_icon = RecoveredIcon()

    monkeypatch.setattr(app, "setup_icon", setup_icon)
    monkeypatch.setattr(tray_win.time, "sleep", lambda _seconds: None)

    app._run_tray_loop(retry_delay=0)

    assert events == ["first run returned", "icon recreated", "recovered run"]


def test_exit_request_stops_tray_loop_without_recreating_icon(monkeypatch):
    from simkl_mps import tray_win

    app = tray_win.TrayAppWin.__new__(tray_win.TrayAppWin)

    class ExitingIcon:
        def run(self):
            app._exit_requested = True

    app._exit_requested = False
    app.tray_icon = ExitingIcon()
    monkeypatch.setattr(
        app,
        "setup_icon",
        lambda: (_ for _ in ()).throw(AssertionError("icon should not be recreated")),
    )

    app._run_tray_loop(retry_delay=0)


def test_exit_app_marks_intent_before_stopping_monitor(monkeypatch):
    from simkl_mps import tray_win

    app = tray_win.TrayAppWin.__new__(tray_win.TrayAppWin)
    app._exit_requested = False
    app.monitoring_active = True
    events = []

    class Icon:
        def stop(self):
            events.append("icon stopped")

    app.tray_icon = Icon()

    def stop_monitoring():
        assert app._exit_requested is True
        events.append("monitor stopped")

    monkeypatch.setattr(app, "stop_monitoring", stop_monitoring)

    assert app.exit_app() == 0
    assert events == ["monitor stopped", "icon stopped"]


def test_simkl_poster_id_resolves_to_supported_image_url():
    from simkl_mps import tray_win

    assert (
        tray_win._resolve_poster_url("1234/abc567")
        == "https://simkl.in/posters/1234/abc567_m.webp"
    )
    assert (
        tray_win._resolve_poster_url("https://simkl.in/posters/1234/abc567_m.webp")
        == "https://simkl.in/posters/1234/abc567_m.webp"
    )
    assert tray_win._resolve_poster_url("https://example.com/poster.webp") is None


def test_poster_download_is_validated_and_cached(monkeypatch, tmp_path):
    from io import BytesIO

    from PIL import Image

    from simkl_mps import tray_win

    image_bytes = BytesIO()
    Image.new("RGB", (8, 12), "#334155").save(image_bytes, format="WEBP")
    calls = []

    class Response:
        content = image_bytes.getvalue()

        @staticmethod
        def raise_for_status():
            return None

    def fake_get(url, timeout):
        calls.append((url, timeout))
        return Response()

    monkeypatch.setattr(tray_win.requests, "get", fake_get)

    first = tray_win._cache_poster(tmp_path, "1234/abc567", 42)
    second = tray_win._cache_poster(tmp_path, "1234/abc567", 42)

    assert first == second
    assert first.is_file()
    assert len(calls) == 1


def test_completion_receipt_keeps_the_exact_episode_from_identification():
    from simkl_mps.tray_win import TrayAppWin

    class Cache:
        @staticmethod
        def get_by_simkl_id(_simkl_id):
            return "episode-3.mkv", {
                "movie_name": "The Husband",
                "year": 2026,
                "season": 1,
                "episode": 3,
                "season_display": 1,
                "episode_display": 3,
                "poster_url": "20/example",
            }

    class MediaScrobbler:
        media_cache = Cache()

    class Result:
        ok = True
        pending = 0
        summary = "Trakt: +1 episode(s)"

    app = TrayAppWin.__new__(TrayAppWin)
    app._last_receipt = {
        "kind": "identification",
        "simkl_id": 2914731,
        "season": 1,
        "episode": 4,
        "display_season": 1,
        "display_episode": 4,
        "poster_url": "20/example",
        "year": 2026,
    }
    rendered = []
    app._get_media_scrobbler = lambda: MediaScrobbler()
    app._show_receipt_async = rendered.append

    app.handle_trakt_sync_result(
        Result(),
        {
            "kind": "episode",
            "title": "The Husband",
            "simkl_id": 2914731,
            "season": 1,
            "episode": 4,
            "is_anime": False,
        },
    )

    assert rendered[0]["episode"] == 4
    assert rendered[0]["display_episode"] == 4
