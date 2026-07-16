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
