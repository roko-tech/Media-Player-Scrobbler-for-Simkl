import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def _profile_environment(profile):
    environment = os.environ.copy()
    environment.update(
        {
            "USERPROFILE": str(profile),
            "HOME": str(profile),
            "APPDATA": str(profile / "AppData" / "Roaming"),
            "LOCALAPPDATA": str(profile / "AppData" / "Local"),
            "XDG_CONFIG_HOME": str(profile / ".config"),
            "XDG_DATA_HOME": str(profile / ".local" / "share"),
        }
    )
    current_pythonpath = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = os.pathsep.join(
        part for part in (str(REPO_ROOT), current_pythonpath) if part
    )
    return environment


def test_package_import_is_passive(tmp_path):
    profile = tmp_path / "profile"
    profile.mkdir()
    sentinel = profile / "sentinel.txt"
    sentinel.write_text("unchanged", encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import simkl_mps; assert simkl_mps.__version__ == '2.5.0'",
        ],
        cwd=tmp_path,
        env=_profile_environment(profile),
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    assert list(profile.iterdir()) == [sentinel]
    assert sentinel.read_text(encoding="utf-8") == "unchanged"


def test_version_flag_does_not_require_a_subcommand(tmp_path):
    profile = tmp_path / "profile"
    profile.mkdir()

    result = subprocess.run(
        [sys.executable, "-m", "simkl_mps.cli", "--version"],
        cwd=tmp_path,
        env=_profile_environment(profile),
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    assert "simkl-mps v" in result.stdout


def test_legacy_entrypoint_exports_remain_available(tmp_path):
    profile = tmp_path / "profile"
    profile.mkdir()
    code = (
        "import simkl_mps; "
        "assert 'SimklScrobbler' not in simkl_mps.__dict__; "
        "assert simkl_mps.SimklScrobbler.__name__ == 'SimklScrobbler'"
    )

    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=tmp_path,
        env=_profile_environment(profile),
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr


def test_collection_redirects_application_profile(tmp_path):
    real_profile = tmp_path / "real-profile"
    real_profile.mkdir()
    sentinel = real_profile / "sentinel.txt"
    sentinel.write_text("unchanged", encoding="utf-8")
    test_profile = tmp_path / "isolated-profile"
    environment = _profile_environment(real_profile)
    environment["SIMKL_MPS_TEST_PROFILE"] = str(test_profile)

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/test_trakt_sync.py",
            "--collect-only",
            "-q",
            "-p",
            "no:cacheprovider",
            "--basetemp",
            str(tmp_path / "child-pytest"),
        ],
        cwd=REPO_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == 0, result.stderr
    assert list(real_profile.iterdir()) == [sentinel]
    assert sentinel.read_text(encoding="utf-8") == "unchanged"
