from __future__ import annotations

from typing import Any, TypedDict

from sf_report_agent.models.permissions import SalesforcePermissionReport
from sf_report_agent.models.report_plan import SalesforceReportPlan
from sf_report_agent.models.report_request import SalesforceReportRequest
from sf_report_agent.models.task import ExternalTask


class ReportAgentState(TypedDict, total=False):
    task_id: int
    task: ExternalTask
    request: SalesforceReportRequest
    permission_report: SalesforcePermissionReport
    schema_snapshot: dict[str, Any]
    report_plan: SalesforceReportPlan
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

