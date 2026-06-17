from pathlib import Path

from sf_report_agent.config import Settings
from sf_report_agent.db.run_repository import ReportRunRepository
from sf_report_agent.db.task_reader import TaskReader
from sf_report_agent.graph.nodes import AgentServices, ReportGraphNodes
from sf_report_agent.models.report_plan import SalesforceReportPlan
from sf_report_agent.models.report_request import SalesforceReportRequest


def test_compose_response_uses_dynamic_year_campaigns_and_artifacts(tmp_path: Path) -> None:
    settings = Settings(
        source_db_path=tmp_path / "source.db",
        worker_db_path=tmp_path / "worker.db",
        artifacts_dir=tmp_path / "artifacts",
        field_mapping_path=None,
        model_provider="ollama",
        ollama_model="test",
        ollama_base_url="http://127.0.0.1:11434",
        ollama_temperature=0,
        salesforce_username=None,
        salesforce_password=None,
        salesforce_security_token=None,
        salesforce_domain="login",
        sf_read_only=True,
        max_export_rows=100,
        require_human_approval_for_pii=True,
        log_pii=False,
        update_source_task=False,
        allow_report_without_person_fields=False,
    )
    nodes = ReportGraphNodes(
        AgentServices(
            settings=settings,
            task_reader=TaskReader(settings.source_db_path),
            run_repository=ReportRunRepository(settings.worker_db_path),
        )
    )
    request = SalesforceReportRequest(
        task_id=44,
        report_type="altas_por_campaña",
        year=2024,
        campaign_ids=["7011W000001buEh"],
        campaign_names=["Campaña Horizonte"],
        origin_sources=["referidos"],
    )
    plan = SalesforceReportPlan(
        task_id=44,
        title="Altas 2024 por campaña",
        description="Reporte dinámico",
        primary_object="Opportunity",
        selected_fields=["Id", "CampaignId", "LeadSource"],
        filters=[],
        campaign_ids=request.campaign_ids,
        origin_sources=request.origin_sources,
        origin_source_field="LeadSource",
    )

    result = nodes.compose_response(
        {
            "request": request,
            "report_plan": plan,
            "quality_report": {
                "row_count": 7,
                "campaigns_found": ["7011W000001buEh"],
                "columns": ["Id", "CampaignId", "LeadSource"],
            },
            "artifacts": [str(tmp_path / "altas_2024.csv")],
            "warnings": ["Advertencia de prueba"],
        }
    )

    response = result["response_text"]
    assert "informe de altas 2024" in response
    assert "Altas 2024 por campaña" in response
    assert "Campaña Horizonte" in response
    assert "referidos" in response
    assert "altas_2024.csv" in response
    assert "2026" not in response
