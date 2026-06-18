from __future__ import annotations

from datetime import date
from typing import Any

import pandas as pd

from sf_report_agent.models.report_plan import DerivedFieldPlan


def _flatten(value: Any, *, prefix: str = "") -> dict[str, Any]:
    flattened: dict[str, Any] = {}
    if not isinstance(value, dict):
        return flattened
    for key, item in value.items():
        if key == "attributes":
            continue
        target = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(item, dict):
            flattened.update(_flatten(item, prefix=target))
        else:
            flattened[target] = item
    return flattened


def records_to_dataframe(records: list[dict[str, Any]]) -> pd.DataFrame:
    return pd.DataFrame([_flatten(record) for record in records])


def apply_derived_fields(
    dataframe: pd.DataFrame,
    fields: list[DerivedFieldPlan],
    *,
    as_of: date | None = None,
) -> pd.DataFrame:
    result = dataframe.copy()
    today = pd.Timestamp(as_of or date.today())
    for field in fields:
        if field.kind == "age_years":
            source = field.source_fields[0] if field.source_fields else ""
            values = result[source] if source in result.columns else pd.Series(pd.NaT, index=result.index)
            parsed = pd.to_datetime(values, errors="coerce")
            result[field.output_field] = ((today - parsed).dt.days // 365).astype("Int64")
        elif field.kind == "first_non_empty":
            values = pd.Series(pd.NA, index=result.index, dtype="object")
            for source in field.source_fields:
                if source not in result.columns:
                    continue
                candidate = result[source].replace(r"^\s*$", pd.NA, regex=True)
                values = values.fillna(candidate)
            result[field.output_field] = values
    return result
