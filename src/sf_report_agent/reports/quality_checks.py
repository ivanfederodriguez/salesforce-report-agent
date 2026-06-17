from __future__ import annotations

from typing import Any

import pandas as pd

from sf_report_agent.models.report_plan import SalesforceReportPlan
from sf_report_agent.models.report_request import SalesforceReportRequest


def run_quality_checks(
    dataframe: pd.DataFrame,
    *,
    request: SalesforceReportRequest,
    plan: SalesforceReportPlan,
) -> dict[str, Any]:
    warnings: list[str] = []
    columns = set(dataframe.columns)
    missing_fields = [field for field in plan.selected_fields if field not in columns]
    if missing_fields and not dataframe.empty:
        warnings.append("Campos seleccionados ausentes: " + ", ".join(missing_fields))

    null_percentages = {
        str(column): round(float(dataframe[column].isna().mean() * 100), 2)
        for column in dataframe.columns
    }
    campaign_column = next(
        (name for name in ("CampaignId", "Campaign.Id") if name in columns), None
    )
    found_campaigns = (
        sorted(str(value) for value in dataframe[campaign_column].dropna().unique())
        if campaign_column
        else []
    )
    missing_campaigns = sorted(set(request.campaign_ids) - set(found_campaigns))
    if missing_campaigns:
        warnings.append("Campañas sin filas: " + ", ".join(missing_campaigns))

    date_column = next((name for name in ("CloseDate", "CreatedDate") if name in columns), None)
    dates_outside_period = 0
    if date_column and request.year and not dataframe.empty:
        parsed = pd.to_datetime(dataframe[date_column], errors="coerce", utc=True)
        dates_outside_period = int((parsed.dropna().dt.year != request.year).sum())
        if dates_outside_period:
            warnings.append(f"Hay {dates_outside_period} fechas fuera de {request.year}.")

    duplicates = int(dataframe.duplicated(subset=["Id"]).sum()) if "Id" in columns else 0
    if duplicates:
        warnings.append(f"Hay {duplicates} registros duplicados por Id.")
    if request.person_fields and not plan.joins_or_relationships:
        warnings.append("No se incluyeron campos personales por falta de una relación validada.")

    return {
        "row_count": len(dataframe),
        "columns": list(dataframe.columns),
        "missing_fields": missing_fields,
        "null_percentages": null_percentages,
        "campaigns_requested": request.campaign_ids,
        "campaigns_found": found_campaigns,
        "campaigns_missing": missing_campaigns,
        "dates_outside_period": dates_outside_period,
        "duplicate_ids": duplicates,
        "warnings": warnings,
    }
