import json
import sqlite3
from pathlib import Path

import openpyxl

from conftest import FakeSalesforceClient, create_source_database, write_field_mapping
from sf_report_agent.config import Settings
from sf_report_agent.db.run_repository import ReportRunRepository
from sf_report_agent.db.task_reader import TaskReader
from sf_report_agent.graph.app import ReportAgentRunner
from sf_report_agent.graph.nodes import AgentServices
from sf_report_agent.models.execution_result import ExecutionResult
from sf_report_agent.models.task import ExternalTask


def _settings(tmp_path: Path, source_db: Path, *, mapping_path: Path | None = None) -> Settings:
    return Settings(
        source_db_path=source_db,
        worker_db_path=tmp_path / "worker.db",
        artifacts_dir=tmp_path / "artifacts",
        field_mapping_path=mapping_path,
        model_provider="ollama",
        ollama_model="gemma4:e2b-mlx",
        ollama_base_url="http://127.0.0.1:11434",
        ollama_temperature=0,
        salesforce_username="user@example.org",
        salesforce_password="secret",
        salesforce_security_token="token",
        salesforce_domain="login",
        sf_read_only=True,
        max_export_rows=50_000,
        require_human_approval_for_pii=True,
        log_pii=False,
        update_source_task=False,
        allow_report_without_person_fields=False,
    )


def _run(
    tmp_path: Path,
    task: ExternalTask,
    fake_salesforce: FakeSalesforceClient,
    *,
    mapping_path: Path,
    dry_run: bool = False,
) -> tuple[ExecutionResult, Settings]:
    source_db = tmp_path / "source.db"
    create_source_database(source_db, task)
    settings = _settings(tmp_path, source_db, mapping_path=mapping_path)
    services = AgentServices(
        settings=settings,
        task_reader=TaskReader(source_db),
        run_repository=ReportRunRepository(settings.worker_db_path),
        salesforce_client=fake_salesforce,
        ollama_client=None,
    )
    return ReportAgentRunner(services).run(task.id, dry_run=dry_run), settings


def test_full_graph_with_mock_salesforce_generates_artifacts(
    tmp_path: Path,
    micaela_task: ExternalTask,
    fake_salesforce: FakeSalesforceClient,
) -> None:
    result, _ = _run(
        tmp_path,
        micaela_task,
        fake_salesforce,
        mapping_path=write_field_mapping(tmp_path / "mapping.json"),
    )

    assert result.status == "done_pending_approval"
    assert result.row_count == 2
    assert result.soql.startswith("SELECT ")
    assert "Contact.Name" in result.soql
    assert "LeadSource IN ('amplify', 'orgánico web')" in result.soql
    assert " OR " in result.soql
    assert fake_salesforce.queried_soql
    csv_path = next(Path(path) for path in result.artifacts if path.endswith(".csv"))
    xlsx_path = next(Path(path) for path in result.artifacts if path.endswith(".xlsx"))
    metadata_path = next(
        Path(path) for path in result.artifacts if "/runs/" in path and path.endswith(".json")
    )
    assert csv_path.exists()
    assert xlsx_path.exists()
    assert metadata_path.exists()
    assert openpyxl.load_workbook(xlsx_path).sheetnames == ["datos", "metadata", "warnings"]
    assert "requiere aprobación humana" in result.response_text
    assert "informe de altas 2026" in result.response_text
    assert "[IND] Campañas Pauta Digital" in result.response_text
    assert "amplify, orgánico web" in result.response_text


def test_dry_run_never_queries_salesforce(
    tmp_path: Path,
    micaela_task: ExternalTask,
    fake_salesforce: FakeSalesforceClient,
) -> None:
    result, _ = _run(
        tmp_path,
        micaela_task,
        fake_salesforce,
        mapping_path=write_field_mapping(tmp_path / "mapping.json"),
        dry_run=True,
    )

    assert result.status == "dry_run_completed"
    assert fake_salesforce.queried_soql == []
    assert "Salesforce no fue consultado" in result.response_text


def test_missing_person_relationship_finishes_needs_clarification_and_persists_context(
    tmp_path: Path,
    micaela_task: ExternalTask,
    fake_salesforce: FakeSalesforceClient,
) -> None:
    result, settings = _run(
        tmp_path,
        micaela_task,
        fake_salesforce,
        mapping_path=write_field_mapping(tmp_path / "mapping.json", include_relationship=False),
    )

    assert result.status == "needs_clarification"
    assert result.errors == []
    assert fake_salesforce.queried_soql == []
    assert "¿Qué relación de Opportunity conecta la donación con la persona" in result.response_text
    with sqlite3.connect(settings.worker_db_path) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute("SELECT * FROM report_runs ORDER BY id DESC LIMIT 1").fetchone()
    assert row is not None
    assert row["status"] == "needs_clarification"
    assert json.loads(row["request_json"])["person_fields"]
    assert json.loads(row["plan_json"])["needs_clarification"] is True
    assert json.loads(row["warnings_json"])
    assert "Preguntas para Iván" in row["response_text"]


def test_missing_origin_mapping_finishes_needs_clarification(
    tmp_path: Path,
    micaela_task: ExternalTask,
    fake_salesforce: FakeSalesforceClient,
) -> None:
    result, _ = _run(
        tmp_path,
        micaela_task,
        fake_salesforce,
        mapping_path=write_field_mapping(tmp_path / "mapping.json", include_origin=False),
    )

    assert result.status == "needs_clarification"
    assert fake_salesforce.queried_soql == []
    assert "¿Qué campo de Salesforce representa campaña de origen/fuente?" in result.response_text
