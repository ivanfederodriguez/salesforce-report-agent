from __future__ import annotations

import re

from sf_report_agent.models.report_plan import SalesforceReportPlan

FORBIDDEN_SOQL = re.compile(
    r"\b(UPDATE|DELETE|INSERT|UPSERT|MERGE|DROP|ALTER|TRUNCATE|UNDELETE)\b", re.IGNORECASE
)
LIMIT_RE = re.compile(r"\bLIMIT\s+(\d+)\b", re.IGNORECASE)


class UnsafeSOQLError(ValueError):
    pass


def validate_report_plan(plan: SalesforceReportPlan) -> list[str]:
    errors: list[str] = []
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", plan.primary_object):
        errors.append("Objeto principal inválido")
    if not plan.selected_fields:
        errors.append("El plan no tiene campos seleccionados")
    if not plan.campaign_ids:
        errors.append("El plan no tiene Campaign IDs")
    return errors


def validate_soql(soql: str, *, max_rows: int, require_limit: bool = True) -> None:
    normalized = soql.strip()
    if not normalized.upper().startswith("SELECT "):
        raise UnsafeSOQLError("La consulta debe comenzar con SELECT")
    if ";" in normalized or "--" in normalized or "/*" in normalized:
        raise UnsafeSOQLError("No se permiten múltiples sentencias ni comentarios SOQL")
    match = FORBIDDEN_SOQL.search(normalized)
    if match:
        raise UnsafeSOQLError(f"Operación no permitida en SOQL: {match.group(1).upper()}")
    limits = LIMIT_RE.findall(normalized)
    if require_limit and len(limits) != 1:
        raise UnsafeSOQLError("La consulta debe contener exactamente un LIMIT")
    if limits and int(limits[0]) > max_rows:
        raise UnsafeSOQLError(f"LIMIT excede MAX_EXPORT_ROWS={max_rows}")

