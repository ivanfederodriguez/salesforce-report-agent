from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

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
from sf_report_agent.salesforce.permissions_doctor import SalesforcePermissionsDoctor

console = Console()


def _ollama(settings: Settings) -> OllamaClient:
    return OllamaClient(
        base_url=settings.ollama_base_url,
        model=settings.ollama_model,
        temperature=settings.ollama_temperature,
    )


def _salesforce(settings: Settings) -> SalesforceClient:
    if not settings.has_salesforce_credentials:
        raise SalesforceClientError(
            "Faltan SALESFORCE_USERNAME, SALESFORCE_PASSWORD o SALESFORCE_SECURITY_TOKEN"
        )
    return SalesforceClient(
        username=settings.salesforce_username or "",
        password=settings.salesforce_password or "",
        security_token=settings.salesforce_security_token or "",
        domain=settings.salesforce_domain,
    )


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
        installed = {
            str(item.get("name") or item.get("model")) for item in tags.get("models", [])
        }
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
    doctor = SalesforcePermissionsDoctor(client, artifacts_dir=settings.artifacts_dir)
    report = doctor.run()
    path = doctor.save(report)
    doctor.print_report(report, console)
    console.print(f"Reporte guardado en: {path}")
    return 0 if report.login_ok and report.api_ok and report.describe_global_ok else 1


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
    result = ReportAgentRunner(_services(settings, dry_run=dry_run)).run(
        task_id, dry_run=dry_run
    )
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
        if args.command == "list-tasks":
            return command_list_tasks(settings, limit=args.limit)
        if args.command == "run-task":
            return _run_task(settings, args.task_id, dry_run=args.dry_run)
        if args.command == "run-once":
            return command_run_once(settings, dry_run=args.dry_run)
    except (SourceDatabaseError, SalesforceClientError, OllamaError, ValueError, RuntimeError) as exc:
        console.print(f"[red]Error:[/red] {exc}")
        return 1
    parser.error("Comando desconocido")
    return 2


if __name__ == "__main__":
    sys.exit(main())

