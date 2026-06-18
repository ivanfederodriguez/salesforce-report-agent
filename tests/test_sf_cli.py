from __future__ import annotations

import io
import subprocess
from pathlib import Path
from typing import Any

import pytest
from rich.console import Console

import sf_report_agent.main as main_module
import sf_report_agent.salesforce.client as client_module
import sf_report_agent.salesforce.sf_cli as sf_cli_module
from sf_report_agent.config import Settings
from sf_report_agent.main import _salesforce, command_sf_auth_status
from sf_report_agent.salesforce.sf_cli import (
    SalesforceCliError,
    SalesforceCliSession,
    load_salesforce_cli_session,
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
        "salesforce_auth_mode": "sf_cli",
        "salesforce_cli_alias": "techo",
    }
    values.update(overrides)
    return Settings(**values)


def test_settings_sf_cli_mode(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SALESFORCE_AUTH_MODE", "sf_cli")
    monkeypatch.setenv("SALESFORCE_CLI_ALIAS", "techo")

    settings = Settings.from_env(tmp_path / "does-not-exist.env")

    assert settings.salesforce_auth_mode == "sf_cli"
    assert settings.salesforce_cli_alias == "techo"


def test_settings_sf_cli_requires_alias(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SALESFORCE_AUTH_MODE", "sf_cli")
    monkeypatch.delenv("SALESFORCE_CLI_ALIAS", raising=False)

    with pytest.raises(ValueError, match="SALESFORCE_CLI_ALIAS"):
        Settings.from_env(tmp_path / "does-not-exist.env")


def test_load_salesforce_cli_session(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        captured.update({"command": command, **kwargs})
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=(
                '{"status": 0, "result": {'
                '"accessToken": "cli-access-secret", '
                '"instanceUrl": "https://example.my.salesforce.com/", '
                '"username": "user@example.org"}}'
            ),
            stderr="",
        )

    monkeypatch.setattr(sf_cli_module.subprocess, "run", fake_run)

    session = load_salesforce_cli_session("techo")

    assert captured["command"] == [
        "sf",
        "org",
        "display",
        "--target-org",
        "techo",
        "--json",
    ]
    assert captured["capture_output"] is True
    assert captured["check"] is False
    assert session == SalesforceCliSession(
        access_token="cli-access-secret",
        instance_url="https://example.my.salesforce.com",
        username="user@example.org",
    )


def test_load_salesforce_cli_session_handles_cli_error_without_raw_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            command,
            1,
            stdout='{"status": 1, "message": "expired cli-access-secret"}',
            stderr="another-secret",
        )

    monkeypatch.setattr(sf_cli_module.subprocess, "run", fake_run)

    with pytest.raises(SalesforceCliError) as raised:
        load_salesforce_cli_session("techo")

    assert "techo" in str(raised.value)
    assert "cli-access-secret" not in str(raised.value)
    assert "another-secret" not in str(raised.value)


def test_salesforce_factory_uses_sf_cli_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}

    class FakeSalesforce:
        base_url = "https://example.my.salesforce.com/services/data/v59.0/"

    def fake_salesforce(**kwargs: Any) -> FakeSalesforce:
        captured.update(kwargs)
        return FakeSalesforce()

    monkeypatch.setattr(client_module, "Salesforce", fake_salesforce)
    monkeypatch.setattr(
        main_module,
        "load_salesforce_cli_session",
        lambda alias: SalesforceCliSession(
            access_token="cli-access-secret",
            instance_url="https://example.my.salesforce.com",
            username="user@example.org",
        ),
    )

    client = _salesforce(_settings(tmp_path))

    assert client.username == "user@example.org"
    assert captured == {
        "instance_url": "https://example.my.salesforce.com",
        "session_id": "cli-access-secret",
    }


def test_sf_auth_status_checks_cli_without_printing_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        main_module,
        "load_salesforce_cli_session",
        lambda alias: SalesforceCliSession(
            access_token="cli-access-secret",
            instance_url="https://example.my.salesforce.com",
            username="user@example.org",
        ),
    )
    stream = io.StringIO()
    output = Console(file=stream, color_system=None, force_terminal=False, width=120)

    status = command_sf_auth_status(_settings(tmp_path), output=output)
    rendered = stream.getvalue()

    assert status == 0
    assert "CLI target org" in rendered
    assert "techo" in rendered
    assert "Sesión Salesforce CLI válida" in rendered
    assert "cli-access-secret" not in rendered

