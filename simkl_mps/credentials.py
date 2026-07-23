"""
Manages Simkl API credentials.

The public Client ID is injected during the build process.
The user's Access Token is loaded from the application data directory.
"""
import pathlib
import logging
import os
from dotenv import dotenv_values
from .migration import perform_full_migration
from .secure_store import (
    SecretProtectionError,
    ensure_private_file,
    is_protected,
    open_private_text_file,
    protect_secret,
    unprotect_secret,
)

logger = logging.getLogger(__name__)

# The build replaces this public identifier. Public desktop clients must never
# embed a client secret; Simkl PIN authentication requires only the Client ID.
CLIENT_ID_PLACEHOLDER = "SIMKL_CLIENT_ID_PLACEHOLDER"
SIMKL_CLIENT_ID = "" if "PLACEHOLDER" in CLIENT_ID_PLACEHOLDER else CLIENT_ID_PLACEHOLDER

# Compatibility only for developers with an older confidential-client setup.
SIMKL_CLIENT_SECRET = ""

APP_NAME_FOR_PATH = "simkl-mps"
USER_SUBDIR_FOR_PATH = "kavin"  # Updated from kavinthangavel
try:
    APP_DATA_DIR_FOR_PATH = pathlib.Path.home() / USER_SUBDIR_FOR_PATH / APP_NAME_FOR_PATH
    ENV_FILE_PATH = APP_DATA_DIR_FOR_PATH / ".simkl_mps.env"
    logger.debug(f"Using env file path: {ENV_FILE_PATH}")
except Exception as e:

    logger.warning(f"Could not determine home directory ({e}), using fallback env path.")
    ENV_FILE_PATH = pathlib.Path(".simkl_mps.env")


DEV_CREDS_PATH = pathlib.Path(".env")
_BOOTSTRAP_COMPLETE = False


def bootstrap_credentials():
    """Run stateful legacy-data migration from an explicit runtime entry point."""
    global _BOOTSTRAP_COMPLETE
    if _BOOTSTRAP_COMPLETE:
        return True
    try:
        perform_full_migration()
        _BOOTSTRAP_COMPLETE = True
        return True
    except Exception as exc:
        logger.warning("Credential migration warning: %s", exc)
        return False


def _replace_env_values(path, replacements):
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    remaining = dict(replacements)
    for index, line in enumerate(lines):
        key = line.split("=", 1)[0].strip() if "=" in line else None
        if key in remaining:
            ending = "\n" if line.endswith("\n") else ""
            lines[index] = f"{key}={remaining.pop(key)}{ending}"
    for key, value in remaining.items():
        lines.append(f"{key}={value}\n")
    temp = path.with_suffix(path.suffix + ".tmp")
    with open_private_text_file(temp) as handle:
        handle.write("".join(lines))
        handle.flush()
        os.fsync(handle.fileno())
    temp.replace(path)
    ensure_private_file(path)


def _load_secure_env(path, migrate=True):
    if os.name != "nt" and pathlib.Path(path).exists():
        try:
            ensure_private_file(path)
        except OSError as exc:
            logger.warning("Could not restrict Simkl credential file permissions: %s", exc)
    config = dict(dotenv_values(path))
    replacements = {}
    for key in ("SIMKL_CLIENT_SECRET", "SIMKL_ACCESS_TOKEN"):
        stored = config.get(key)
        if not stored:
            continue
        try:
            config[key] = unprotect_secret(stored)
        except SecretProtectionError as exc:
            logger.error(
                "Could not open stored %s; treating it as unavailable until re-authentication: %s",
                key,
                exc,
            )
            config[key] = None
            continue
        if migrate and os.name == "nt" and not is_protected(stored):
            replacements[key] = protect_secret(stored)
    if replacements:
        try:
            _replace_env_values(path, replacements)
            logger.info("Protected plaintext Simkl secrets with Windows DPAPI.")
        except OSError as exc:
            logger.warning("Could not migrate Simkl secrets to DPAPI yet: %s", exc)
    return config


def get_credentials():
    """Return the public Simkl client ID and the user's saved credentials.

    Desktop builds embed only the public Client ID. ``client_secret`` remains in
    the returned mapping solely so older local configurations can be read and
    migrated without breaking callers; public PIN authentication does not use it.
    """
    client_id = SIMKL_CLIENT_ID or None
    client_secret = SIMKL_CLIENT_SECRET or None

    env_client_id = os.environ.get("SIMKL_CLIENT_ID")
    env_client_secret = os.environ.get("SIMKL_CLIENT_SECRET")
    client_id = client_id or env_client_id
    client_secret = client_secret or env_client_secret

    access_token = None
    user_id = None
    account_type = None
    settings_all = None
    env_file_path = get_env_file_path()
    if env_file_path.exists():
        logger.debug("Reading credentials from %s", env_file_path)
        try:
            config = _load_secure_env(env_file_path)
        except (OSError, UnicodeError) as exc:
            logger.error(
                "Could not read the Simkl credential file; continuing without saved credentials: %s",
                exc,
            )
            config = {}
        client_id = client_id or config.get("SIMKL_CLIENT_ID")
        client_secret = client_secret or config.get("SIMKL_CLIENT_SECRET")
        access_token = config.get("SIMKL_ACCESS_TOKEN")
        user_id = config.get("SIMKL_USER_ID")
        account_type = config.get("SIMKL_ACCOUNT_TYPE")
        settings_all = config.get("SIMKL_SETTINGS_ALL")
        if not access_token:
            logger.warning(
                "Found env file at %s, but SIMKL_ACCESS_TOKEN is missing or empty.",
                env_file_path,
            )

    if (not client_id or not client_secret) and DEV_CREDS_PATH.exists():
        logger.debug("Loading development credentials from %s", DEV_CREDS_PATH)
        dev_config = dotenv_values(DEV_CREDS_PATH)
        client_id = client_id or dev_config.get("SIMKL_CLIENT_ID")
        client_secret = client_secret or dev_config.get("SIMKL_CLIENT_SECRET")

    if not client_id:
        logger.warning(
            "Simkl Client ID not found. Set SIMKL_CLIENT_ID for a source build."
        )

    return {
        "client_id": client_id,
        "client_secret": client_secret,
        "access_token": access_token,
        "user_id": user_id,
        "account_type": account_type,
        "settings_all": settings_all,
    }

def get_env_file_path():
    """
    Returns the calculated path to the .env file used for the access token.

    Returns:
        pathlib.Path: The path object for the .env file.
    """
    return ENV_FILE_PATH
