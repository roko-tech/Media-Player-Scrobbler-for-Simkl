import requests

from simkl_mps.simkl_api import (
    HistorySyncResult,
    ProviderStatus,
    add_to_history,
)


class Response:
    def __init__(self, status_code, payload=None, text="", headers=None):
        self.status_code = status_code
        self._payload = payload
        self.text = text
        self.headers = headers or {}

    def json(self):
        if self._payload is None:
            raise requests.exceptions.JSONDecodeError("invalid", "", 0)
        return self._payload


def _submit(monkeypatch, response):
    monkeypatch.setattr(
        "simkl_mps.simkl_api.is_internet_connected",
        lambda: True,
    )
    monkeypatch.setattr(
        "simkl_mps.simkl_api.requests.post",
        lambda *args, **kwargs: response,
    )
    return add_to_history({"movies": [{"ids": {"simkl": 1}}]}, "client", "token")


def test_history_sync_returns_typed_accepted_outcome(monkeypatch):
    result = _submit(
        monkeypatch,
        Response(201, {"added": {"movies": 1}, "not_found": {}}),
    )

    assert isinstance(result, HistorySyncResult)
    assert result.status == ProviderStatus.ACCEPTED
    assert result.accepted is True
    assert result.retryable is False


def test_history_sync_treats_semantic_not_found_as_permanent(monkeypatch):
    result = _submit(
        monkeypatch,
        Response(200, {"added": {"movies": 0}, "not_found": {"movies": [{}]}}),
    )

    assert result.status == ProviderStatus.REJECTED
    assert result.accepted is False
    assert result.retryable is False


def test_history_sync_classifies_rate_limit_as_retryable(monkeypatch):
    result = _submit(
        monkeypatch,
        Response(429, {"error": "slow down"}, headers={"Retry-After": "7"}),
    )

    assert result.status == ProviderStatus.RATE_LIMITED
    assert result.retryable is True
    assert result.retry_after == 7


def test_history_sync_classifies_auth_failure_as_permanent(monkeypatch):
    result = _submit(monkeypatch, Response(401, {"error": "invalid token"}))

    assert result.status == ProviderStatus.UNAUTHORIZED
    assert result.retryable is False


def test_history_sync_classifies_connection_failure_as_retryable(monkeypatch):
    monkeypatch.setattr(
        "simkl_mps.simkl_api.is_internet_connected",
        lambda: True,
    )

    def fail(*args, **kwargs):
        raise requests.exceptions.ConnectionError("offline")

    monkeypatch.setattr("simkl_mps.simkl_api.requests.post", fail)

    result = add_to_history({"movies": [{}]}, "client", "token")

    assert result.status == ProviderStatus.NETWORK_ERROR
    assert result.retryable is True
