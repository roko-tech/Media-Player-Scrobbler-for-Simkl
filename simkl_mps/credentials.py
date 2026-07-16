"""
Manages Simkl API credentials.

Client ID and Secret are injected during the build process.
Access Token is loaded from a .env file in the user's application data directory.
"""
import pathlib
import logging
import os
from dotenv import dotenv_values
from .migration import get_app_data_dir, perform_full_migration
from .secure_store import is_protected, protect_secret, unprotect_secret

logger = logging.getLogger(__name__)

# Perform migration on import
try:
    perform_full_migration()
except Exception as e:
    logger.warning(f"Migration warning: {e}")


# --- Injected by build process ---
# These placeholders are replaced by the build workflow.
CLIENT_ID_PLACEHOLDER = "SIMKL_CLIENT_ID_PLACEHOLDER"
CLIENT_SECRET_PLACEHOLDER = "SIMKL_CLIENT_SECRET_PLACEHOLDER"
# --- End of injected values ---

# Patched for source builds: if the build process did NOT inject real values,
# treat the credentials as empty so get_credentials() falls back to runtime
# sources (env vars / .simkl_mps.env / .env) instead of sending the literal
# placeholder to the Simkl API (which returns 412 client_id_failed).
SIMKL_CLIENT_ID = "" if "PLACEHOLDER" in CLIENT_ID_PLACEHOLDER else CLIENT_ID_PLACEHOLDER
SIMKL_CLIENT_SECRET = "" if "PLACEHOLDER" in CLIENT_SECRET_PLACEHOLDER else CLIENT_SECRET_PLACEHOLDER

APP_NAME_FOR_PATH = "simkl-mps"
USER_SUBDIR_FOR_PATH = "kavin"  # Updated from kavinthangavel
try:
    # Use migration-aware directory path
    APP_DATA_DIR_FOR_PATH = get_app_data_dir()
    ENV_FILE_PATH = APP_DATA_DIR_FOR_PATH / ".simkl_mps.env"
    logger.debug(f"Using env file path: {ENV_FILE_PATH}")
except Exception as e:

    logger.warning(f"Could not determine home directory ({e}), using fallback env path.")
    ENV_FILE_PATH = pathlib.Path(".simkl_mps.env")


DEV_CREDS_PATH = pathlib.Path(".env")


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
    temp.write_text("".join(lines), encoding="utf-8")
    temp.replace(path)


def _load_secure_env(path, migrate=True):
    config = dict(dotenv_values(path))
    replacements = {}
    for key in ("SIMKL_CLIENT_SECRET", "SIMKL_ACCESS_TOKEN"):
        stored = config.get(key)
        if not stored:
            continue
        config[key] = unprotect_secret(stored)
        if migrate and os.name == "nt" and not is_protected(stored):
            replacements[key] = protect_secret(stored)
    if replacements:
        try:
            _replace_env_values(path, replacements)
            logger.info("Protected plaintext Simkl secrets with Windows DPAPI.")
        except OSError as exc:
            logger.warning("Could not migrate Simkl secrets to DPAPI yet: %s", exc)
    return config


SIMKL_ACCESS_TOKEN = None
if ENV_FILE_PATH.exists():
    logger.debug(f"Loading access token from {ENV_FILE_PATH}")
    config = _load_secure_env(ENV_FILE_PATH)
    SIMKL_ACCESS_TOKEN = config.get("SIMKL_ACCESS_TOKEN")
    if not SIMKL_ACCESS_TOKEN:
        logger.warning(f"Found env file at {ENV_FILE_PATH}, but SIMKL_ACCESS_TOKEN key is missing or empty.")
else:
    logger.debug(f"Env file not found at {ENV_FILE_PATH}")



def get_credentials():
    """
    Retrieves the Simkl API credentials.

    Client ID/Secret are read from module-level variables (injected at build).
    Access Token, User ID, and cached account metadata are read directly from the .env file *each time* this function
    is called to ensure the latest values are used.

    Returns:
        dict: A dictionary containing 'client_id', 'client_secret',
              'access_token', and 'user_id'. Values might be None if not configured
              or if the build/init process failed.
    """

    client_id = None
    client_secret = None

    if SIMKL_CLIENT_ID:
        client_id = SIMKL_CLIENT_ID
    if SIMKL_CLIENT_SECRET:
        client_secret = SIMKL_CLIENT_SECRET

    if client_id and client_secret:
        logger.debug("Using build-injected SIMKL client credentials.")
    else:
        logger.debug("Build-injected credentials missing/placeholder, trying runtime sources...")

        # Check environment variables
        env_client_id = os.environ.get("SIMKL_CLIENT_ID")
        env_client_secret = os.environ.get("SIMKL_CLIENT_SECRET")
        
        if env_client_id:
            client_id = env_client_id
        if env_client_secret:
            client_secret = env_client_secret

        # Fall back to app env file used by end users (.simkl_mps.env)
        env_file_path = get_env_file_path()
        if (not client_id or not client_secret) and env_file_path.exists():
            logger.debug(f"Loading runtime credentials from {env_file_path}")
            runtime_config = _load_secure_env(env_file_path)
            
            runtime_client_id = runtime_config.get("SIMKL_CLIENT_ID")
            runtime_client_secret = runtime_config.get("SIMKL_CLIENT_SECRET")
            
            if runtime_client_id:
                client_id = client_id or runtime_client_id
            if runtime_client_secret:
                client_secret = client_secret or runtime_client_secret

        # Final fallback for local development
        if (not client_id or not client_secret) and DEV_CREDS_PATH.exists():
            logger.debug(f"Loading development credentials from {DEV_CREDS_PATH}")
            dev_config = dotenv_values(DEV_CREDS_PATH)
            
            dev_client_id = dev_config.get("SIMKL_CLIENT_ID")
            dev_client_secret = dev_config.get("SIMKL_CLIENT_SECRET")
            
            if dev_client_id:
                client_id = client_id or dev_client_id
            if dev_client_secret:
                client_secret = client_secret or dev_client_secret

    access_token = None
    user_id = None
    account_type = None
    settings_all = None
    env_file_path = get_env_file_path()
    if env_file_path.exists():
        logger.debug(f"Reading credentials from {env_file_path} inside get_credentials()")
        config = _load_secure_env(env_file_path)

        access_token = config.get("SIMKL_ACCESS_TOKEN")
        user_id = config.get("SIMKL_USER_ID")
        account_type = config.get("SIMKL_ACCOUNT_TYPE")
        settings_all = config.get("SIMKL_SETTINGS_ALL")

        if user_id:
            logger.debug(f"Found user ID in env file: {user_id}")
        else:
            logger.debug("User ID not found in env file")

        if account_type:
            logger.debug(f"Found account type in env file: {account_type}")

        if not access_token:
            logger.warning(
                f"Found env file at {env_file_path}, but SIMKL_ACCESS_TOKEN key is missing or empty."
            )
    else:
        logger.debug(f"Env file not found at {env_file_path} inside get_credentials()")

    if not client_id or not client_secret:
        logger.warning(
            "Client ID or Secret not found. For local development, create a .env file with SIMKL_CLIENT_ID and SIMKL_CLIENT_SECRET."
        )

    return {
        "client_id": client_id,
        "client_secret": client_secret,
        "access_token": access_token,
        "user_id": user_id,
        "account_type": account_type,
        "settings_all": settings_all
    }

def get_env_file_path():
    """
    Returns the calculated path to the .env file used for the access token.

    Returns:
        pathlib.Path: The path object for the .env file.
    """
    return ENV_FILE_PATH
