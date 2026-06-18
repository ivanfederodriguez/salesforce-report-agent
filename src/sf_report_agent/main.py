from __future__ import annotations

import argparse
import json
import re
import secrets
import sys
import webbrowser
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol, TypedDict

from rich.console import Console
from rich.table import Table

from sf_report_agent.config import Settings
from sf_report_agent.db.run_repository import ReportRunRepository
from sf_report_agent.db.task_reader import SourceDatabaseError, TaskReader
from sf_report_agent.graph.app import ReportAgentRunner
from sf_report_agent.graph.nodes import AgentServices
from sf_report_agent.llm.ollama_client import OllamaClient, OllamaError
from sf_report_agent.logging_config import configure_logging
from sf_report_agent.salesforce.client import SalesforceClient, SalesforceClientError
from sf_report_agent.salesforce.oauth import (
    OAuthCallbackReceiver,
    SalesforceOAuthError,
    SalesforceOAuthToken,
    build_authorization_url,
    exchange_authorization_code,
    load_token_file,
    refresh_access_token,
    save_token_file,
)
from sf_report_agent.salesforce.permissions_doctor import SalesforcePermissionsDoctor
from sf_report_agent.salesforce.sf_cli import SalesforceCliError, load_salesforce_cli_session

console = Console()


class SalesforceDescriber(Protocol):
    def describe_object(self, object_name: str) -> dict[str, Any]: ...


class SchemaField(TypedDict):
    label: str
    name: str
    type: str
    referenceTo: list[str]


def _ollama(settings: Settings) -> OllamaClient:
    return OllamaClient(
        base_url=settings.ollama_base_url,
        model=settings.ollama_model,
        temperature=settings.ollama_temperature,
    )


def _salesforce(settings: Settings) -> SalesforceClient:
    if settings.salesforce_auth_mode == "password":
        if not settings.has_salesforce_password_credentials:
            raise SalesforceClientError(
                "Faltan SALESFORCE_USERNAME, SALESFORCE_PASSWORD o SALESFORCE_SECURITY_TOKEN"
            )
        return SalesforceClient.from_password(
            username=settings.salesforce_username or "",
            password=settings.salesforce_password or "",
            security_token=settings.salesforce_security_token or "",
            domain=settings.salesforce_domain,
        )
    if settings.salesforce_auth_mode == "sf_cli":
        session = load_salesforce_cli_session(settings.salesforce_cli_alias or "")
        return SalesforceClient.from_session(
            instance_url=session.instance_url,
            access_token=session.access_token,
            username=session.username,
        )
    token = _refresh_oauth_session(settings)
    return SalesforceClient.from_session(
        instance_url=token.instance_url,
        access_token=token.access_token,
        username=settings.salesforce_username,
    )


def _oauth_material(
    settings: Settings,
) -> tuple[SalesforceOAuthToken | None, str | None, str | None]:
    stored_token = (
        None
        if settings.salesforce_refresh_token
        else load_token_file(settings.salesforce_token_path)
    )
    refresh_token = settings.salesforce_refresh_token or (
        stored_token.refresh_token if stored_token else None
    )
    instance_url = settings.salesforce_instance_url or (
        stored_token.instance_url if stored_token else None
    )
    return stored_token, refresh_token, instance_url


def _refresh_oauth_session(settings: Settings) -> SalesforceOAuthToken:
    _, refresh_token, instance_url = _oauth_material(settings)
    if not refresh_token:
        raise SalesforceClientError(
            "No hay refresh token. Ejecutá python -m sf_report_agent.main sf-oauth-login."
        )
    if not settings.has_salesforce_oauth_client_credentials:
        raise SalesforceClientError(
            "Faltan SALESFORCE_CLIENT_ID o SALESFORCE_CLIENT_SECRET para OAuth."
        )
    token = refresh_access_token(
        domain=settings.salesforce_domain,
        client_id=settings.salesforce_client_id or "",
        client_secret=settings.salesforce_client_secret or "",
        refresh_token=refresh_token,
        instance_url=instance_url,
    )
    save_token_file(token, settings.salesforce_token_path)
    return token


def _services(settings: Settings, *, dry_run: bool) -> AgentServices:
    return AgentServices(
        settings=settings,
        task_reader=TaskReader(settings.source_db_path),
        run_repository=ReportRunRepository(settings.worker_db_path),
        salesforce_client=None if dry_run else _salesforce(settings),
        ollama_client=_ollama(settings),
    )


def command_doctor(settings: Settings) -> int:
    checks: list[tuple[str, bool, str]] = []
    try:
        TaskReader(settings.source_db_path).list_tasks(limit=1)
        checks.append(("SQLite fuente", True, str(settings.source_db_path)))
    except (SourceDatabaseError, ValueError) as exc:
        checks.append(("SQLite fuente", False, str(exc)))

    try:
        ReportRunRepository(settings.worker_db_path).initialize()
        checks.append(("SQLite worker", True, str(settings.worker_db_path)))
    except OSError as exc:
        checks.append(("SQLite worker", False, str(exc)))

    try:
        settings.artifacts_dir.mkdir(parents=True, exist_ok=True)
        probe = settings.artifacts_dir / ".write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        checks.append(("Artifacts", True, str(settings.artifacts_dir)))
    except OSError as exc:
        checks.append(("Artifacts", False, str(exc)))

    try:
        tags = _ollama(settings).health()
        installed = {str(item.get("name") or item.get("model")) for item in tags.get("models", [])}
        model_ok = settings.ollama_model in installed
        detail = (
            settings.ollama_model
            if model_ok
            else f"Ollama responde, pero no se encontró el modelo {settings.ollama_model}"
        )
        checks.append(("Ollama/modelo", model_ok, detail))
    except OllamaError as exc:
        checks.append(("Ollama/modelo", False, str(exc)))

    table = Table(title="Doctor local")
    table.add_column("Check")
    table.add_column("Estado")
    table.add_column("Detalle")
    for name, ok, detail in checks:
        table.add_row(name, "[green]OK[/green]" if ok else "[red]ERROR[/red]", detail)
    console.print(table)
    return 0 if all(ok for _, ok, _ in checks) else 1


def command_sf_doctor(settings: Settings) -> int:
    client = _salesforce(settings)
    auth_table = Table(title="Autenticación Salesforce")
    auth_table.add_column("Modo")
    auth_table.add_column("Instance URL")
    auth_table.add_column("Refresh token usado")
    auth_table.add_row(
        settings.salesforce_auth_mode,
        client.instance_url or "desconocida",
        "sí" if settings.salesforce_auth_mode == "oauth" else "no",
    )
    console.print(auth_table)
    doctor = SalesforcePermissionsDoctor(client, artifacts_dir=settings.artifacts_dir)
    report = doctor.run()
    path = doctor.save(report)
    doctor.print_report(report, console)
    console.print("Login OK: " + ("sí" if report.login_ok else "no"))
    console.print("API OK: " + ("sí" if report.api_ok else "no"))
    console.print(f"Reporte guardado en: {path}")
    return 0 if report.login_ok and report.api_ok and report.describe_global_ok else 1


def command_sf_oauth_login(settings: Settings) -> int:
    if settings.salesforce_auth_mode != "oauth":
        raise SalesforceOAuthError("sf-oauth-login requiere SALESFORCE_AUTH_MODE=oauth.")
    if not settings.has_salesforce_oauth_client_credentials:
        raise SalesforceOAuthError(
            "Faltan SALESFORCE_CLIENT_ID o SALESFORCE_CLIENT_SECRET para OAuth."
        )
    state = secrets.token_urlsafe(32)
    authorization_url = build_authorization_url(
        domain=settings.salesforce_domain,
        client_id=settings.salesforce_client_id or "",
        redirect_uri=settings.salesforce_redirect_uri,
        state=state,
    )
    receiver = OAuthCallbackReceiver(
        redirect_uri=settings.salesforce_redirect_uri,
        expected_state=state,
    )
    console.print("Abrí esta URL para autenticar Salesforce con MFA:")
    console.print(authorization_url, markup=False)
    try:
        browser_opened = webbrowser.open(authorization_url)
    except webbrowser.Error:
        browser_opened = False
    if browser_opened:
        console.print("Se abrió el navegador. Esperando el callback local…")
    else:
        console.print("No se pudo abrir el navegador automáticamente; usá la URL impresa.")
    code = receiver.wait_for_code()
    token = exchange_authorization_code(
        domain=settings.salesforce_domain,
        client_id=settings.salesforce_client_id or "",
        client_secret=settings.salesforce_client_secret or "",
        redirect_uri=settings.salesforce_redirect_uri,
        code=code,
    )
    save_token_file(token, settings.salesforce_token_path)
    console.print(f"Instance URL: {token.instance_url}")
    console.print(f"Token guardado en: {settings.salesforce_token_path}")
    console.print("Refresh token presente: " + ("sí" if token.refresh_token else "no"))
    return 0 if token.refresh_token else 1


def command_sf_auth_status(settings: Settings, *, output: Console | None = None) -> int:
    target = output or console
    token_file_exists = settings.salesforce_token_path.exists()
    refresh_present = False
    instance_url = settings.salesforce_instance_url
    refresh_status = "no aplica"
    ok = False

    cli_status = "no aplica"
    if settings.salesforce_auth_mode == "password":
        ok = settings.has_salesforce_password_credentials
        refresh_status = "no aplica (password)"
    elif settings.salesforce_auth_mode == "sf_cli":
        refresh_status = "no aplica (sf_cli)"
        try:
            session = load_salesforce_cli_session(settings.salesforce_cli_alias or "")
            instance_url = session.instance_url
            cli_status = "sí"
            ok = True
        except SalesforceCliError as exc:
            cli_status = f"no: {exc}"
    else:
        _, refresh_token, stored_instance_url = _oauth_material(settings)
        refresh_present = bool(refresh_token)
        instance_url = instance_url or stored_instance_url
        if not refresh_token:
            refresh_status = "no: falta refresh token"
        elif not settings.has_salesforce_oauth_client_credentials:
            refresh_status = "no: faltan client ID/secret"
        else:
            try:
                token = _refresh_oauth_session(settings)
                instance_url = token.instance_url
                refresh_status = "sí"
                ok = True
            except (SalesforceOAuthError, SalesforceClientError) as exc:
                refresh_status = f"no: {exc}"

    token_file_exists = settings.salesforce_token_path.exists()
    table = Table(title="Estado de autenticación Salesforce")
    table.add_column("Dato")
    table.add_column("Valor")
    table.add_row("Auth mode", settings.salesforce_auth_mode)
    table.add_row("Token file existe", "sí" if token_file_exists else "no")
    table.add_row("Refresh token presente", "sí" if refresh_present else "no")
    table.add_row("Instance URL", instance_url or "desconocida")
    table.add_row("Puede refrescar access token", refresh_status)
    if settings.salesforce_auth_mode == "sf_cli":
        table.add_row("CLI target org", settings.salesforce_cli_alias or "no configurada")
        table.add_row("Sesión Salesforce CLI válida", cli_status)
    target.print(table)
    return 0 if ok else 1


def command_inspect_schema(
    settings: Settings,
    *,
    object_name: str,
    filter_text: str | None = None,
    client: SalesforceDescriber | None = None,
) -> int:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", object_name):
        raise ValueError(f"Nombre de objeto Salesforce inválido: {object_name!r}")
    describer = client or _salesforce(settings)
    description = describer.describe_object(object_name)
    visible_fields: list[SchemaField] = [
        {
            "label": str(field.get("label") or field.get("name") or ""),
            "name": str(field.get("name") or ""),
            "type": str(field.get("type") or ""),
            "referenceTo": [str(value) for value in (field.get("referenceTo") or [])],
        }
        for field in (description.get("fields") or [])
        if isinstance(field, dict)
        and field.get("name")
        and field.get("accessible", True) is not False
    ]
    needle = (filter_text or "").casefold().strip()
    matching_fields = [
        field
        for field in visible_fields
        if not needle
        or needle
        in " ".join(
            [
                field["label"],
                field["name"],
                field["type"],
                *field["referenceTo"],
            ]
        ).casefold()
    ]

    generated_at = datetime.now(UTC)
    schema_dir = settings.artifacts_dir / "schema"
    schema_dir.mkdir(parents=True, exist_ok=True)
    stamp = generated_at.strftime("%Y%m%dT%H%M%SZ")
    path = schema_dir / f"{object_name}_describe_{stamp}.json"
    payload = {
        "object": object_name,
        "generated_at": generated_at.isoformat(),
        "filter": filter_text,
        "visible_field_count": len(visible_fields),
        "matched_field_count": len(matching_fields),
        "fields": matching_fields,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    table = Table(title=f"Schema visible: {object_name}")
    table.add_column("Label")
    table.add_column("API name")
    table.add_column("Type")
    table.add_column("referenceTo")
    for field in matching_fields:
        table.add_row(
            field["label"],
            field["name"],
            field["type"],
            ", ".join(field["referenceTo"]),
        )
    console.print(table)
    console.print(f"Describe guardado en: {path}")
    return 0


def command_list_tasks(settings: Settings, *, limit: int) -> int:
    tasks = TaskReader(settings.source_db_path).list_tasks(limit=limit)
    table = Table(title=f"Tareas ({len(tasks)})")
    for heading in ("ID", "Categoría", "Prioridad", "Estado", "Remitente", "Acción"):
        table.add_column(heading)
    for task in tasks:
        action = task.public_request_text or task.requested_action or ""
        table.add_row(
            str(task.id),
            task.category or "",
            task.priority or "",
            task.status or "",
            task.sender_label or "",
            action[:100],
        )
    console.print(table)
    return 0


def _run_task(settings: Settings, task_id: int, *, dry_run: bool) -> int:
    result = ReportAgentRunner(_services(settings, dry_run=dry_run)).run(task_id, dry_run=dry_run)
    console.print(result.response_text)
    console.print("\n[bold]SOQL y auditoría:[/bold]")
    console.print_json(json.dumps(result.model_dump(mode="json"), ensure_ascii=False))
    return 0


def command_run_once(settings: Settings, *, dry_run: bool) -> int:
    task = TaskReader(settings.source_db_path).next_salesforce_task()
    if task is None:
        console.print("No hay tareas Salesforce pendientes.")
        return 0
    return _run_task(settings, task.id, dry_run=dry_run)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sf-report-agent")
    parser.add_argument("--env-file", type=Path, default=None)
    parser.add_argument("--verbose", action="store_true")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("doctor", help="Valida entorno local, SQLite y Ollama")
    subparsers.add_parser("sf-doctor", help="Diagnostica permisos read-only de Salesforce")
    subparsers.add_parser(
        "sf-oauth-login", help="Autoriza Salesforce con MFA y guarda un refresh token"
    )
    subparsers.add_parser(
        "sf-auth-status", help="Muestra el estado de autenticación sin revelar secretos"
    )
    inspect_parser = subparsers.add_parser(
        "inspect-schema", help="Lista campos visibles de un objeto Salesforce"
    )
    inspect_parser.add_argument("--object", dest="object_name", required=True)
    inspect_parser.add_argument("--filter", dest="filter_text", default=None)
    list_parser = subparsers.add_parser("list-tasks", help="Lista tareas de la SQLite fuente")
    list_parser.add_argument("--limit", type=int, default=20)
    run_parser = subparsers.add_parser("run-task", help="Ejecuta una tarea por ID")
    run_parser.add_argument("--task-id", type=int, required=True)
    run_parser.add_argument("--dry-run", action="store_true")
    once_parser = subparsers.add_parser("run-once", help="Ejecuta la próxima tarea Salesforce")
    once_parser.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    configure_logging(verbose=args.verbose)
    try:
        settings = Settings.from_env(args.env_file)
        if args.command == "doctor":
            return command_doctor(settings)
        if args.command == "sf-doctor":
            return command_sf_doctor(settings)
        if args.command == "sf-oauth-login":
            return command_sf_oauth_login(settings)
        if args.command == "sf-auth-status":
            return command_sf_auth_status(settings)
        if args.command == "inspect-schema":
            return command_inspect_schema(
                settings,
                object_name=args.object_name,
                filter_text=args.filter_text,
            )
        if args.command == "list-tasks":
            return command_list_tasks(settings, limit=args.limit)
        if args.command == "run-task":
            return _run_task(settings, args.task_id, dry_run=args.dry_run)
        if args.command == "run-once":
            return command_run_once(settings, dry_run=args.dry_run)
    except (
        SourceDatabaseError,
        SalesforceClientError,
        SalesforceOAuthError,
        SalesforceCliError,
        OllamaError,
        ValueError,
        RuntimeError,
    ) as exc:
        console.print(f"[red]Error:[/red] {exc}")
        return 1
    parser.error("Comando desconocido")
    return 2


if __name__ == "__main__":
    sys.exit(main())
