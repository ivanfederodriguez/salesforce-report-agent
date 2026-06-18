from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from typing import Any


class SalesforceCliError(RuntimeError):
    """Error seguro al recuperar una sesión ya autenticada por Salesforce CLI."""


@dataclass(frozen=True, slots=True)
class SalesforceCliSession:
    access_token: str
    instance_url: str
    username: str | None
    org_id: str | None = None


def _parse_json(value: str) -> dict[str, Any] | None:
    try:
        payload = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return None
    return payload if isinstance(payload, dict) else None


def _run_json_command(
    command: list[str],
    *,
    timeout_seconds: int,
    command_error: str,
    invalid_json_error: str,
) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            check=False,
            text=True,
            timeout=timeout_seconds,
        )
    except FileNotFoundError:
        raise SalesforceCliError(
            "No se encontró Salesforce CLI ('sf') en PATH. Instalalo y ejecutá "
            "'sf org login web' antes de usar sf_cli."
        ) from None
    except subprocess.TimeoutExpired:
        raise SalesforceCliError(
            f"Salesforce CLI no respondió en {timeout_seconds} segundos."
        ) from None
    except OSError:
        raise SalesforceCliError("No se pudo ejecutar Salesforce CLI.") from None

    if completed.returncode != 0:
        # Never include stdout or stderr: either stream can contain an access token.
        raise SalesforceCliError(command_error)
    payload = _parse_json(completed.stdout)
    if payload is None:
        raise SalesforceCliError(invalid_json_error)
    return payload


def _result(payload: dict[str, Any], *, error: str) -> dict[str, Any]:
    result = payload.get("result")
    if not isinstance(result, dict):
        raise SalesforceCliError(error)
    return result


def _token_is_missing_or_redacted(value: object) -> bool:
    return (
        not isinstance(value, str)
        or not value.strip()
        or value.lstrip().startswith("[REDACTED]")
    )


def load_salesforce_cli_session(
    target_org: str,
    *,
    executable: str = "sf",
    timeout_seconds: int = 30,
) -> SalesforceCliSession:
    """Read a current access token from Salesforce CLI without persisting it locally."""

    if not target_org.strip():
        raise SalesforceCliError("SALESFORCE_CLI_ALIAS no puede estar vacío.")
    command = [
        executable,
        "org",
        "display",
        "--target-org",
        target_org,
        "--json",
    ]
    payload = _run_json_command(
        command,
        timeout_seconds=timeout_seconds,
        command_error=(
            f"Salesforce CLI no pudo abrir la org '{target_org}'. "
            f"Reautenticá con 'sf org login web --alias {target_org}'."
        ),
        invalid_json_error="Salesforce CLI devolvió una respuesta JSON inválida.",
    )

    result = _result(payload, error="Salesforce CLI no devolvió los datos de la org.")
    access_token = result.get("accessToken")
    instance_url = result.get("instanceUrl")
    username = result.get("username")
    org_id = result.get("id")
    if not isinstance(instance_url, str) or not instance_url.startswith("https://"):
        raise SalesforceCliError("Salesforce CLI no devolvió una instance URL HTTPS válida.")

    if _token_is_missing_or_redacted(access_token):
        token_command = [
            executable,
            "org",
            "auth",
            "show-access-token",
            "--target-org",
            target_org,
            "--json",
        ]
        token_payload = _run_json_command(
            token_command,
            timeout_seconds=timeout_seconds,
            command_error=(
                f"Salesforce CLI no pudo recuperar el access token de la org "
                f"'{target_org}'. Reautenticá con 'sf org login web --alias {target_org}'."
            ),
            invalid_json_error=(
                "Salesforce CLI devolvió una respuesta JSON inválida al recuperar "
                "el access token."
            ),
        )
        token_result = _result(
            token_payload,
            error="Salesforce CLI no devolvió los datos del access token.",
        )
        access_token = token_result.get("accessToken")

    if _token_is_missing_or_redacted(access_token):
        raise SalesforceCliError(
            "Salesforce CLI no devolvió un access token. Reautenticá la org y volvé a intentar."
        )
    assert isinstance(access_token, str)

    return SalesforceCliSession(
        access_token=access_token,
        instance_url=instance_url.rstrip("/"),
        username=username if isinstance(username, str) and username else None,
        org_id=org_id if isinstance(org_id, str) and org_id else None,
    )
