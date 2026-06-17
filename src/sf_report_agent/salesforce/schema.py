from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sf_report_agent.models.report_plan import SalesforceReportPlan
from sf_report_agent.models.report_request import SalesforceReportRequest
from sf_report_agent.salesforce.client import SalesforceClient, SalesforceClientError

CANDIDATE_OBJECTS = (
    "Campaign",
    "CampaignMember",
    "Contact",
    "Account",
    "Opportunity",
    "npe03__Recurring_Donation__c",
    "Recurring_Donation__c",
    "npe01__OppPayment__c",
    "Payment",
)

DEFAULT_FIELD_MAPPING: dict[str, Any] = {
    "person": {
        "object": "Contact",
        "fields": {
            "nombre_y_apellido": "Name",
            "fecha_nacimiento_o_edad": "Birthdate",
            "lugar_de_residencia": ["MailingCity", "MailingState", "MailingCountry"],
        },
    },
    "donation": {
        "object": "Opportunity",
        "fields": {
            "fecha_establecida": "CloseDate",
            "estado": "StageName",
            "monto": "Amount",
            "fecha_de_finalizacion": None,
            "campaña": "CampaignId",
        },
    },
}


class SchemaResolver:
    def __init__(
        self,
        client: SalesforceClient | None,
        *,
        mapping_path: Path | None = None,
    ) -> None:
        self.client = client
        self.mapping_path = mapping_path

    def resolve(self) -> dict[str, Any]:
        mapping = self._load_mapping()
        snapshot: dict[str, Any] = {"objects": {}, "field_mapping": mapping, "warnings": []}
        if self.client is None:
            snapshot["offline"] = True
            snapshot["warnings"].append(
                "Dry-run sin Salesforce: se usa el mapping conservador local y debe validarse contra la org."
            )
            return snapshot

        global_description = self.client.describe_global()
        available = {
            str(item["name"])
            for item in global_description.get("sobjects", [])
            if isinstance(item, dict) and item.get("name")
        }
        for object_name in CANDIDATE_OBJECTS:
            if object_name not in available:
                continue
            try:
                description = self.client.describe_object(object_name)
            except SalesforceClientError as exc:
                snapshot["warnings"].append(str(exc))
                continue
            snapshot["objects"][object_name] = {
                "fields": [
                    {
                        "name": field.get("name"),
                        "label": field.get("label"),
                        "type": field.get("type"),
                        "referenceTo": field.get("referenceTo", []),
                    }
                    for field in description.get("fields", [])
                ]
            }
        return snapshot

    def _load_mapping(self) -> dict[str, Any]:
        if self.mapping_path is None:
            return json.loads(json.dumps(DEFAULT_FIELD_MAPPING))
        try:
            payload = json.loads(self.mapping_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"No se pudo leer FIELD_MAPPING_PATH={self.mapping_path}: {exc}") from exc
        if not isinstance(payload, dict) or "donation" not in payload:
            raise ValueError("El field mapping debe ser un objeto con una sección 'donation'")
        return payload


def _object_field_names(snapshot: dict[str, Any], object_name: str) -> set[str]:
    return {
        str(field["name"])
        for field in snapshot.get("objects", {}).get(object_name, {}).get("fields", [])
        if field.get("name")
    }


def build_report_plan(
    request: SalesforceReportRequest,
    snapshot: dict[str, Any],
) -> SalesforceReportPlan:
    mapping = snapshot.get("field_mapping", DEFAULT_FIELD_MAPPING)
    donation = mapping.get("donation", {})
    primary_object = str(donation.get("object") or "Opportunity")
    donation_mapping = donation.get("fields", {})
    selected = ["Id"]
    warnings = list(snapshot.get("warnings", []))
    available = _object_field_names(snapshot, primary_object)

    for semantic_name in request.donation_fields:
        api_value = donation_mapping.get(semantic_name)
        if api_value is None:
            warnings.append(f"Sin mapping Salesforce para el campo de donación '{semantic_name}'.")
            continue
        values = api_value if isinstance(api_value, list) else [api_value]
        for value in values:
            if snapshot.get("offline") or not available or value in available:
                selected.append(str(value))
            else:
                warnings.append(f"El campo {primary_object}.{value} no está visible en el schema.")

    if "CampaignId" not in selected:
        selected.append("CampaignId")
    if request.year is not None and "CloseDate" not in selected:
        selected.append("CloseDate")

    # Solo agregamos relaciones de persona cuando el mapping manual las expresa para el objeto principal.
    relationships = mapping.get("relationships", {})
    person_prefix = relationships.get("person_from_donation")
    if request.person_fields and person_prefix:
        person_mapping = mapping.get("person", {}).get("fields", {})
        for semantic_name in request.person_fields:
            api_value = person_mapping.get(semantic_name)
            if api_value is None:
                warnings.append(f"Sin mapping Salesforce para el campo personal '{semantic_name}'.")
                continue
            values = api_value if isinstance(api_value, list) else [api_value]
            selected.extend(f"{person_prefix}.{value}" for value in values)
    elif request.person_fields:
        warnings.append(
            "No se infirió una relación inequívoca entre la donación y Contact; "
            "los campos personales requieren mapping manual."
        )

    needs_clarification = primary_object not in snapshot.get("objects", {}) and not snapshot.get(
        "offline"
    )
    questions = (
        [f"¿Qué objeto contiene las altas/donaciones? '{primary_object}' no está accesible."]
        if needs_clarification
        else []
    )
    title = f"Altas {request.year or ''} por campaña".strip()
    return SalesforceReportPlan(
        task_id=request.task_id,
        title=title,
        description="Informe de altas y datos de donación para las campañas solicitadas.",
        primary_object=primary_object,
        selected_fields=list(dict.fromkeys(selected)),
        filters=["CampaignId en campañas solicitadas", f"Año {request.year}"]
        if request.year
        else ["CampaignId en campañas solicitadas"],
        campaign_ids=request.campaign_ids,
        date_filter_description=f"Desde {request.year}-01-01 hasta antes de {request.year + 1}-01-01"
        if request.year
        else None,
        joins_or_relationships=[str(person_prefix)] if person_prefix else [],
        warnings=warnings,
        needs_clarification=needs_clarification,
        clarification_questions=questions,
    )

