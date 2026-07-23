from types import SimpleNamespace

import pystray

from simkl_mps import tray_base, tray_linux
from simkl_mps.tray_base import TrayAppBase


class StubTray(TrayAppBase):
    def update_icon(self, status):
        pass

    def show_notification(self, title, message):
        pass

    def show_about(self, *_args):
        pass

    def show_help(self, *_args):
        pass

    def exit_app(self, *_args):
        pass

    def run(self):
        pass

    def _ask_custom_threshold_dialog(self, callback):
        pass

    def _ask_directory_filter_dialog(self, *_args):
        pass


class FakeMenu:
    def __init__(self):
        self.children = []

    def append(self, item):
        self.children.append(item)


class FakeMenuItem:
    def __init__(self, label=""):
        self.label = label
        self.sensitive = True
        self.submenu = None
        self.active = None
        self.callback = None

    def set_sensitive(self, value):
        self.sensitive = value

    def set_submenu(self, menu):
        self.submenu = menu

    def set_active(self, value):
        self.active = value

    def get_active(self):
        return self.active

    def connect(self, _signal, callback):
        self.callback = callback


class FakeSeparatorMenuItem(FakeMenuItem):
    pass


class FakeCheckMenuItem(FakeMenuItem):
    pass


class FakeRadioMenuItem(FakeCheckMenuItem):
    def __init__(self, group=None, label=""):
        super().__init__(label)
        self.group = group


FAKE_GTK = SimpleNamespace(
    Menu=FakeMenu,
    MenuItem=FakeMenuItem,
    SeparatorMenuItem=FakeSeparatorMenuItem,
    CheckMenuItem=FakeCheckMenuItem,
    RadioMenuItem=FakeRadioMenuItem,
)


def _pystray_shape(items):
    shape = []
    for item in items:
        if item is pystray.Menu.SEPARATOR:
            shape.append(("separator",))
            continue
        kind = "radio" if item.radio else "check" if item.checked is not None else "item"
        children = _pystray_shape(item.submenu) if item.submenu is not None else None
        shape.append((kind, item.text, bool(item.enabled), item.checked, children))
    return shape


def _gtk_shape(menu):
    shape = []
    for item in menu.children:
        if isinstance(item, FakeSeparatorMenuItem):
            shape.append(("separator",))
            continue
        kind = (
            "radio"
            if isinstance(item, FakeRadioMenuItem)
            else "check"
            if isinstance(item, FakeCheckMenuItem)
            else "item"
        )
        children = _gtk_shape(item.submenu) if item.submenu is not None else None
        shape.append((kind, item.label, bool(item.sensitive), item.active, children))
    return shape


def _find_item(menu, label):
    for item in menu.children:
        if getattr(item, "label", None) == label:
            return item
        if item.submenu is not None:
            found = _find_item(item.submenu, label)
            if found is not None:
                return found
    return None


def test_linux_appindicator_renders_the_shared_menu(monkeypatch):
    monkeypatch.setattr(tray_base, "get_setting", lambda _name, default=None: default)
    monkeypatch.setattr(tray_linux, "Gtk", FAKE_GTK)

    app = StubTray.__new__(StubTray)
    app.status = "running"
    app.status_details = ""
    app.last_scrobbled = None
    app.is_authenticated = True
    app._auth_in_progress = False
    app._refresh_auth_state = lambda: None
    app._get_trakt_watcher = lambda: SimpleNamespace(configured=True)

    calls = []
    app.show_last_receipt = lambda: calls.append("receipt")
    shared_items = app._build_pystray_menu_items()

    indicator = tray_linux.AppIndicatorTray.__new__(tray_linux.AppIndicatorTray)
    gtk_menu = indicator._build_gtk_menu(shared_items)

    assert _gtk_shape(gtk_menu) == _pystray_shape(shared_items)
    assert _find_item(gtk_menu, "Trakt") is not None

    receipt_item = _find_item(gtk_menu, "Show Last Receipt")
    assert receipt_item is not None
    receipt_item.callback(receipt_item)
    assert calls == ["receipt"]
