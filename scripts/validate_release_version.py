"""Fail a release build when repository version declarations disagree."""

import re
import sys
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def _declared_versions():
    pyproject_version = tomllib.loads(
        (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )["tool"]["poetry"]["version"]
    package_match = re.search(
        r'^__version__ = "([^"]+)"$',
        (ROOT / "simkl_mps" / "__init__.py").read_text(encoding="utf-8"),
        re.MULTILINE,
    )
    installer_match = re.search(
        r'^#define MyAppVersion "([^"]+)"$',
        (ROOT / "setup.iss").read_text(encoding="utf-8"),
        re.MULTILINE,
    )
    if not package_match or not installer_match:
        raise ValueError("Could not read every version declaration.")
    return {
        "pyproject.toml": pyproject_version,
        "simkl_mps/__init__.py": package_match.group(1),
        "setup.iss": installer_match.group(1),
    }


def main(expected_version):
    versions = _declared_versions()
    mismatches = {
        path: version for path, version in versions.items() if version != expected_version
    }
    if mismatches:
        details = ", ".join(f"{path}={version}" for path, version in mismatches.items())
        raise SystemExit(
            f"Release version {expected_version} does not match repository declarations: {details}"
        )
    print(f"Validated release version {expected_version} in {len(versions)} files.")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: validate_release_version.py VERSION")
    main(sys.argv[1])
