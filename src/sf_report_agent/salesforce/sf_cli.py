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


def _parse_json(value: str) -> dict[str, Any] | None:
    try:
        payload = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return None
    return payload if isinstance(payload, dict) else None


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
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            check=False,
            text=True,
            timeout=timeout_seconds,
        )
    except FileNotFoundError as exc:
        raise SalesforceCliError(
            "No se encontró Salesforce CLI ('sf') en PATH. Instalalo y ejecutá "
            "'sf org login web' antes de usar sf_cli."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise SalesforceCliError(
            f"Salesforce CLI no respondió en {timeout_seconds} segundos."
        ) from exc
    except OSError as exc:
        raise SalesforceCliError("No se pudo ejecutar Salesforce CLI.") from exc

    payload = _parse_json(completed.stdout)
    if completed.returncode != 0:
        # Avoid including raw stdout/stderr because either could contain credentials.
        raise SalesforceCliError(
            f"Salesforce CLI no pudo abrir la org '{target_org}'. "
            f"Reautenticá con 'sf org login web --alias {target_org}'."
        )
    if payload is None:
        raise SalesforceCliError("Salesforce CLI devolvió una respuesta JSON inválida.")

    result = payload.get("result")
    if not isinstance(result, dict):
        raise SalesforceCliError("Salesforce CLI no devolvió los datos de la org.")
    access_token = result.get("accessToken")
    instance_url = result.get("instanceUrl")
    username = result.get("username")
    if not isinstance(access_token, str) or not access_token:
        raise SalesforceCliError(
            "Salesforce CLI no devolvió un access token. Reautenticá la org y volvé a intentar."
        )
    if not isinstance(instance_url, str) or not instance_url.startswith("https://"):
        raise SalesforceCliError("Salesforce CLI no devolvió una instance URL HTTPS válida.")

    return SalesforceCliSession(
        access_token=access_token,
        instance_url=instance_url.rstrip("/"),
        username=username if isinstance(username, str) and username else None,
    )
