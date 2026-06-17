from __future__ import annotations

import json
import re
import unicodedata
from datetime import date
from typing import Any

from sf_report_agent.llm.ollama_client import OllamaClient, OllamaError
from sf_report_agent.llm.prompts import SALESFORCE_REQUEST_SYSTEM_PROMPT
from sf_report_agent.models.report_request import SalesforceReportRequest
from sf_report_agent.models.task import ExternalTask

CAMPAIGN_ID_RE = re.compile(r"\b701[a-zA-Z0-9]{12}(?:[a-zA-Z0-9]{3})?\b")
YEAR_RE = re.compile(r"\b(20\d{2})\b")
SLACK_LINK_LABEL_RE = re.compile(r"<[^>|]+\|([^>]+)>")

KNOWN_CAMPAIGNS = (
    "[IND] Campañas Pauta Digital",
    "[IND] Redes Sociales - Instagram",
    "[IND] Redes Sociales",
)
KNOWN_SOURCES = ("amplify", "orgánico web")

PERSON_FIELD_ALIASES = {
    "nombre_y_apellido": ("nombre y apellido", "nombre completo"),
    "fecha_nacimiento_o_edad": ("fecha de nacimiento", "nacimiento", "edad"),
    "lugar_de_residencia": ("lugar de residencia", "residencia", "domicilio"),
}
DONATION_FIELD_ALIASES = {
    "fecha_establecida": ("fecha establecida", "fecha de alta"),
    "estado": ("estado",),
    "monto": ("monto", "importe"),
    "fecha_de_finalizacion": ("fecha de finalización", "fecha de finalizacion"),
    "campaña": ("campaña", "campaign"),
}


def _fold(value: str) -> str:
    return "".join(
        char
        for char in unicodedata.normalize("NFKD", value.casefold())
        if not unicodedata.combining(char)
    )


def extract_campaign_ids(text: str) -> list[str]:
    return list(dict.fromkeys(CAMPAIGN_ID_RE.findall(text)))


def _task_text(task: ExternalTask) -> str:
    parts = [task.public_request_text or "", task.requested_action or ""]
    parts.extend(str(value) for value in task.classification_json.values() if value)
    for link in task.message_links:
        parts.extend(str(link.get(key) or "") for key in ("url", "title", "metadata_json"))
    return "\n".join(parts)


def _find_known_values(text: str, values: tuple[str, ...]) -> list[str]:
    folded = _fold(text)
    return [value for value in values if _fold(value) in folded]


def _detect_fields(text: str, aliases: dict[str, tuple[str, ...]]) -> list[str]:
    folded = _fold(text)
    return [key for key, terms in aliases.items() if any(_fold(term) in folded for term in terms)]


def parse_salesforce_request(
    task: ExternalTask,
    *,
    llm: OllamaClient | None = None,
) -> SalesforceReportRequest:
    text = _task_text(task)
    folded = _fold(text)
    campaign_ids = extract_campaign_ids(text)

    campaign_names = _find_known_values(text, KNOWN_CAMPAIGNS)
    origin_sources = _find_known_values(text, KNOWN_SOURCES)
    # Los labels de Slack complementan las reglas conocidas sin capturar URLs puras.
    for label in SLACK_LINK_LABEL_RE.findall(text):
        if label.startswith("[IND]") and label not in campaign_names:
            campaign_names.append(label)

    years = [int(value) for value in YEAR_RE.findall(text)]
    year = years[0] if years else None
    person_fields = _detect_fields(text, PERSON_FIELD_ALIASES)
    donation_fields = _detect_fields(text, DONATION_FIELD_ALIASES)
    report_type = "altas_por_campaña" if "alta" in folded and "campan" in folded else "reporte_salesforce"

    missing: list[str] = []
    if not campaign_ids and not campaign_names:
        missing.append("campañas")
    if year is None:
        missing.append("período")
    if not person_fields and not donation_fields:
        missing.append("campos requeridos")

    deterministic: dict[str, Any] = {
        "task_id": task.id,
        "report_type": report_type,
        "year": year,
        "campaign_ids": campaign_ids,
        "campaign_names": campaign_names,
        "origin_sources": origin_sources,
        "person_fields": person_fields,
        "donation_fields": donation_fields,
        "missing_information": missing,
    }
    if llm is not None and missing:
        prompt = (
            "Pedido original:\n"
            + text
            + "\n\nExtracción determinística que no debés contradecir:\n"
            + json.dumps(deterministic, ensure_ascii=False)
        )
        try:
            llm_data = llm.complete_json(system=SALESFORCE_REQUEST_SYSTEM_PROMPT, prompt=prompt)
        except OllamaError:
            llm_data = {}
        for key in ("report_type", "date_from", "date_to"):
            if not deterministic.get(key) and llm_data.get(key):
                deterministic[key] = llm_data[key]

    request = SalesforceReportRequest.model_validate(deterministic)
    if request.year is not None:
        request.date_from = request.date_from or date(request.year, 1, 1)
        request.date_to = request.date_to or date(request.year, 12, 31)
    return request

