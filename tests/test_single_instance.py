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
