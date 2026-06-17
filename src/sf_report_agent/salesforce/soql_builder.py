from __future__ import annotations

import re

from sf_report_agent.models.report_plan import SalesforceReportPlan
from sf_report_agent.models.report_request import SalesforceReportRequest
from sf_report_agent.salesforce.field_mapper import CAMPAIGN_ID_RE
from sf_report_agent.salesforce.validators import validate_soql

API_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_.]*")


class SOQLBuilder:
    def __init__(self, *, max_rows: int) -> None:
        self.max_rows = max_rows

    @staticmethod
    def _api_name(value: str) -> str:
        if not API_NAME_RE.fullmatch(value):
            raise ValueError(f"Nombre de API Salesforce inválido: {value!r}")
        return value

    @staticmethod
    def _campaign_id(value: str) -> str:
        match = CAMPAIGN_ID_RE.fullmatch(value)
        if not match:
            raise ValueError(f"Campaign ID inválido: {value!r}")
        return value

    def build(
        self,
        plan: SalesforceReportPlan,
        request: SalesforceReportRequest,
        *,
        dry_run: bool,
    ) -> str:
        primary_object = self._api_name(plan.primary_object)
        fields = list(dict.fromkeys(self._api_name(field) for field in plan.selected_fields))
        if "Id" not in fields:
            fields.insert(0, "Id")

        campaign_ids = [self._campaign_id(value) for value in request.campaign_ids]
        if not campaign_ids:
            raise ValueError("No hay Campaign IDs válidos para construir SOQL")
        campaign_field = "CampaignId"
        if primary_object == "CampaignMember":
            campaign_field = "CampaignId"
        quoted_ids = ", ".join(f"'{value}'" for value in campaign_ids)
        filters = [f"{campaign_field} IN ({quoted_ids})"]

        if request.year is not None:
            date_field = "CreatedDate" if primary_object == "CampaignMember" else "CloseDate"
            filters.append(f"{date_field} >= {request.year}-01-01")
            filters.append(f"{date_field} < {request.year + 1}-01-01")

        limit = min(200, self.max_rows) if dry_run else self.max_rows
        soql = (
            f"SELECT {', '.join(fields)} FROM {primary_object} "
            f"WHERE {' AND '.join(filters)} LIMIT {limit}"
        )
        validate_soql(soql, max_rows=self.max_rows)
        return soql

