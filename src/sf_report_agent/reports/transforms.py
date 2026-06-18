from __future__ import annotations

import re
from datetime import date
from html.parser import HTMLParser
from typing import Any

import pandas as pd
from pandas.api.types import is_object_dtype, is_string_dtype

from sf_report_agent.models.report_plan import DerivedFieldPlan


class _HTMLTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def _plain_text(value: Any) -> Any:
    if not isinstance(value, str) or not re.search(r"</?[A-Za-z][^>]*>", value):
        return value
    parser = _HTMLTextExtractor()
    try:
        parser.feed(value)
        parser.close()
    except ValueError:
        return value
    return "".join(parser.parts).strip()


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


def clean_html_cells(dataframe: pd.DataFrame) -> pd.DataFrame:
    result = dataframe.copy()
    for column in result.columns:
        if not (
            is_object_dtype(result[column].dtype)
            or is_string_dtype(result[column].dtype)
        ):
            continue
        result[column] = result[column].map(_plain_text)
    return result


def apply_value_labels(
    dataframe: pd.DataFrame, value_labels: dict[str, dict[str, str]]
) -> pd.DataFrame:
    result = dataframe.copy()
    for field_name, labels in value_labels.items():
        if field_name not in result.columns:
            continue
        result[field_name] = result[field_name].map(
            lambda value, mapping=labels: (
                mapping.get(str(value), value) if not pd.isna(value) else value
            )
        )
    return result


def apply_output_order(dataframe: pd.DataFrame, output_order: list[str]) -> pd.DataFrame:
    configured = [field for field in output_order if field in dataframe.columns]
    remaining = [field for field in dataframe.columns if field not in configured]
    return dataframe.reindex(columns=[*configured, *remaining])


def prepare_export_dataframe(
    dataframe: pd.DataFrame,
    *,
    value_labels: dict[str, dict[str, str]],
    output_order: list[str],
    integer_fields: list[str] | None = None,
) -> pd.DataFrame:
    result = clean_html_cells(dataframe)
    result = apply_value_labels(result, value_labels)
    for field_name in integer_fields or []:
        if field_name in result.columns:
            result[field_name] = pd.to_numeric(
                result[field_name], errors="coerce"
            ).astype("Int64")
    return apply_output_order(result, output_order)


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
            strategy = field.strategy or "floor"
            if strategy == "calendar_age":
                before_birthday = (parsed.dt.month > today.month) | (
                    (parsed.dt.month == today.month) & (parsed.dt.day > today.day)
                )
                ages = today.year - parsed.dt.year - before_birthday.astype("Int64")
                result[field.output_field] = ages.astype("Int64")
            else:
                years = (today - parsed).dt.days / 365
                years = years.round() if strategy == "round" else years // 1
                result[field.output_field] = years.astype("Int64")
        elif field.kind == "first_non_empty":
            values = pd.Series(pd.NA, index=result.index, dtype="object")
            for source in field.source_fields:
                if source not in result.columns:
                    continue
                candidate = result[source].replace(r"^\s*$", pd.NA, regex=True)
                values = values.fillna(candidate)
            result[field.output_field] = values
    return result
