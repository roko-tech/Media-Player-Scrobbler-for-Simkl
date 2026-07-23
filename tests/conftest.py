import os
import shutil
import tempfile
from pathlib import Path


_PROFILE_OVERRIDE = os.environ.get("SIMKL_MPS_TEST_PROFILE")
_OWNS_PROFILE = not _PROFILE_OVERRIDE
_TEST_PROFILE = Path(
    _PROFILE_OVERRIDE or tempfile.mkdtemp(prefix="simkl-mps-pytest-")
).resolve()


def _isolate_user_profile():
    roaming = _TEST_PROFILE / "AppData" / "Roaming"
    local = _TEST_PROFILE / "AppData" / "Local"
    temp_dir = _TEST_PROFILE / "Temp"
    for path in (roaming, local, temp_dir):
        path.mkdir(parents=True, exist_ok=True)

    isolated = {
        "USERPROFILE": _TEST_PROFILE,
        "HOME": _TEST_PROFILE,
        "APPDATA": roaming,
        "LOCALAPPDATA": local,
        "XDG_CONFIG_HOME": _TEST_PROFILE / ".config",
        "XDG_DATA_HOME": _TEST_PROFILE / ".local" / "share",
        "TEMP": temp_dir,
        "TMP": temp_dir,
    }
    for name, path in isolated.items():
        os.environ[name] = str(path)


_isolate_user_profile()


def pytest_sessionfinish(session, exitstatus):
    if _OWNS_PROFILE:
        shutil.rmtree(_TEST_PROFILE, ignore_errors=True)
