import re
import threading
import tomllib
from pathlib import Path

from simkl_mps import cli, main
from simkl_mps.media_scrobbler import MediaScrobbler


REPO_ROOT = Path(__file__).resolve().parent.parent


def test_runtime_configuration_accepts_secretless_public_client(monkeypatch):
    expected = {
        "client_id": "public-client-id",
        "client_secret": None,
        "access_token": "user-access-token",
    }
    monkeypatch.setattr(main, "get_credentials", lambda: expected)

    assert main.load_configuration() is expected


def test_init_uses_secretless_pin_flow(tmp_path, monkeypatch, capsys):
    pin_calls = []
    monkeypatch.setattr(cli, "get_env_file_path", lambda: tmp_path / ".simkl_mps.env")
    monkeypatch.setattr(
        cli,
        "get_credentials",
        lambda: {
            "client_id": "public-client-id",
            "client_secret": None,
            "access_token": None,
        },
    )
    monkeypatch.setattr(
        cli,
        "pin_auth_flow",
        lambda client_id: pin_calls.append(client_id) or "user-access-token",
    )
    monkeypatch.setattr(
        cli,
        "get_user_settings",
        lambda client_id, access_token: {"user_id": 123},
    )

    assert cli.init_command(None) == 0
    assert pin_calls == ["public-client-id"]
    assert "Client ID loaded" in capsys.readouterr().out


def test_builds_never_require_or_embed_simkl_client_secret():
    paths = [
        REPO_ROOT / ".github" / "workflows" / "build.yml",
        REPO_ROOT / ".github" / "workflows" / "windows-build.yml",
        REPO_ROOT / ".github" / "workflows" / "publish-pypi.yml",
        REPO_ROOT / "simkl_mps" / "credentials.py",
    ]

    combined = "\n".join(path.read_text(encoding="utf-8") for path in paths)

    assert "SIMKL_CLIENT_SECRET_PLACEHOLDER" not in combined
    assert "secrets.SIMKL_CLIENT_SECRET" not in combined


def test_backlog_never_promotes_automatic_completion_to_rewatch(monkeypatch):
    item = {
        "simkl_id": 100,
        "title": "Example",
        "type": "show",
        "season": 1,
        "episode": 2,
        "allow_rewatch": True,
        "attempt_count": 0,
        "last_attempt_timestamp": None,
    }

    class Backlog:
        def __init__(self):
            self.items = {"event": item}
            self.removed = []

        def get_pending(self):
            return self.items

        def update_item(self, key, updates):
            self.items[key].update(updates)
            return True

        def record_outcome(self, *args, **kwargs):
            return True

        def remove(self, key):
            self.removed.append(key)
            self.items.pop(key, None)
            return True

    submitted = {}
    backlog = Backlog()
    scrobbler = MediaScrobbler.__new__(MediaScrobbler)
    scrobbler.client_id = "configured"
    scrobbler.access_token = "configured"
    scrobbler.backlog_cleaner = backlog
    scrobbler._processing_lock = threading.Lock()
    scrobbler._processing_backlog_items = set()
    scrobbler._backlog_notification_throttle = {}
    scrobbler._send_notification = lambda *args, **kwargs: None
    scrobbler._resolve_backlog_item_identity = lambda key, data: (True, data, None)
    scrobbler._build_add_to_history_payload = lambda watched_at=None: {"shows": [{}]}
    scrobbler._fetch_and_update_cache_with_full_details = lambda *args, **kwargs: None
    scrobbler._store_in_watch_history = lambda *args, **kwargs: True
    scrobbler.simkl_id = None
    scrobbler.media_type = None
    scrobbler.season = None
    scrobbler.episode = None

    def fake_add_to_history(payload, client_id, access_token, allow_rewatch=False):
        submitted["allow_rewatch"] = allow_rewatch
        return {"status": "success"}

    monkeypatch.setattr("simkl_mps.media_scrobbler.is_internet_connected", lambda: True)
    monkeypatch.setattr("simkl_mps.media_scrobbler.add_to_history", fake_add_to_history)

    result = scrobbler.process_backlog()

    assert result["processed"] == 1
    assert submitted["allow_rewatch"] is False
    assert backlog.removed == ["event"]


def test_automatic_completion_code_does_not_read_rewatch_setting():
    source = (REPO_ROOT / "simkl_mps" / "media_scrobbler.py").read_text(encoding="utf-8")

    assert "get_setting('allow_rewatch'" not in source


def test_operational_links_use_canonical_repository():
    canonical = "roko-tech/Media-Player-Scrobbler-for-Simkl"
    paths = [
        REPO_ROOT / "setup.iss",
        REPO_ROOT / "pyproject.toml",
        REPO_ROOT / "simkl_mps" / "utils" / "updater.ps1",
        REPO_ROOT / "simkl_mps" / "tray_win.py",
        REPO_ROOT / "simkl_mps" / "tray_mac.py",
        REPO_ROOT / "simkl_mps" / "tray_linux.py",
        REPO_ROOT / "simkl_mps" / "watch-history-viewer" / "index.html",
        REPO_ROOT / "docs" / "configuration.md",
        REPO_ROOT / "docs" / "index.md",
        REPO_ROOT / "docs" / "troubleshooting.md",
        REPO_ROOT / "docs" / "windows-guide.md",
    ]

    for path in paths:
        text = path.read_text(encoding="utf-8")
        assert "ByteTrix/Media-Player-Scrobbler-for-Simkl" not in text
        assert "ByteTrix/media-player-scrobbler-for-simkl" not in text
        if "github.com/" in text or "api.github.com/repos/" in text:
            assert canonical in text


def test_declared_versions_match_and_tray_reuses_package_version():
    project_version = tomllib.loads(
        (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )["tool"]["poetry"]["version"]
    package_text = (REPO_ROOT / "simkl_mps" / "__init__.py").read_text(encoding="utf-8")
    setup_text = (REPO_ROOT / "setup.iss").read_text(encoding="utf-8")
    tray_text = (REPO_ROOT / "simkl_mps" / "tray_base.py").read_text(encoding="utf-8")

    package_version = re.search(r'^__version__ = "([^"]+)"$', package_text, re.MULTILINE)
    setup_version = re.search(r'^#define MyAppVersion "([^"]+)"$', setup_text, re.MULTILINE)

    assert package_version and package_version.group(1) == project_version
    assert setup_version and setup_version.group(1) == project_version
    assert "APP_HARDCODED_VERSION" not in tray_text


def test_windows_runtime_and_installer_share_canonical_registry_owner():
    setup_text = (REPO_ROOT / "setup.iss").read_text(encoding="utf-8")
    tray_text = (REPO_ROOT / "simkl_mps" / "tray_win.py").read_text(encoding="utf-8")
    cli_text = (REPO_ROOT / "simkl_mps" / "cli.py").read_text(encoding="utf-8")
    updater_text = (
        REPO_ROOT / "simkl_mps" / "utils" / "updater.ps1"
    ).read_text(encoding="utf-8")
    migration_text = (
        REPO_ROOT / "simkl_mps" / "migration.py"
    ).read_text(encoding="utf-8")

    assert '#define MyAppPublisher "roko-tech"' in setup_text
    assert "createvalueifdoesntexist uninsdeletekey" in setup_text
    assert r"Software\roko-tech\Media Player Scrobbler for SIMKL" in tray_text
    assert r"Software\roko-tech\Media Player Scrobbler for SIMKL" in cli_text
    assert '$Publishers = @("roko-tech", "kavin", "ByteTrix")' in updater_text
    assert r"Software\kavin" not in tray_text
    assert r"Software\kavin" not in cli_text
    assert "legacy_key_paths" in migration_text
    assert "canonical_key_path" in migration_text


def test_release_promotion_happens_only_after_draft_assets_and_pypi():
    build = (REPO_ROOT / ".github" / "workflows" / "build.yml").read_text(
        encoding="utf-8"
    )
    create_release = (
        REPO_ROOT / ".github" / "workflows" / "create-release.yml"
    ).read_text(encoding="utf-8")
    publish_pypi = (
        REPO_ROOT / ".github" / "workflows" / "publish-pypi.yml"
    ).read_text(encoding="utf-8")

    assert "--draft" in create_release
    assert "--draft=false" not in create_release
    assert create_release.index("gh release create") < create_release.index("gh release upload")
    assert "build-python-package:" in build
    assert "publish-pypi:" in build
    assert "publish-github-release:" in build
    assert "needs: [prepare-release, create-github-release]" in build
    assert "needs: [prepare-release, create-github-release, publish-pypi]" in build
    assert "gh release edit" in build and "--draft=false" in build
    assert "actions/upload-artifact" in publish_pypi
    assert "actions/download-artifact" in publish_pypi
    assert "poetry lock" not in publish_pypi
    assert "poetry lock" not in (
        REPO_ROOT / ".github" / "workflows" / "windows-build.yml"
    ).read_text(encoding="utf-8")


def test_pyinstaller_build_does_not_kill_running_user_processes():
    spec = (REPO_ROOT / "simkl-mps.spec").read_text(encoding="utf-8")

    assert "taskkill" not in spec.lower()
