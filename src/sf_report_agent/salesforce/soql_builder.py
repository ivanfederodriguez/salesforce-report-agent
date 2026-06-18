from __future__ import annotations

import re

from sf_report_agent.models.report_plan import SalesforceFilter, SalesforceReportPlan
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

    def _semantic_filter(self, item: SalesforceFilter) -> str:
        field = self._api_name(item.field)
        if item.operator == "not_null":
            return f"{field} != NULL"
        if not item.values:
            raise ValueError(f"El filtro {item.description!r} no tiene valores")
        if item.operator == "equals":
            return f"{field} = {self._literal(item.values[0])}"
        if item.operator == "in":
            values = ", ".join(self._literal(value) for value in item.values)
            return f"{field} IN ({values})"
        if item.operator == "contains":
            expressions = [
                f"{field} LIKE {self._literal('%' + value + '%')}"
                for value in item.values
            ]
            return expressions[0] if len(expressions) == 1 else "(" + " OR ".join(expressions) + ")"
        raise ValueError(f"Operador de filtro no soportado: {item.operator}")

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
        filters: list[str] = []
        if plan.scope_filters:
            filters.extend(self._semantic_filter(item) for item in plan.scope_filters)
        else:
            scope_filters: list[str] = []
            if campaign_ids:
                campaign_fields = [
                    self._api_name(value) for value in plan.campaign_filter_fields
                ]
                if not campaign_fields:
                    raise ValueError("Falta el mapping de campos de campaña")
                quoted_ids = ", ".join(f"'{value}'" for value in campaign_ids)
                campaign_filters = [
                    f"{campaign_field} IN ({quoted_ids})"
                    for campaign_field in campaign_fields
                ]
                scope_filters.append(
                    campaign_filters[0]
                    if len(campaign_filters) == 1
                    else "(" + " OR ".join(campaign_filters) + ")"
                )
            origin_covered_by_campaign_ids = bool(
                campaign_ids and plan.origin_sources_resolved_by_campaign_ids
            )
            if request.origin_sources and not origin_covered_by_campaign_ids:
                if not plan.origin_source_field:
                    raise ValueError("Falta el mapping del campo de campaña de origen/fuente")
                origin_field = self._api_name(plan.origin_source_field)
                quoted_sources = ", ".join(
                    self._literal(value) for value in request.origin_sources
                )
                scope_filters.append(f"{origin_field} IN ({quoted_sources})")
            if not scope_filters:
                raise ValueError("No hay campañas ni fuentes de origen para construir SOQL")
            filters.append(
                scope_filters[0]
                if len(scope_filters) == 1
                else "(" + " OR ".join(scope_filters) + ")"
            )

        if request.year is not None:
            if not plan.date_filter_field:
                raise ValueError("Falta el mapping del campo de fecha")
            date_field = self._api_name(plan.date_filter_field)
            if plan.date_filter_mode == "range":
                filters.append(f"{date_field} >= {request.year}-01-01")
                filters.append(f"{date_field} < {request.year + 1}-01-01")
            else:
                filters.append(f"CALENDAR_YEAR({date_field}) = {request.year}")
        elif request.date_from is not None or request.date_to is not None:
            if not plan.date_filter_field:
                raise ValueError("Falta el mapping del campo de fecha")
            date_field = self._api_name(plan.date_filter_field)
            if request.date_from is not None:
                filters.append(f"{date_field} >= {request.date_from.isoformat()}")
            if request.date_to is not None:
                filters.append(f"{date_field} <= {request.date_to.isoformat()}")

        limit = min(200, self.max_rows) if dry_run else self.max_rows
        soql = (
            f"SELECT {', '.join(fields)} FROM {primary_object} "
            f"WHERE {' AND '.join(filters)} LIMIT {limit}"
        )
        validate_soql(soql, max_rows=self.max_rows)
        return soql
