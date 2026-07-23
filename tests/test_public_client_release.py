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


def test_release_promotion_is_github_only_and_waits_for_verified_assets():
    build = (REPO_ROOT / ".github" / "workflows" / "build.yml").read_text(
        encoding="utf-8"
    )
    create_release = (
        REPO_ROOT / ".github" / "workflows" / "create-release.yml"
    ).read_text(encoding="utf-8")
    package_workflow = (
        REPO_ROOT / ".github" / "workflows" / "publish-pypi.yml"
    ).read_text(encoding="utf-8")

    def job_body(name):
        match = re.search(
            rf"(?ms)^  {re.escape(name)}:\n.*?(?=^  [A-Za-z0-9_-]+:\n|\Z)", build
        )
        assert match, f"Missing workflow job: {name}"
        return match.group(0)

    package_job = job_body("build-python-package")
    promotion_job = job_body("publish-github-release")

    assert "--draft" in create_release
    assert "--draft=false" not in create_release
    assert "--discussion-category" not in create_release
    assert create_release.index("gh release create") < create_release.index("gh release upload")
    assert "name: Build and Publish GitHub Release" in build
    assert "publish-pypi:" not in build
    assert "PYPI_API_TOKEN" not in build
    assert "uses: ./.github/workflows/publish-pypi.yml" in package_job
    assert "build: true" in package_job and "publish: false" in package_job
    assert "needs: [prepare-release, create-github-release]" in promotion_job
    assert "publish-pypi" not in promotion_job
    assert "name: python-package" in promotion_job
    assert "path: expected-python-package" in promotion_job
    assert 'verify_release_asset "$EXPECTED_INSTALLER"' in promotion_job
    assert 'verify_release_asset "$EXPECTED_WHEEL"' in promotion_job
    assert 'verify_release_asset "$EXPECTED_SDIST"' in promotion_job
    assert 'verify_checksum_row "$EXPECTED_WHEEL"' in promotion_job
    assert 'verify_checksum_row "$EXPECTED_SDIST"' in promotion_job

    first_draft_check = promotion_job.index("IS_DRAFT=$(gh release view")
    second_draft_check = promotion_job.rindex("IS_DRAFT=$(gh release view")
    publication = promotion_job.index(
        'gh release edit "$TAG_NAME" --repo "$REPOSITORY" --draft=false'
    )
    for protected_check in (
        'verify_release_asset "$EXPECTED_WHEEL"',
        'verify_release_asset "$EXPECTED_SDIST"',
        'verify_checksum_row "$EXPECTED_WHEEL"',
        'verify_checksum_row "$EXPECTED_SDIST"',
    ):
        assert (
            first_draft_check
            < promotion_job.index(protected_check)
            < second_draft_check
        )
    assert second_draft_check < publication

    for expected in (
        "artifacts/python-package",
        '[ "${#PYTHON_PACKAGES[@]}" -ne 2 ]',
        '[ "${#WHEELS[@]}" -ne 1 ]',
        '[ "${#SDISTS[@]}" -ne 1 ]',
        'EXPECTED_WHEEL_FILENAME="simkl_mps-${RELEASE_VERSION}-py3-none-any.whl"',
        'EXPECTED_SDIST_FILENAME="simkl_mps-${RELEASE_VERSION}.tar.gz"',
        "wheel_path=",
        "sdist_path=",
        "wheel_hash=",
        "sdist_hash=",
        '--pattern "$WHEEL_FILENAME"',
        '--pattern "$SDIST_FILENAME"',
        'gh release upload "$TAG_NAME" "$WHEEL_PATH" "$SDIST_PATH" --clobber',
        'verify_uploaded_asset "$WHEEL_PATH"',
        'verify_uploaded_asset "$SDIST_PATH"',
        "**Distribution:** GitHub-only.",
    ):
        assert expected in create_release

    package_upload = create_release.index(
        'gh release upload "$TAG_NAME" "$WHEEL_PATH" "$SDIST_PATH" --clobber'
    )
    package_download = create_release.index(
        'gh release download "$TAG_NAME" "${DOWNLOAD_ARGS[@]}"'
    )
    wheel_verification = create_release.index('verify_uploaded_asset "$WHEEL_PATH"')
    sdist_verification = create_release.index('verify_uploaded_asset "$SDIST_PATH"')
    assert package_upload < package_download < wheel_verification
    assert package_upload < package_download < sdist_verification

    assert "actions/upload-artifact" in package_workflow
    assert "poetry lock" not in package_workflow
    assert "poetry lock" not in (
        REPO_ROOT / ".github" / "workflows" / "windows-build.yml"
    ).read_text(encoding="utf-8")


def test_release_binds_tag_source_build_and_signer_identity():
    build = (REPO_ROOT / ".github" / "workflows" / "build.yml").read_text(
        encoding="utf-8"
    )
    build_verification = (
        REPO_ROOT / ".github" / "workflows" / "build-verification.yml"
    ).read_text(encoding="utf-8")
    windows_build = (
        REPO_ROOT / ".github" / "workflows" / "windows-build.yml"
    ).read_text(encoding="utf-8")
    create_release = (
        REPO_ROOT / ".github" / "workflows" / "create-release.yml"
    ).read_text(encoding="utf-8")

    assert 'EXPECTED_REF="refs/tags/$TAG_NAME"' in build
    assert "git rev-parse \"refs/tags/$TAG_NAME^{commit}\"" in build
    assert 'TAG_SHA" != "$CHECKED_OUT_SHA' in build
    assert "source_sha: ${{ steps.verify_release_ref.outputs.source_sha }}" in build
    assert "Reverify and publish fully assembled release" in build
    assert "Release tag moved after the build; refusing publication." in build

    assert "ref: ${{ inputs.source_sha }}" in build_verification
    assert '"git_commit": os.environ["SOURCE_SHA"]' in build_verification
    assert "ref: ${{ inputs.source_sha }}" in windows_build

    assert "build_info.get(\"git_commit\") != expected_sha" in create_release
    assert "--verify-tag" in create_release
    assert "--certificate-identity \"$CERT_IDENTITY\"" in create_release
    assert "--certificate-oidc-issuer \"$CERT_ISSUER\"" in create_release
    assert create_release.count("cosign verify-blob") >= 2


def test_release_reruns_refresh_checksums_and_verify_uploaded_assets():
    create_release = (
        REPO_ROOT / ".github" / "workflows" / "create-release.yml"
    ).read_text(encoding="utf-8")

    assert "<!-- simkl-mps-verification-start -->" in create_release
    assert "<!-- simkl-mps-source-sha:$SOURCE_SHA -->" in create_release
    assert "DRAFT_SOURCE_SHA" in create_release
    assert 'DRAFT_SOURCE_SHA" != "$SOURCE_SHA' in create_release
    create_start = create_release.index('gh release create "$TAG_NAME"')
    first_release_view = create_release.index(
        'gh release view "$TAG_NAME" --json body',
        create_start,
    )
    initial_create = create_release[create_start:first_release_view]
    assert '--notes-file "$VERIFICATION_BLOCK_FILE"' in initial_create
    assert 'gh release edit "$TAG_NAME" --notes-file "$UPDATED_BODY_FILE"' in create_release
    assert 'gh release download "$TAG_NAME" "${DOWNLOAD_ARGS[@]}"' in create_release
    assert 'cmp --silent "$LOCAL_PATH" "$DOWNLOADED_PATH"' in create_release
    assert "Consider manual review/update if needed" not in create_release


def test_existing_pypi_version_must_match_local_distribution_digests():
    publish = (
        REPO_ROOT / ".github" / "workflows" / "publish-pypi.yml"
    ).read_text(encoding="utf-8")

    assert "https://pypi.org/pypi/simkl-mps/{version}/json" in publish
    assert "local_names != remote_names" in publish
    assert "hashlib.sha256(path.read_bytes()).hexdigest()" in publish
    assert "already_exists=true" in publish
    assert "steps.pypi_preflight.outputs.already_exists != 'true'" in publish
    assert "poetry publish --skip-existing" not in publish


def test_pypi_publish_is_reusable_only_and_revalidates_tag_last():
    publish = (
        REPO_ROOT / ".github" / "workflows" / "publish-pypi.yml"
    ).read_text(encoding="utf-8")

    assert "workflow_dispatch:" not in publish
    assert "inputs.source_sha || github.sha" not in publish
    assert "ref: ${{ inputs.source_sha }}" in publish
    validation = "Revalidate release tag and source immediately before PyPI publication"
    assert validation in publish
    assert 'TAG_SHA" != "$SOURCE_SHA' in publish
    validation_start = publish.index(validation)
    publish_start = publish.index("- name: Publish to PyPI")
    assert validation_start < publish_start
    assert "\n      - name:" not in publish[validation_start + len(validation) : publish_start]


def test_release_runs_are_serialized_and_promotion_reverifies_assets():
    build = (REPO_ROOT / ".github" / "workflows" / "build.yml").read_text(
        encoding="utf-8"
    )

    assert "group: release-${{ github.repository }}-${{ github.ref }}" in build
    assert "cancel-in-progress: false" in build
    assert "name: windows-installer" in build
    assert "Reverify and publish fully assembled release" in build
    assert 'cmp --silent "$EXPECTED_PATH"' in build
    assert 'verify_release_asset "$EXPECTED_INSTALLER"' in build
    assert 'verify_release_asset "$EXPECTED_WHEEL"' in build
    assert 'verify_release_asset "$EXPECTED_SDIST"' in build
    assert 'cosign verify-blob \\' in build
    assert "<!-- simkl-mps-source-sha:$SOURCE_SHA -->" in build
    assert "EXPECTED_ROW=" in build
    assert build.index("Reverify and publish fully assembled release") < build.index(
        "--draft=false"
    )


def test_release_workflow_run_scripts_do_not_interpolate_actions_expressions():
    workflow_names = (
        "build.yml",
        "build-verification.yml",
        "windows-build.yml",
        "create-release.yml",
        "publish-pypi.yml",
    )

    for workflow_name in workflow_names:
        path = REPO_ROOT / ".github" / "workflows" / workflow_name
        lines = path.read_text(encoding="utf-8").splitlines()
        for index, line in enumerate(lines):
            stripped = line.lstrip()
            if not stripped.startswith("run:"):
                continue

            assert "${{" not in line, f"{workflow_name}:{index + 1}"
            if stripped not in {"run: |", "run: >", "run: |-", "run: >-"}:
                continue

            run_indent = len(line) - len(stripped)
            for nested_index in range(index + 1, len(lines)):
                nested = lines[nested_index]
                if not nested.strip():
                    continue
                nested_indent = len(nested) - len(nested.lstrip())
                if nested_indent <= run_indent:
                    break
                assert "${{" not in nested, (
                    f"{workflow_name}:{nested_index + 1} interpolates an Actions "
                    "expression directly into a shell script"
                )


def test_pyinstaller_build_does_not_kill_running_user_processes():
    spec = (REPO_ROOT / "simkl-mps.spec").read_text(encoding="utf-8")

    assert "taskkill" not in spec.lower()


def test_windows_release_build_isolates_poetry_from_synced_environment():
    workflow = (
        REPO_ROOT / ".github" / "workflows" / "windows-build.yml"
    ).read_text(encoding="utf-8")

    assert 'POETRY_VIRTUALENVS_IN_PROJECT: "true"' in workflow
    assert 'python -m pip install "poetry==2.4.1"' in workflow
    assert "poetry config virtualenvs.create false" not in workflow
    assert "poetry install --no-interaction --sync" not in workflow
    assert "poetry sync --no-interaction" in workflow
    assert "pip install pyinstaller" not in workflow
    assert (
        'poetry run python scripts/validate_release_version.py "$env:RELEASE_VERSION"'
        in workflow
    )
    assert "poetry run python -m PyInstaller --clean simkl-mps.spec" in workflow
    assert "poetry run python test_build.py windows" in workflow
