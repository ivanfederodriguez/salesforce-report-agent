from __future__ import annotations

from typing import Any, TypedDict

from sf_report_agent.models.permissions import SalesforcePermissionReport
from sf_report_agent.models.report_plan import SalesforceReportPlan, SalesforceReportPlanBundle
from sf_report_agent.models.report_request import SalesforceReportRequest
from sf_report_agent.models.task import ExternalTask
from sf_report_agent.salesforce.business_semantics import BusinessSemantics


class ReportAgentState(TypedDict, total=False):
    task_id: int
    task: ExternalTask
    request: SalesforceReportRequest
    permission_report: SalesforcePermissionReport
    schema_snapshot: dict[str, Any]
    business_semantics: BusinessSemantics
    report_plan: SalesforceReportPlan
    plan_bundle: SalesforceReportPlanBundle
    soql: str
    raw_records: list[dict[str, Any]]
    dataframe_path: str
    artifacts: list[str]
    response_text: str
    errors: list[str]
    warnings: list[str]
    status: str
    dry_run: bool
    run_id: int
    dataframe_records: list[dict[str, Any]]
    dataframe_columns: list[str]
    quality_report: dict[str, Any]
    variant_queries: list[dict[str, Any]]
    variant_records: list[dict[str, Any]]
    variant_datasets: list[dict[str, Any]]
    variant_quality_reports: list[dict[str, Any]]
    variant_results: list[dict[str, Any]]
