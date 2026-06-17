from __future__ import annotations

import io
import json
import stat
from pathlib import Path
from typing import Any

import pytest
from rich.console import Console

import sf_report_agent.main as main_module
import sf_report_agent.salesforce.client as client_module
import sf_report_agent.salesforce.oauth as oauth_module
from sf_report_agent.config import Settings
from sf_report_agent.main import _salesforce, command_sf_auth_status, command_sf_oauth_login
from sf_report_agent.salesforce.client import SalesforceClient, SalesforceClientError
from sf_report_agent.salesforce.oauth import (
    SalesforceOAuthToken,
    load_token_file,
    refresh_access_token,
    save_token_file,
)


def _settings(tmp_path: Path, **overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "source_db_path": tmp_path / "source.db",
        "worker_db_path": tmp_path / "worker.db",
        "artifacts_dir": tmp_path / "artifacts",
        "field_mapping_path": None,
        "model_provider": "ollama",
        "ollama_model": "test",
        "ollama_base_url": "http://127.0.0.1:11434",
        "ollama_temperature": 0,
        "salesforce_username": None,
        "salesforce_password": None,
        "salesforce_security_token": None,
        "salesforce_domain": "login",
        "sf_read_only": True,
        "max_export_rows": 100,
        "require_human_approval_for_pii": True,
        "log_pii": False,
        "update_source_task": False,
        "allow_report_without_person_fields": False,
        "salesforce_auth_mode": "oauth",
        "salesforce_client_id": "client-id",
        "salesforce_client_secret": "super-client-secret",
        "salesforce_redirect_uri": "http://localhost:8765/callback",
        "salesforce_refresh_token": None,
        "salesforce_access_token": None,
        "salesforce_instance_url": None,
        "salesforce_token_path": tmp_path / ".salesforce_token.json",
    }
    values.update(overrides)
    return Settings(**values)


def test_settings_oauth_mode(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SALESFORCE_AUTH_MODE", "oauth")
    monkeypatch.setenv("SALESFORCE_CLIENT_ID", "test-client")
    monkeypatch.setenv("SALESFORCE_CLIENT_SECRET", "test-secret")
    monkeypatch.setenv("SALESFORCE_TOKEN_PATH", str(tmp_path / "oauth.json"))
    monkeypatch.delenv("SALESFORCE_SECURITY_TOKEN", raising=False)

    settings = Settings.from_env(tmp_path / "does-not-exist.env")

    assert settings.salesforce_auth_mode == "oauth"
    assert settings.has_salesforce_oauth_client_credentials is True
    assert settings.salesforce_security_token is None
    assert settings.salesforce_token_path == tmp_path / "oauth.json"


def test_oauth_token_file_read_write(tmp_path: Path) -> None:
    path = tmp_path / ".salesforce_token.json"
    token = SalesforceOAuthToken(
        access_token="access-secret",
        refresh_token="refresh-secret",
        instance_url="https://example.my.salesforce.com",
        issued_at="1710000000000",
    )

    save_token_file(token, path)
    loaded = load_token_file(path)

    assert loaded == token
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["refresh_token"] == "refresh-secret"


def test_oauth_refresh_token_request_mock(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    class FakeResponse:
        status_code = 200

        @staticmethod
        def json() -> dict[str, str]:
            return {
                "access_token": "new-access-token",
                "instance_url": "https://example.my.salesforce.com",
                "issued_at": "1710000000001",
                "token_type": "Bearer",
            }

    def fake_post(url: str, *, data: dict[str, str], timeout: int) -> FakeResponse:
        captured.update({"url": url, "data": data, "timeout": timeout})
        return FakeResponse()

    monkeypatch.setattr(oauth_module.requests, "post", fake_post)

    token = refresh_access_token(
        domain="login",
        client_id="client-id",
        client_secret="client-secret",
        refresh_token="refresh-secret",
    )

    assert captured["url"] == "https://login.salesforce.com/services/oauth2/token"
    assert captured["data"]["grant_type"] == "refresh_token"
    assert captured["data"]["refresh_token"] == "refresh-secret"
    assert token.access_token == "new-access-token"
    assert token.refresh_token == "refresh-secret"


def test_salesforce_client_from_session(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    class FakeSalesforce:
        base_url = "https://example.my.salesforce.com/services/data/v59.0/"

    def fake_salesforce(**kwargs: Any) -> FakeSalesforce:
        captured.update(kwargs)
        return FakeSalesforce()

    monkeypatch.setattr(client_module, "Salesforce", fake_salesforce)

    client = SalesforceClient.from_session(
        instance_url="https://example.my.salesforce.com",
        access_token="access-secret",
        username="user@example.org",
    )

    assert captured == {
        "instance_url": "https://example.my.salesforce.com",
        "session_id": "access-secret",
    }
    assert client.instance_url == "https://example.my.salesforce.com"
    assert client.username == "user@example.org"


def test_salesforce_factory_requires_oauth_login_when_no_refresh_token(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)

    with pytest.raises(SalesforceClientError, match="No hay refresh token"):
        _salesforce(settings)


def test_sf_auth_status_masks_secrets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    token_path = tmp_path / ".salesforce_token.json"
    save_token_file(
        SalesforceOAuthToken(
            access_token="old-access-secret",
            refresh_token="refresh-secret",
            instance_url="https://example.my.salesforce.com",
            issued_at="1710000000000",
        ),
        token_path,
    )
    settings = _settings(tmp_path, salesforce_token_path=token_path)

    def fake_refresh_access_token(**kwargs: Any) -> SalesforceOAuthToken:
        assert kwargs["refresh_token"] == "refresh-secret"
        return SalesforceOAuthToken(
            access_token="new-access-secret",
            refresh_token="refresh-secret",
            instance_url="https://example.my.salesforce.com",
            issued_at="1710000000001",
        )

    monkeypatch.setattr(main_module, "refresh_access_token", fake_refresh_access_token)
    stream = io.StringIO()
    output = Console(file=stream, color_system=None, force_terminal=False, width=120)

    status = command_sf_auth_status(settings, output=output)
    rendered = stream.getvalue()

    assert status == 0
    assert "Puede refrescar access token" in rendered
    assert "sí" in rendered
    for secret in (
        "old-access-secret",
        "new-access-secret",
        "refresh-secret",
        "super-client-secret",
    ):
        assert secret not in rendered


def test_sf_oauth_login_saves_tokens_without_printing_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path)

    class FakeReceiver:
        def __init__(self, *, redirect_uri: str, expected_state: str) -> None:
            assert redirect_uri == "http://localhost:8765/callback"
            assert expected_state

        @staticmethod
        def wait_for_code() -> str:
            return "authorization-code-secret"

    def fake_exchange_authorization_code(**kwargs: Any) -> SalesforceOAuthToken:
        assert kwargs["code"] == "authorization-code-secret"
        return SalesforceOAuthToken(
            access_token="access-secret",
            refresh_token="refresh-secret",
            instance_url="https://example.my.salesforce.com",
            issued_at="1710000000000",
        )

    monkeypatch.setattr(main_module, "OAuthCallbackReceiver", FakeReceiver)
    monkeypatch.setattr(
        main_module, "exchange_authorization_code", fake_exchange_authorization_code
    )
    monkeypatch.setattr(main_module.webbrowser, "open", lambda _: False)
    stream = io.StringIO()
    monkeypatch.setattr(
        main_module,
        "console",
        Console(file=stream, color_system=None, force_terminal=False, width=200),
    )

    status = command_sf_oauth_login(settings)
    rendered = stream.getvalue()

    assert status == 0
    assert load_token_file(settings.salesforce_token_path) is not None
    assert "Refresh token presente: sí" in rendered
    for secret in (
        "authorization-code-secret",
        "access-secret",
        "refresh-secret",
        "super-client-secret",
    ):
        assert secret not in rendered


def test_password_mode_still_supported(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    class FakeSalesforce:
        base_url = "https://example.my.salesforce.com/services/data/v59.0/"

    def fake_salesforce(**kwargs: Any) -> FakeSalesforce:
        captured.update(kwargs)
        return FakeSalesforce()

    monkeypatch.setattr(client_module, "Salesforce", fake_salesforce)
    settings = _settings(
        tmp_path,
        salesforce_auth_mode="password",
        salesforce_username="legacy@example.org",
        salesforce_password="legacy-password",
        salesforce_security_token="legacy-security-token",
    )

    client = _salesforce(settings)

    assert client.username == "legacy@example.org"
    assert captured["username"] == "legacy@example.org"
    assert captured["password"] == "legacy-password"
    assert captured["security_token"] == "legacy-security-token"
