from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from sf_report_agent.callbacks.sqlite_update import mark_source_task_done_pending_reply
from sf_report_agent.config import Settings
from sf_report_agent.db.run_repository import ReportRunRepository
from sf_report_agent.db.task_reader import TaskReader
from sf_report_agent.graph.state import ReportAgentState
from sf_report_agent.llm.ollama_client import OllamaClient
from sf_report_agent.reports.exporters import export_report, write_run_metadata
from sf_report_agent.reports.quality_checks import run_quality_checks
from sf_report_agent.reports.transforms import records_to_dataframe
from sf_report_agent.salesforce.client import SalesforceClient
from sf_report_agent.salesforce.field_mapper import parse_salesforce_request
from sf_report_agent.salesforce.permissions_doctor import SalesforcePermissionsDoctor
from sf_report_agent.salesforce.schema import SchemaResolver, build_report_plan
from sf_report_agent.salesforce.soql_builder import SOQLBuilder
from sf_report_agent.salesforce.validators import validate_report_plan, validate_soql


@dataclass(slots=True)
class AgentServices:
    settings: Settings
    task_reader: TaskReader
    run_repository: ReportRunRepository
    salesforce_client: SalesforceClient | Any | None = None
    ollama_client: OllamaClient | None = None


class ReportGraphNodes:
    def __init__(self, services: AgentServices) -> None:
        self.services = services

    def load_task(self, state: ReportAgentState) -> ReportAgentState:
        task = state.get("task") or self.services.task_reader.get_task(state["task_id"])
        if (task.category or "").casefold() != "salesforce":
            raise ValueError(f"La tarea {task.id} no tiene category=salesforce")
        return {"task": task, "status": "task_loaded"}

    def parse_request(self, state: ReportAgentState) -> ReportAgentState:
        request = parse_salesforce_request(state["task"], llm=self.services.ollama_client)
        return {"request": request, "status": "request_parsed"}

    def resolve_salesforce_schema(self, state: ReportAgentState) -> ReportAgentState:
        client = None if state.get("dry_run") else self.services.salesforce_client
        snapshot = SchemaResolver(
            client, mapping_path=self.services.settings.field_mapping_path
        ).resolve()
        return {
            "schema_snapshot": snapshot,
            "warnings": list(state.get("warnings", [])) + list(snapshot.get("warnings", [])),
            "status": "schema_resolved",
        }

    def check_permissions(self, state: ReportAgentState) -> ReportAgentState:
        if state.get("dry_run"):
            return {"status": "permissions_skipped_dry_run"}
        client = self.services.salesforce_client
        if client is None:
            raise RuntimeError("La ejecución real requiere conexión Salesforce")
        doctor = SalesforcePermissionsDoctor(
            client, artifacts_dir=self.services.settings.artifacts_dir
        )
        report = doctor.run()
        permission_path = doctor.save(report)
        return {
            "permission_report": report,
            "artifacts": list(state.get("artifacts", [])) + [str(permission_path)],
            "warnings": list(state.get("warnings", [])) + report.warnings,
            "status": "permissions_checked",
        }

    def build_report_plan(self, state: ReportAgentState) -> ReportAgentState:
        plan = build_report_plan(state["request"], state["schema_snapshot"])
        return {
            "report_plan": plan,
            "warnings": list(state.get("warnings", [])) + plan.warnings,
            "status": "plan_built",
        }

    def validate_plan(self, state: ReportAgentState) -> ReportAgentState:
        errors = validate_report_plan(state["report_plan"])
        if state["report_plan"].needs_clarification:
            errors.extend(state["report_plan"].clarification_questions)
        if errors:
            raise ValueError("Plan inválido: " + " ".join(errors))
        return {"status": "plan_validated"}

    def build_soql(self, state: ReportAgentState) -> ReportAgentState:
        soql = SOQLBuilder(max_rows=self.services.settings.max_export_rows).build(
            state["report_plan"], state["request"], dry_run=state.get("dry_run", False)
        )
        return {"soql": soql, "status": "soql_built"}

    def validate_soql(self, state: ReportAgentState) -> ReportAgentState:
        validate_soql(state["soql"], max_rows=self.services.settings.max_export_rows)
        return {"status": "soql_validated"}

    def execute_query(self, state: ReportAgentState) -> ReportAgentState:
        if state.get("dry_run"):
            return {
                "raw_records": [],
                "warnings": list(state.get("warnings", []))
                + ["Dry-run: no se ejecutó ninguna consulta contra Salesforce."],
                "status": "query_skipped_dry_run",
            }
        client = self.services.salesforce_client
        if client is None:
            raise RuntimeError("La ejecución real requiere conexión Salesforce")
        records = client.query_all(state["soql"])
        if len(records) > self.services.settings.max_export_rows:
            raise RuntimeError("Salesforce devolvió más filas que MAX_EXPORT_ROWS")
        return {"raw_records": records, "status": "query_executed"}

    def transform_dataset(self, state: ReportAgentState) -> ReportAgentState:
        dataframe = records_to_dataframe(state.get("raw_records", []))
        if dataframe.empty:
            dataframe = pd.DataFrame(columns=state["report_plan"].selected_fields)
        return {
            "dataframe_records": dataframe.to_dict(orient="records"),
            "dataframe_columns": [str(column) for column in dataframe.columns],
            "status": "dataset_transformed",
        }

    @staticmethod
    def _dataframe(state: ReportAgentState) -> pd.DataFrame:
        return pd.DataFrame(
            state.get("dataframe_records", []), columns=state.get("dataframe_columns", [])
        )

    def quality_checks(self, state: ReportAgentState) -> ReportAgentState:
        quality = run_quality_checks(
            self._dataframe(state), request=state["request"], plan=state["report_plan"]
        )
        return {
            "quality_report": quality,
            "warnings": list(state.get("warnings", [])) + quality["warnings"],
            "status": "quality_checked",
        }

    def export_report(self, state: ReportAgentState) -> ReportAgentState:
        generated_at = datetime.now(UTC)
        settings = self.services.settings
        metadata = self._metadata(state, generated_at)
        report_paths = export_report(
            self._dataframe(state),
            task_id=state["task_id"],
            title=state["report_plan"].title,
            artifacts_dir=settings.artifacts_dir,
            output_formats=state["request"].output_formats,
            metadata=metadata,
            warnings=state.get("warnings", []),
            generated_at=generated_at,
        )
        run_path = write_run_metadata(
            task_id=state["task_id"],
            artifacts_dir=settings.artifacts_dir,
            metadata=metadata,
            generated_at=generated_at,
        )
        all_paths = list(state.get("artifacts", [])) + [str(path) for path in report_paths] + [
            str(run_path)
        ]
        csv_path = next((str(path) for path in report_paths if path.suffix == ".csv"), "")
        return {
            "artifacts": all_paths,
            "dataframe_path": csv_path,
            "status": "report_exported",
        }

    def _metadata(self, state: ReportAgentState, generated_at: datetime) -> dict[str, Any]:
        client = self.services.salesforce_client
        mapping = state["schema_snapshot"].get("field_mapping", {})
        return {
            "task_id": state["task_id"],
            "generated_at": generated_at.isoformat(),
            "salesforce_username": getattr(client, "username", None),
            "instance_url": getattr(client, "instance_url", None),
            "soql": state["soql"],
            "row_count": state["quality_report"]["row_count"],
            "campaign_ids": state["request"].campaign_ids,
            "report_title": state["report_plan"].title,
            "warnings": state.get("warnings", []),
            "field_mapping_used": mapping,
            "quality_checks": state["quality_report"],
            "dry_run": state.get("dry_run", False),
        }

    def compose_response(self, state: ReportAgentState) -> ReportAgentState:
        quality = state["quality_report"]
        artifacts = [Path(path) for path in state.get("artifacts", [])]
        csv_path = next((path for path in artifacts if path.suffix == ".csv"), None)
        xlsx_path = next((path for path in artifacts if path.suffix == ".xlsx"), None)
        warnings = list(dict.fromkeys(state.get("warnings", [])))
        lines = [
            "Listo. Armé el informe de altas 2026 para las campañas solicitadas.",
            "Incluye:",
            "- datos personales: nombre y apellido, fecha de nacimiento/edad y residencia;",
            "- datos de donación: fecha establecida, estado, monto, fecha de finalización y campaña.",
            "Resultado:",
            f"- filas exportadas: {quality['row_count']}",
            "- campañas encontradas: " + (", ".join(quality["campaigns_found"]) or "ninguna"),
            f"- archivo CSV: {csv_path or 'no generado'}",
            f"- archivo XLSX: {xlsx_path or 'no generado'}",
        ]
        if warnings:
            lines.append("Advertencias:")
            lines.extend(f"- {warning}" for warning in warnings)
        if self.services.settings.require_human_approval_for_pii:
            lines.append("El envío requiere aprobación humana porque el informe puede contener PII.")
        if state.get("dry_run"):
            lines.insert(0, "Dry-run completado; Salesforce no fue consultado.")
        return {"response_text": "\n".join(lines), "status": "response_composed"}

    def persist_result(self, state: ReportAgentState) -> ReportAgentState:
        dry_run = state.get("dry_run", False)
        status = "dry_run_completed" if dry_run else "done_pending_approval"
        if not dry_run and not self.services.settings.require_human_approval_for_pii:
            status = "done_pending_reply"
        repository = self.services.run_repository
        repository.finish_run(
            state["run_id"],
            status=status,
            request=state["request"],
            plan=state["report_plan"],
            permission_report=state.get("permission_report"),
            soql=state["soql"],
            row_count=state["quality_report"]["row_count"],
            response_text=state["response_text"],
        )
        for raw_path in state.get("artifacts", []):
            path = Path(raw_path)
            repository.add_artifact(
                state["run_id"], state["task_id"], path.suffix.lstrip(".") or "artifact", path
            )
        warnings = list(state.get("warnings", []))
        if not dry_run and self.services.settings.update_source_task:
            try:
                mark_source_task_done_pending_reply(
                    self.services.settings.source_db_path, state["task_id"]
                )
            except Exception as exc:
                warnings.append(f"El reporte terminó, pero no se actualizó la tarea fuente: {exc}")
        return {"status": status, "warnings": warnings}

