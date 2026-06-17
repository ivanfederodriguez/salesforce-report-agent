from __future__ import annotations

import json
import os
import re
import tempfile
import time
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import requests

DEFAULT_SCOPES = ("api", "refresh_token", "offline_access")
DEFAULT_TIMEOUT_SECONDS = 30


class SalesforceOAuthError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class SalesforceOAuthToken:
    access_token: str
    refresh_token: str | None
    instance_url: str
    issued_at: str
    token_type: str = "Bearer"

    @classmethod
    def from_payload(
        cls,
        payload: dict[str, Any],
        *,
        fallback_refresh_token: str | None = None,
        fallback_instance_url: str | None = None,
    ) -> SalesforceOAuthToken:
        access_token = payload.get("access_token")
        instance_url = payload.get("instance_url") or fallback_instance_url
        if not isinstance(access_token, str) or not access_token:
            raise SalesforceOAuthError("Salesforce no devolvió un access token válido.")
        if not isinstance(instance_url, str) or not instance_url.startswith("https://"):
            raise SalesforceOAuthError("Salesforce no devolvió una instance_url HTTPS válida.")
        refresh_value = payload.get("refresh_token") or fallback_refresh_token
        refresh_token = str(refresh_value) if refresh_value else None
        issued_at = str(payload.get("issued_at") or datetime.now(UTC).isoformat())
        token_type = str(payload.get("token_type") or "Bearer")
        return cls(
            access_token=access_token,
            refresh_token=refresh_token,
            instance_url=instance_url.rstrip("/"),
            issued_at=issued_at,
            token_type=token_type,
        )

    def as_dict(self) -> dict[str, str | None]:
        return {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "instance_url": self.instance_url,
            "issued_at": self.issued_at,
            "token_type": self.token_type,
        }


def sanitize_secrets(message: str, *secret_values: str | None) -> str:
    sanitized = message
    for value in secret_values:
        if value:
            sanitized = sanitized.replace(value, "[REDACTED]")
    return re.sub(
        r'(?i)("?(?:access_token|refresh_token|client_secret|password)"?\s*[:=]\s*)'
        r'("[^"]*"|[^\s,&}]+)',
        r"\1[REDACTED]",
        sanitized,
    )


def _oauth_host(domain: str) -> str:
    normalized = domain.strip().rstrip("/")
    if not normalized:
        raise SalesforceOAuthError("SALESFORCE_DOMAIN no puede estar vacío.")
    if normalized in {"login", "test"}:
        return f"https://{normalized}.salesforce.com"
    if normalized.startswith("http://"):
        raise SalesforceOAuthError("El dominio OAuth de Salesforce debe usar HTTPS.")
    if normalized.startswith("https://"):
        return normalized
    if "." in normalized:
        return f"https://{normalized}"
    return f"https://{normalized}.my.salesforce.com"


def build_authorization_url(
    *,
    domain: str,
    client_id: str,
    redirect_uri: str,
    state: str,
    scopes: tuple[str, ...] = DEFAULT_SCOPES,
) -> str:
    query = urlencode(
        {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "scope": " ".join(scopes),
            "state": state,
        }
    )
    return f"{_oauth_host(domain)}/services/oauth2/authorize?{query}"


def _token_request(
    *,
    domain: str,
    form: dict[str, str],
    secrets: tuple[str | None, ...],
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    endpoint = f"{_oauth_host(domain)}/services/oauth2/token"
    try:
        response = requests.post(endpoint, data=form, timeout=timeout)
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        message = sanitize_secrets(str(exc), *secrets)
        raise SalesforceOAuthError(f"Falló la solicitud OAuth a Salesforce: {message}") from exc
    if not isinstance(payload, dict):
        raise SalesforceOAuthError("Salesforce devolvió una respuesta OAuth inválida.")
    if response.status_code >= 400 or payload.get("error"):
        error = str(payload.get("error") or f"HTTP {response.status_code}")
        description = str(payload.get("error_description") or "sin detalle")
        detail = sanitize_secrets(f"{error}: {description}", *secrets)
        raise SalesforceOAuthError(f"Salesforce rechazó la autenticación OAuth: {detail}")
    return payload


def exchange_authorization_code(
    *,
    domain: str,
    client_id: str,
    client_secret: str,
    redirect_uri: str,
    code: str,
) -> SalesforceOAuthToken:
    payload = _token_request(
        domain=domain,
        form={
            "grant_type": "authorization_code",
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "code": code,
        },
        secrets=(client_secret, code),
    )
    return SalesforceOAuthToken.from_payload(payload)


def refresh_access_token(
    *,
    domain: str,
    client_id: str,
    client_secret: str,
    refresh_token: str,
    instance_url: str | None = None,
) -> SalesforceOAuthToken:
    payload = _token_request(
        domain=domain,
        form={
            "grant_type": "refresh_token",
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
        },
        secrets=(client_secret, refresh_token),
    )
    return SalesforceOAuthToken.from_payload(
        payload,
        fallback_refresh_token=refresh_token,
        fallback_instance_url=instance_url,
    )


def save_token_file(token: SalesforceOAuthToken, path: Path) -> None:
    temporary_path: Path | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            delete=False,
        ) as temporary:
            json.dump(token.as_dict(), temporary, ensure_ascii=False, indent=2)
            temporary.write("\n")
            temporary_path = Path(temporary.name)
        os.chmod(temporary_path, 0o600)
        temporary_path.replace(path)
        os.chmod(path, 0o600)
    except OSError as exc:
        raise SalesforceOAuthError(f"No se pudo guardar el token file {path}: {exc}") from exc
    finally:
        if temporary_path and temporary_path.exists():
            with suppress(OSError):
                temporary_path.unlink()


def load_token_file(path: Path) -> SalesforceOAuthToken | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SalesforceOAuthError(f"No se pudo leer el token file {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise SalesforceOAuthError(f"El token file {path} no contiene un objeto JSON.")
    return SalesforceOAuthToken.from_payload(payload)


class OAuthCallbackReceiver:
    def __init__(self, *, redirect_uri: str, expected_state: str) -> None:
        parsed = urlparse(redirect_uri)
        if parsed.scheme != "http" or parsed.hostname not in {"localhost", "127.0.0.1"}:
            raise SalesforceOAuthError(
                "SALESFORCE_REDIRECT_URI debe usar http://localhost o http://127.0.0.1."
            )
        if parsed.port is None:
            raise SalesforceOAuthError("SALESFORCE_REDIRECT_URI debe incluir un puerto local.")
        self.callback_path = parsed.path or "/callback"
        self.expected_state = expected_state
        self.code: str | None = None
        self.error: str | None = None
        receiver = self

        class CallbackHandler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                receiver._handle_callback(self)

            def log_message(self, format: str, *args: object) -> None:
                return

        try:
            self.server = HTTPServer((parsed.hostname, parsed.port), CallbackHandler)
        except OSError as exc:
            raise SalesforceOAuthError(
                f"No se pudo iniciar el callback OAuth en {parsed.hostname}:{parsed.port}: {exc}"
            ) from exc

    def _handle_callback(self, handler: BaseHTTPRequestHandler) -> None:
        parsed = urlparse(handler.path)
        if parsed.path != self.callback_path:
            self._respond(handler, 404, "Ruta OAuth no encontrada.")
            return
        query = parse_qs(parsed.query)
        returned_state = query.get("state", [""])[0]
        if returned_state != self.expected_state:
            self.error = "Salesforce devolvió un state OAuth inválido."
            self._respond(handler, 400, self.error)
            return
        oauth_error = query.get("error", [""])[0]
        if oauth_error:
            description = query.get("error_description", [oauth_error])[0]
            self.error = sanitize_secrets(description)
            self._respond(
                handler, 400, "Salesforce rechazó la autorización. Podés cerrar esta ventana."
            )
            return
        code = query.get("code", [""])[0]
        if not code:
            self.error = "El callback OAuth no incluyó un código de autorización."
            self._respond(handler, 400, self.error)
            return
        self.code = code
        self._respond(
            handler,
            200,
            "Autenticación Salesforce completada. Ya podés cerrar esta ventana.",
        )

    @staticmethod
    def _respond(handler: BaseHTTPRequestHandler, status: int, message: str) -> None:
        body = message.encode("utf-8")
        handler.send_response(status)
        handler.send_header("Content-Type", "text/plain; charset=utf-8")
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)

    def wait_for_code(self, *, timeout_seconds: int = 300) -> str:
        deadline = time.monotonic() + timeout_seconds
        try:
            while self.code is None and self.error is None and time.monotonic() < deadline:
                self.server.timeout = min(1.0, max(0.0, deadline - time.monotonic()))
                self.server.handle_request()
        finally:
            self.server.server_close()
        if self.error:
            raise SalesforceOAuthError(self.error)
        if self.code is None:
            raise SalesforceOAuthError("Se agotó el tiempo esperando el callback OAuth.")
        return self.code
