import json
import os
import stat

import pytest

from simkl_mps import credentials, simkl_api, trakt_sync
from simkl_mps.secure_store import is_protected, protect_secret, unprotect_secret


@pytest.mark.skipif(os.name != "nt", reason="Windows DPAPI only")
def test_dpapi_round_trip_is_not_plaintext():
    protected = protect_secret("test-only-secret")

    assert protected != "test-only-secret"
    assert is_protected(protected)
    assert unprotect_secret(protected) == "test-only-secret"


@pytest.mark.skipif(os.name != "nt", reason="Windows DPAPI only")
def test_trakt_plaintext_files_migrate_on_read(tmp_path, monkeypatch):
    config_file = tmp_path / "trakt_config.json"
    token_file = tmp_path / "trakt_token.json"
    monkeypatch.setattr(trakt_sync, "CONFIG_FILE", config_file)
    monkeypatch.setattr(trakt_sync, "TOKEN_FILE", token_file)
    config_file.write_text(
        json.dumps({"client_id": "public-id", "client_secret": "test-client-secret"}),
        encoding="utf-8",
    )
    token_file.write_text(
        json.dumps(
            {
                "access_token": "test-access-token",
                "refresh_token": "test-refresh-token",
                "created_at": 0,
                "expires_in": 9999999999,
            }
        ),
        encoding="utf-8",
    )

    config = trakt_sync.trakt_config()
    token = trakt_sync.trakt_token(config)
    stored_config = json.loads(config_file.read_text(encoding="utf-8"))
    stored_token = json.loads(token_file.read_text(encoding="utf-8"))

    assert config["client_secret"] == "test-client-secret"
    assert token == "test-access-token"
    assert is_protected(stored_config["client_secret"])
    assert is_protected(stored_token["access_token"])
    assert is_protected(stored_token["refresh_token"])


@pytest.mark.skipif(os.name != "nt", reason="Windows DPAPI only")
def test_simkl_plaintext_env_migrates_on_read(tmp_path, monkeypatch):
    env_file = tmp_path / ".simkl_mps.env"
    env_file.write_text(
        "SIMKL_CLIENT_ID=public-id\n"
        "SIMKL_CLIENT_SECRET=test-client-secret\n"
        "SIMKL_ACCESS_TOKEN=test-access-token\n"
        "SIMKL_USER_ID=123\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(credentials, "ENV_FILE_PATH", env_file)
    monkeypatch.setattr(credentials, "SIMKL_CLIENT_ID", "")
    monkeypatch.setattr(credentials, "SIMKL_CLIENT_SECRET", "")
    monkeypatch.setattr(credentials, "DEV_CREDS_PATH", tmp_path / "missing.env")
    monkeypatch.delenv("SIMKL_CLIENT_ID", raising=False)
    monkeypatch.delenv("SIMKL_CLIENT_SECRET", raising=False)

    result = credentials.get_credentials()
    stored = dict(credentials.dotenv_values(env_file))

    assert result["client_secret"] == "test-client-secret"
    assert result["access_token"] == "test-access-token"
    assert is_protected(stored["SIMKL_CLIENT_SECRET"])
    assert is_protected(stored["SIMKL_ACCESS_TOKEN"])


@pytest.mark.skipif(os.name == "nt", reason="POSIX file modes only")
def test_posix_secret_files_are_owner_only(tmp_path):
    trakt_file = tmp_path / "trakt_token.json"
    env_file = tmp_path / ".simkl_mps.env"

    trakt_sync.save_secret_json(
        trakt_file,
        {"access_token": "test-token", "refresh_token": "test-refresh"},
        ("access_token", "refresh_token"),
    )
    assert simkl_api._save_access_token(env_file, "test-token")

    assert stat.S_IMODE(trakt_file.stat().st_mode) == 0o600
    assert stat.S_IMODE(env_file.stat().st_mode) == 0o600
