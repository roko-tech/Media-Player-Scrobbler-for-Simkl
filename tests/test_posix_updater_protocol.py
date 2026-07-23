import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest


UPDATER = (
    Path(__file__).resolve().parent.parent
    / "simkl_mps"
    / "utils"
    / "updater.sh"
)


def test_posix_updater_is_forced_to_lf_line_endings():
    attributes = UPDATER.parents[2] / ".gitattributes"

    assert b"\r" not in UPDATER.read_bytes()
    assert (
        "simkl_mps/utils/updater.sh text eol=lf"
        in attributes.read_text(encoding="utf-8").splitlines()
    )


def _write_executable(path, content):
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


@pytest.mark.skipif(os.name == "nt", reason="POSIX shell protocol test")
@pytest.mark.parametrize(
    ("latest_version", "expected"),
    [
        (
            "2.0.0",
            "UPDATE_AVAILABLE: 2.0.0 https://pypi.org/project/simkl-mps/2.0.0/",
        ),
        ("1.0.0", "NO_UPDATE: 1.0.0"),
    ],
)
def test_silent_check_only_stdout_is_one_machine_readable_line(
    tmp_path, latest_version, expected
):
    bash = shutil.which("bash")
    if not bash:
        pytest.skip("bash is required for the POSIX updater regression test")

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_executable(
        fake_bin / "simkl-mps",
        "#!/bin/sh\nprintf 'simkl-mps 1.0.0\\n'\n",
    )
    _write_executable(
        fake_bin / "curl",
        "#!/bin/sh\nprintf '{\"info\":{\"version\":\"%s\"}}\\n' "
        '"$FAKE_LATEST_VERSION"\n',
    )
    _write_executable(
        fake_bin / "jq",
        "#!/bin/sh\nprintf '%s\\n' \"$FAKE_LATEST_VERSION\"\n",
    )
    env = os.environ.copy()
    env.update(
        {
            "FAKE_LATEST_VERSION": latest_version,
            "HOME": str(tmp_path / "home"),
            "PATH": os.pathsep.join((str(fake_bin), env["PATH"])),
        }
    )

    result = subprocess.run(
        [bash, str(UPDATER), "--check-only", "--silent"],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.stdout.splitlines() == [expected]
    assert "Update Check Started" in result.stderr
