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

    @staticmethod
    def _literal(value: str) -> str:
        return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"

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
        campaign_field = "CampaignId"
        if primary_object == "CampaignMember":
            campaign_field = "CampaignId"
        scope_filters: list[str] = []
        if campaign_ids:
            quoted_ids = ", ".join(f"'{value}'" for value in campaign_ids)
            scope_filters.append(f"{campaign_field} IN ({quoted_ids})")
        if request.origin_sources:
            if not plan.origin_source_field:
                raise ValueError("Falta el mapping del campo de campaña de origen/fuente")
            origin_field = self._api_name(plan.origin_source_field)
            quoted_sources = ", ".join(self._literal(value) for value in request.origin_sources)
            scope_filters.append(f"{origin_field} IN ({quoted_sources})")
        if not scope_filters:
            raise ValueError("No hay campañas ni fuentes de origen para construir SOQL")
        scope = (
            scope_filters[0] if len(scope_filters) == 1 else "(" + " OR ".join(scope_filters) + ")"
        )
        filters = [scope]

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
