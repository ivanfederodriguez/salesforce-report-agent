from pathlib import Path

import openpyxl

from conftest import FakeSalesforceClient, create_source_database
from sf_report_agent.config import Settings
from sf_report_agent.db.run_repository import ReportRunRepository
from sf_report_agent.db.task_reader import TaskReader
from sf_report_agent.graph.app import ReportAgentRunner
from sf_report_agent.graph.nodes import AgentServices
from sf_report_agent.models.task import ExternalTask


def _settings(tmp_path: Path, source_db: Path) -> Settings:
    return Settings(
        source_db_path=source_db,
        worker_db_path=tmp_path / "worker.db",
        artifacts_dir=tmp_path / "artifacts",
        field_mapping_path=None,
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
    )


def test_full_graph_with_mock_salesforce_generates_artifacts(
    tmp_path: Path,
    micaela_task: ExternalTask,
    fake_salesforce: FakeSalesforceClient,
) -> None:
    source_db = tmp_path / "source.db"
    create_source_database(source_db, micaela_task)
    settings = _settings(tmp_path, source_db)
    services = AgentServices(
        settings=settings,
        task_reader=TaskReader(source_db),
        run_repository=ReportRunRepository(settings.worker_db_path),
        salesforce_client=fake_salesforce,
        ollama_client=None,
    )

    result = ReportAgentRunner(services).run(123)

    assert result.status == "done_pending_approval"
    assert result.row_count == 2
    assert result.soql.startswith("SELECT ")
    assert fake_salesforce.queried_soql
    csv_path = next(Path(path) for path in result.artifacts if path.endswith(".csv"))
    xlsx_path = next(Path(path) for path in result.artifacts if path.endswith(".xlsx"))
    metadata_path = next(
        Path(path)
        for path in result.artifacts
        if "/runs/" in path and path.endswith(".json")
    )
    assert csv_path.exists()
    assert xlsx_path.exists()
    assert metadata_path.exists()
    assert openpyxl.load_workbook(xlsx_path).sheetnames == ["datos", "metadata", "warnings"]
    assert "requiere aprobación humana" in result.response_text


def test_dry_run_never_queries_salesforce(
    tmp_path: Path,
    micaela_task: ExternalTask,
    fake_salesforce: FakeSalesforceClient,
) -> None:
    source_db = tmp_path / "source.db"
    create_source_database(source_db, micaela_task)
    settings = _settings(tmp_path, source_db)
    services = AgentServices(
        settings=settings,
        task_reader=TaskReader(source_db),
        run_repository=ReportRunRepository(settings.worker_db_path),
        salesforce_client=fake_salesforce,
        ollama_client=None,
    )

    result = ReportAgentRunner(services).run(123, dry_run=True)

    assert result.status == "dry_run_completed"
    assert fake_salesforce.queried_soql == []
    assert "Salesforce no fue consultado" in result.response_text
