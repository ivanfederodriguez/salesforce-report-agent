from __future__ import annotations

import json
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
from sf_report_agent.salesforce.label_resolver import SalesforceLabelResolver
from sf_report_agent.salesforce.permissions_doctor import SalesforcePermissionsDoctor
from sf_report_agent.salesforce.schema import SchemaResolver, build_report_plan_bundle
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
        mapping_dir = self.services.settings.artifacts_dir / "schema"
        mapping_dir.mkdir(parents=True, exist_ok=True)
        mapping_path = mapping_dir / "field_mapping.json"
        mapping_path.write_text(
            json.dumps(snapshot.get("field_mapping", {}), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return {
            "schema_snapshot": snapshot,
            "artifacts": [*state.get("artifacts", []), str(mapping_path)],
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
            "artifacts": [*state.get("artifacts", []), str(permission_path)],
            "warnings": list(state.get("warnings", [])) + report.warnings,
            "status": "permissions_checked",
        }

    def build_report_plan(self, state: ReportAgentState) -> ReportAgentState:
        bundle = build_report_plan_bundle(
            state["request"],
            state["schema_snapshot"],
            allow_report_without_person_fields=(
                self.services.settings.allow_report_without_person_fields
            ),
        )
        primary_plan = bundle.plans[0]
        return {
            "report_plan": primary_plan,
            "plan_bundle": bundle,
            "warnings": list(state.get("warnings", [])) + bundle.warnings,
            "status": "plan_built",
        }

    def validate_plan(self, state: ReportAgentState) -> ReportAgentState:
        bundle = state["plan_bundle"]
        if bundle.needs_clarification:
            return {"status": "plan_needs_clarification"}
        errors = [
            f"{plan.variant_label}: {error}"
            for plan in bundle.plans
            for error in validate_report_plan(plan)
        ]
        if errors:
            raise ValueError("Plan inválido: " + " ".join(errors))
        return {"status": "plan_validated"}

    def build_soql(self, state: ReportAgentState) -> ReportAgentState:
        builder = SOQLBuilder(max_rows=self.services.settings.max_export_rows)
        queries = []
        for plan in state["plan_bundle"].plans:
            soql = builder.build(plan, state["request"], dry_run=state.get("dry_run", False))
            queries.append(
                {
                    "variant_id": plan.variant_id,
                    "variant_label": plan.variant_label,
                    "interpretation": plan.ambiguity_reason,
                    "plan": plan,
                    "soql": soql,
                }
            )
        return {
            "variant_queries": queries,
            "soql": str(queries[0]["soql"]) if queries else "",
            "status": "soql_built",
        }

    def validate_soql(self, state: ReportAgentState) -> ReportAgentState:
        for query in state["variant_queries"]:
            validate_soql(str(query["soql"]), max_rows=self.services.settings.max_export_rows)
        return {"status": "soql_validated"}

    def execute_query(self, state: ReportAgentState) -> ReportAgentState:
        if state.get("dry_run"):
            return {
                "raw_records": [],
                "variant_records": [
                    {**query, "records": []} for query in state["variant_queries"]
                ],
                "warnings": [
                    *state.get("warnings", []),
                    "Dry-run: no se ejecutó ninguna consulta contra Salesforce.",
                ],
                "status": "query_skipped_dry_run",
            }
        client = self.services.salesforce_client
        if client is None:
            raise RuntimeError("La ejecución real requiere conexión Salesforce")
        variant_records = []
        for query in state["variant_queries"]:
            records = client.query_all(str(query["soql"]))
            if len(records) > self.services.settings.max_export_rows:
                raise RuntimeError(
                    f"Salesforce devolvió más filas que MAX_EXPORT_ROWS para "
                    f"{query['variant_label']}"
                )
            variant_records.append({**query, "records": records})
        return {
            "raw_records": variant_records[0]["records"] if variant_records else [],
            "variant_records": variant_records,
            "status": "query_executed",
        }

    def transform_dataset(self, state: ReportAgentState) -> ReportAgentState:
        datasets = []
        for variant in state["variant_records"]:
            plan = variant["plan"]
            dataframe = records_to_dataframe(variant.get("records", []))
            if dataframe.empty:
                dataframe = pd.DataFrame(columns=plan.selected_fields)
            else:
                dataframe = dataframe.reindex(columns=plan.selected_fields)
            records = [
                {str(key): value for key, value in record.items()}
                for record in dataframe.to_dict(orient="records")
            ]
            datasets.append(
                {
                    **{key: value for key, value in variant.items() if key != "records"},
                    "dataframe_records": records,
                    "dataframe_columns": [str(column) for column in dataframe.columns],
                }
            )
        first = datasets[0] if datasets else {}
        return {
            "variant_datasets": datasets,
            "dataframe_records": first.get("dataframe_records", []),
            "dataframe_columns": first.get("dataframe_columns", []),
            "status": "dataset_transformed",
        }

    @staticmethod
    def _dataframe(state: ReportAgentState) -> pd.DataFrame:
        return pd.DataFrame(
            state.get("dataframe_records", []), columns=state.get("dataframe_columns", [])
        )

    @staticmethod
    def _variant_dataframe(variant: dict[str, Any]) -> pd.DataFrame:
        return pd.DataFrame(
            variant.get("dataframe_records", []),
            columns=variant.get("dataframe_columns", []),
        )

    def quality_checks(self, state: ReportAgentState) -> ReportAgentState:
        variant_quality = []
        prefixed_warnings: list[str] = []
        for variant in state["variant_datasets"]:
            quality = run_quality_checks(
                self._variant_dataframe(variant),
                request=state["request"],
                plan=variant["plan"],
            )
            variant_quality.append({**variant, "quality_report": quality})
            prefixed_warnings.extend(
                f"[{variant['variant_label']}] {warning}"
                for warning in quality["warnings"]
            )
        preferred = next(
            (item for item in variant_quality if item["variant_id"] == "combined"),
            variant_quality[0] if variant_quality else None,
        )
        global_quality = dict(preferred["quality_report"]) if preferred else {"row_count": 0}
        global_quality["variants"] = [
            {
                "variant_id": item["variant_id"],
                "variant_label": item["variant_label"],
                "row_count": item["quality_report"]["row_count"],
            }
            for item in variant_quality
        ]
        return {
            "variant_quality_reports": variant_quality,
            "quality_report": global_quality,
            "warnings": list(state.get("warnings", [])) + prefixed_warnings,
            "status": "quality_checked",
        }

    def export_report(self, state: ReportAgentState) -> ReportAgentState:
        generated_at = datetime.now(UTC)
        settings = self.services.settings
        label_resolver = SalesforceLabelResolver(state["schema_snapshot"])
        generated_paths: list[str] = []
        variant_results: list[dict[str, Any]] = []
        export_warnings: list[str] = []
        for variant in state["variant_quality_reports"]:
            plan = variant["plan"]
            dataframe = self._variant_dataframe(variant)
            api_name_to_label = label_resolver.resolve(
                plan.primary_object, [str(column) for column in dataframe.columns]
            )
            export_dataframe = dataframe.rename(columns=api_name_to_label)
            metadata = self._metadata(
                state,
                variant=variant,
                generated_at=generated_at,
                api_name_to_label=api_name_to_label,
            )
            variant_warnings = list(variant["quality_report"]["warnings"])
            report_paths = export_report(
                export_dataframe,
                task_id=state["task_id"],
                title=f"{plan.title} - {plan.variant_label}",
                artifacts_dir=settings.artifacts_dir,
                output_formats=state["request"].output_formats,
                metadata=metadata,
                warnings=variant_warnings,
                generated_at=generated_at,
            )
            run_path = write_run_metadata(
                task_id=state["task_id"],
                artifacts_dir=settings.artifacts_dir,
                metadata=metadata,
                variant_id=plan.variant_id,
                generated_at=generated_at,
            )
            variant_paths = [str(path) for path in report_paths] + [str(run_path)]
            generated_paths.extend(variant_paths)
            creation_warnings = self._try_create_salesforce_report(
                plan, str(variant["soql"])
            )
            variant_warnings.extend(creation_warnings)
            variant_results.append(
                {
                    "variant_id": plan.variant_id,
                    "variant_label": plan.variant_label,
                    "interpretation": plan.ambiguity_reason,
                    "soql": variant["soql"],
                    "row_count": variant["quality_report"]["row_count"],
                    "artifacts": variant_paths,
                    "warnings": variant_warnings,
                    "api_name_to_label": api_name_to_label,
                }
            )
            export_warnings.extend(creation_warnings)

        all_paths = list(state.get("artifacts", [])) + generated_paths
        csv_path = next((path for path in generated_paths if path.endswith(".csv")), "")
        return {
            "artifacts": all_paths,
            "variant_results": variant_results,
            "dataframe_path": csv_path,
            "warnings": list(state.get("warnings", [])) + export_warnings,
            "status": "report_exported",
        }

    def _metadata(
        self,
        state: ReportAgentState,
        *,
        variant: dict[str, Any],
        generated_at: datetime,
        api_name_to_label: dict[str, str],
    ) -> dict[str, Any]:
        client = self.services.salesforce_client
        mapping = state["schema_snapshot"].get("field_mapping", {})
        plan = variant["plan"]
        return {
            "task_id": state["task_id"],
            "variant_id": plan.variant_id,
            "variant_label": plan.variant_label,
            "interpretation": plan.ambiguity_reason,
            "generated_at": generated_at.isoformat(),
            "salesforce_username": getattr(client, "username", None),
            "instance_url": getattr(client, "instance_url", None),
            "soql": variant["soql"],
            "row_count": variant["quality_report"]["row_count"],
            "campaign_ids": state["request"].campaign_ids,
            "campaign_names": state["request"].campaign_names,
            "origin_sources": state["request"].origin_sources,
            "report_title": plan.title,
            "api_name_to_label": api_name_to_label,
            "warnings": variant["quality_report"]["warnings"],
            "field_mapping_used": mapping,
            "quality_checks": variant["quality_report"],
            "dry_run": state.get("dry_run", False),
        }

    def _try_create_salesforce_report(self, plan: Any, soql: str) -> list[str]:
        if not self.services.settings.allow_salesforce_report_create:
            return []
        client = self.services.salesforce_client
        creator = getattr(client, "create_report", None)
        if not callable(creator):
            return [
                f"[{plan.variant_label}] No se creó un reporte en Salesforce: "
                "la API de creación no está implementada por el cliente configurado."
            ]
        try:
            creator(plan=plan, soql=soql)
        except Exception as exc:
            return [
                f"[{plan.variant_label}] No se pudo crear el reporte opcional en Salesforce: "
                f"{exc}. Los archivos locales se conservaron."
            ]
        return []

    def compose_response(self, state: ReportAgentState) -> ReportAgentState:
        request = state["request"]
        plan = state["report_plan"]
        warnings = list(dict.fromkeys(state.get("warnings", [])))
        variant_results = list(state.get("variant_results", []))
        if not variant_results:
            quality = state["quality_report"]
            variant_results = [
                {
                    "variant_id": plan.variant_id,
                    "variant_label": plan.variant_label or plan.title,
                    "interpretation": plan.ambiguity_reason,
                    "row_count": quality["row_count"],
                    "artifacts": list(state.get("artifacts", [])),
                    "warnings": quality.get("warnings", []),
                    "api_name_to_label": {
                        str(value): str(value) for value in quality.get("columns", [])
                    },
                }
            ]
        variant_prefixes = tuple(
            f"[{result['variant_label']}]" for result in variant_results
        )
        warnings = [
            warning
            for warning in warnings
            if not variant_prefixes or not warning.startswith(variant_prefixes)
        ]

        if request.report_type == "altas_por_campaña":
            report_name = "informe de altas"
        else:
            report_name = "informe de " + request.report_type.replace("_", " ").strip()
        if request.year:
            report_name += f" {request.year}"

        lines = [
            f"Listo. Armé el {report_name}.",
            f"Plan ejecutado: {plan.title}.",
        ]
        bundle = state.get("plan_bundle")
        if bundle and bundle.ambiguity_note:
            lines.append(
                "No estaba seguro de qué interpretación de campaña correspondía, "
                "así que generé todas las variantes read-only seguras."
            )
            lines.append(bundle.ambiguity_note)
        campaign_scope = request.campaign_names or request.campaign_ids
        if campaign_scope:
            lines.append("Campañas solicitadas: " + ", ".join(campaign_scope) + ".")
        if request.origin_sources:
            lines.append("Fuentes de origen: " + ", ".join(request.origin_sources) + ".")
        lines.append("Variantes generadas:")
        for index, result in enumerate(variant_results):
            letter = chr(ord("A") + index) if index < 26 else str(index + 1)
            lines.append(
                f"{letter}. {result['variant_label']}: {result['row_count']} filas."
            )
            if result.get("interpretation"):
                lines.append(f"   Interpretación: {result['interpretation']}")
            result_artifacts = [Path(path) for path in result.get("artifacts", [])]
            if result_artifacts:
                lines.extend(
                    f"   - {path.suffix.lstrip('.') or 'archivo'}: {path}"
                    for path in result_artifacts
                )
            else:
                lines.append("   - sin archivos generados")
            for warning in result.get("warnings", []):
                lines.append(f"   - advertencia: {warning}")
        if warnings:
            lines.append("Advertencias generales:")
            lines.extend(f"- {warning}" for warning in warnings)
        if self.services.settings.require_human_approval_for_pii:
            lines.append(
                "No envié automáticamente porque el informe puede contener PII y requiere "
                "aprobación humana."
            )
        if state.get("dry_run"):
            lines.insert(0, "Dry-run completado; Salesforce no fue consultado.")
        return {"response_text": "\n".join(lines), "status": "response_composed"}

    def compose_clarification_response(self, state: ReportAgentState) -> ReportAgentState:
        request = state["request"]
        plan = state["report_plan"]
        bundle = state.get("plan_bundle")
        questions = list(
            dict.fromkeys(
                bundle.clarification_questions
                if bundle is not None
                else plan.clarification_questions
            )
        )
        warnings = list(dict.fromkeys(state.get("warnings", [])))
        lines = [
            f'Necesito una aclaración antes de ejecutar el reporte "{plan.title}".',
            "No ejecuté la consulta del reporte ni generé una exportación.",
        ]
        scope = request.campaign_names or request.campaign_ids
        if scope:
            lines.append("Campañas identificadas: " + ", ".join(scope) + ".")
        if request.origin_sources:
            lines.append("Fuentes identificadas: " + ", ".join(request.origin_sources) + ".")
        lines.append("Preguntas para Iván:")
        lines.extend(f"- {question}" for question in questions)
        if warnings:
            lines.append("Advertencias de schema/mapping:")
            lines.extend(f"- {warning}" for warning in warnings)
        return {
            "response_text": "\n".join(lines),
            "status": "clarification_response_composed",
        }

    def persist_result(self, state: ReportAgentState) -> ReportAgentState:
        dry_run = state.get("dry_run", False)
        bundle = state.get("plan_bundle")
        needs_clarification = (
            bundle.needs_clarification
            if bundle is not None
            else state["report_plan"].needs_clarification
        )
        if needs_clarification:
            status = "needs_clarification"
        elif dry_run:
            status = "dry_run_completed"
        else:
            status = "done_pending_approval"
        if (
            status == "done_pending_approval"
            and not self.services.settings.require_human_approval_for_pii
        ):
            status = "done_pending_reply"
        repository = self.services.run_repository
        repository.finish_run(
            state["run_id"],
            status=status,
            request=state["request"],
            plan=bundle or state["report_plan"],
            permission_report=state.get("permission_report"),
            soql=state.get("soql"),
            row_count=state.get("quality_report", {}).get("row_count"),
            response_text=state["response_text"],
            warnings=list(state.get("warnings", [])),
        )
        for raw_path in state.get("artifacts", []):
            path = Path(raw_path)
            repository.add_artifact(
                state["run_id"], state["task_id"], path.suffix.lstrip(".") or "artifact", path
            )
        for variant in state.get("variant_results", []):
            repository.add_variant_result(
                state["run_id"],
                state["task_id"],
                variant_id=str(variant["variant_id"]),
                variant_label=str(variant["variant_label"]),
                interpretation=(
                    str(variant["interpretation"])
                    if variant.get("interpretation")
                    else None
                ),
                soql=str(variant["soql"]),
                row_count=int(variant["row_count"]),
                artifacts=[str(path) for path in variant.get("artifacts", [])],
                warnings=[str(value) for value in variant.get("warnings", [])],
            )
        warnings = list(state.get("warnings", []))
        if (
            status in {"done_pending_approval", "done_pending_reply"}
            and self.services.settings.update_source_task
        ):
            try:
                mark_source_task_done_pending_reply(
                    self.services.settings.source_db_path, state["task_id"]
                )
            except Exception as exc:
                warnings.append(f"El reporte terminó, pero no se actualizó la tarea fuente: {exc}")
        return {"status": status, "warnings": warnings}
