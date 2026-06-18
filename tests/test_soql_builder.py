import pytest

from sf_report_agent.models.report_plan import SalesforceReportPlan
from sf_report_agent.models.report_request import SalesforceReportRequest
from sf_report_agent.salesforce.soql_builder import SOQLBuilder
from sf_report_agent.salesforce.validators import UnsafeSOQLError, validate_soql


def _plan() -> SalesforceReportPlan:
    return SalesforceReportPlan(
        task_id=123,
        title="Altas 2026",
        description="Reporte",
        primary_object="Opportunity",
        selected_fields=["Id", "CloseDate", "Amount", "CampaignId"],
        filters=[],
        campaign_ids=["7011W000001buEh"],
        campaign_filter_fields=["CampaignId"],
        date_filter_field="CloseDate",
    )


def test_builds_safe_dry_run_soql() -> None:
    request = SalesforceReportRequest(
        task_id=123,
        report_type="altas_por_campaña",
        year=2026,
        campaign_ids=["7011W000001buEh"],
    )
    soql = SOQLBuilder(max_rows=50_000).build(_plan(), request, dry_run=True)

    assert soql.startswith("SELECT ")
    assert "FROM Opportunity" in soql
    assert "CampaignId IN ('7011W000001buEh')" in soql
    assert "CALENDAR_YEAR(CloseDate) = 2026" in soql
    assert soql.endswith("LIMIT 200")


def test_rejects_invalid_campaign_id() -> None:
    request = SalesforceReportRequest(
        task_id=123,
        report_type="test",
        campaign_ids=["x' OR Name != '"],
    )
    with pytest.raises(ValueError, match="Campaign ID inválido"):
        SOQLBuilder(max_rows=100).build(_plan(), request, dry_run=False)


def test_rejects_destructive_or_unbounded_queries() -> None:
    with pytest.raises(UnsafeSOQLError):
        validate_soql("DELETE FROM Opportunity", max_rows=100)
    with pytest.raises(UnsafeSOQLError, match="LIMIT"):
        validate_soql("SELECT Id FROM Opportunity", max_rows=100)
