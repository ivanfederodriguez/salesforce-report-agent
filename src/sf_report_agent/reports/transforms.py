from __future__ import annotations

from typing import Any

import pandas as pd


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

