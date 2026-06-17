from __future__ import annotations

import json
from copy import deepcopy
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
            "campaña_origen": None,
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
                        "relationshipName": field.get("relationshipName"),
                    }
                    for field in description.get("fields", [])
                ]
            }
        return snapshot

    def _load_mapping(self) -> dict[str, Any]:
        if self.mapping_path is None:
            return deepcopy(DEFAULT_FIELD_MAPPING)
        try:
            payload = json.loads(self.mapping_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(
                f"No se pudo leer FIELD_MAPPING_PATH={self.mapping_path}: {exc}"
            ) from exc
        if not isinstance(payload, dict) or "donation" not in payload:
            raise ValueError("El field mapping debe ser un objeto con una sección 'donation'")
        return dict(payload)


def _object_field_names(snapshot: dict[str, Any], object_name: str) -> set[str]:
    return {
        str(field["name"])
        for field in snapshot.get("objects", {}).get(object_name, {}).get("fields", [])
        if field.get("name")
    }


def _object_relationship_names(snapshot: dict[str, Any], object_name: str) -> set[str]:
    return {
        str(field["relationshipName"])
        for field in snapshot.get("objects", {}).get(object_name, {}).get("fields", [])
        if field.get("relationshipName")
    }


def _mapped_value(mapping: dict[str, Any], semantic_name: str) -> Any:
    aliases = {
        "fecha_nacimiento_o_edad": "fecha_nacimiento",
        "lugar_de_residencia": "residencia",
        "fecha_de_finalizacion": "fecha_finalizacion",
        "campaña_origen": "fuente_origen",
    }
    return mapping.get(semantic_name, mapping.get(aliases.get(semantic_name, "")))


def build_report_plan(
    request: SalesforceReportRequest,
    snapshot: dict[str, Any],
    *,
    allow_report_without_person_fields: bool = False,
) -> SalesforceReportPlan:
    mapping = snapshot.get("field_mapping", DEFAULT_FIELD_MAPPING)
    warnings: list[str] = []
    questions: list[str] = []
    donation_value = mapping.get("donation", {})
    donation = donation_value if isinstance(donation_value, dict) else {}
    if not isinstance(donation_value, dict):
        warnings.append("La sección 'donation' del field mapping no es un objeto válido.")
        questions.append("¿Qué objeto y campos de Salesforce representan las donaciones?")
    elif not donation.get("object"):
        warnings.append("Falta donation.object en el field mapping.")
        questions.append("¿Qué objeto de Salesforce contiene las altas/donaciones?")
    primary_object = str(donation.get("object") or "Opportunity")
    donation_fields_value = donation.get("fields", {})
    donation_mapping = donation_fields_value if isinstance(donation_fields_value, dict) else {}
    if not isinstance(donation_fields_value, dict):
        warnings.append("La sección 'donation.fields' del field mapping no es válida.")
        questions.append("¿Cuál es el mapping de campos de la donación?")
    selected = ["Id"]
    available = _object_field_names(snapshot, primary_object)
    offline = bool(snapshot.get("offline"))

    for semantic_name in request.donation_fields:
        api_value = _mapped_value(donation_mapping, semantic_name)
        if api_value is None:
            warnings.append(f"Sin mapping Salesforce para el campo de donación '{semantic_name}'.")
            questions.append(f"¿Qué campo de {primary_object} representa '{semantic_name}'?")
            continue
        values = api_value if isinstance(api_value, list) else [api_value]
        for value in values:
            if not isinstance(value, str):
                warnings.append(
                    f"El mapping del campo de donación '{semantic_name}' debe usar API names."
                )
                questions.append(f"¿Qué API name de {primary_object} representa '{semantic_name}'?")
            elif offline or value in available:
                selected.append(value)
            else:
                warnings.append(f"El campo {primary_object}.{value} no está visible en el schema.")
                questions.append(f"¿Qué campo visible de {primary_object} reemplaza a {value}?")

    if request.campaign_ids and "CampaignId" not in selected:
        selected.append("CampaignId")
    date_field = "CreatedDate" if primary_object == "CampaignMember" else "CloseDate"
    if request.year is not None and (offline or date_field in available):
        selected.append(date_field)

    origin_source_field: str | None = None
    if request.origin_sources:
        origin_mapping = _mapped_value(donation_mapping, "campaña_origen")
        if not origin_mapping:
            warning = "No hay mapping Salesforce para campaña de origen/fuente."
            warnings.append(warning)
            questions.append("¿Qué campo de Salesforce representa campaña de origen/fuente?")
        elif not isinstance(origin_mapping, str):
            warnings.append("El mapping de campaña de origen/fuente debe ser un único campo.")
            questions.append("¿Qué campo único de Salesforce representa campaña de origen/fuente?")
        elif not offline and origin_mapping not in available:
            warnings.append(
                f"El campo {primary_object}.{origin_mapping} configurado como origen no está visible."
            )
            questions.append(
                f"¿Qué campo visible de {primary_object} representa campaña de origen/fuente?"
            )
        else:
            origin_source_field = origin_mapping
            selected.append(origin_mapping)

    # Los campos personales solo se consultan mediante una relación configurada y verificable.
    relationships_value = mapping.get("relationships", {})
    relationships = relationships_value if isinstance(relationships_value, dict) else {}
    if request.person_fields and not isinstance(relationships_value, dict):
        warnings.append("La sección 'relationships' del field mapping no es válida.")
    person_prefix = relationships.get("person_from_donation")
    relationship_names = _object_relationship_names(snapshot, primary_object)
    relationship_is_visible = bool(
        offline or (isinstance(person_prefix, str) and person_prefix in relationship_names)
    )
    validated_person_prefix: str | None = None
    if request.person_fields and person_prefix:
        if not isinstance(person_prefix, str) or not relationship_is_visible:
            warnings.append(
                f"La relación personal '{person_prefix}' no está visible en {primary_object}."
            )
            if not allow_report_without_person_fields:
                questions.append(
                    f"¿Qué relación visible de {primary_object} conecta la donación con la persona?"
                )
        else:
            validated_person_prefix = person_prefix
            person_value = mapping.get("person", {})
            person_config = person_value if isinstance(person_value, dict) else {}
            person_fields_value = person_config.get("fields", {})
            person_mapping = person_fields_value if isinstance(person_fields_value, dict) else {}
            person_object = str(person_config.get("object") or "Contact")
            available_person = _object_field_names(snapshot, person_object)
            for semantic_name in request.person_fields:
                api_value = _mapped_value(person_mapping, semantic_name)
                if api_value is None:
                    warnings.append(
                        f"Sin mapping Salesforce para el campo personal '{semantic_name}'."
                    )
                    if not allow_report_without_person_fields:
                        questions.append(
                            f"¿Qué campo de {person_object} representa '{semantic_name}'?"
                        )
                    continue
                values = api_value if isinstance(api_value, list) else [api_value]
                visible_values = [
                    value
                    for value in values
                    if isinstance(value, str) and (offline or value in available_person)
                ]
                missing_values = [
                    str(value) for value in values if str(value) not in visible_values
                ]
                if missing_values:
                    warnings.append(
                        f"Campos personales no visibles en {person_object}: "
                        + ", ".join(missing_values)
                    )
                    if not allow_report_without_person_fields:
                        questions.append(
                            f"¿Qué campos visibles de {person_object} reemplazan a "
                            + ", ".join(missing_values)
                            + "?"
                        )
                selected.extend(f"{person_prefix}.{value}" for value in visible_values)
    elif request.person_fields:
        warnings.append(
            "No se infirió una relación inequívoca entre la donación y Contact; "
            "los campos personales requieren mapping manual."
        )
        if not allow_report_without_person_fields:
            questions.append(
                f"¿Qué relación de {primary_object} conecta la donación con la persona "
                "(por ejemplo, npsp__Primary_Contact__r, Contact u otra relación custom)?"
            )

    if not snapshot.get("offline") and primary_object not in snapshot.get("objects", {}):
        questions.append(
            f"¿Qué objeto contiene las altas/donaciones? '{primary_object}' no está accesible."
        )
    if primary_object not in {"Opportunity", "CampaignMember"}:
        questions.append(
            f"El objeto {primary_object} requiere definir explícitamente sus campos de campaña y fecha."
        )
    if (
        request.campaign_ids
        and not offline
        and primary_object in snapshot.get("objects", {})
        and "CampaignId" not in available
    ):
        questions.append(f"{primary_object} no expone CampaignId para filtrar las campañas.")
    if (
        request.year
        and not offline
        and primary_object in snapshot.get("objects", {})
        and date_field not in available
    ):
        questions.append(
            f"{primary_object} no expone {date_field} para filtrar el año {request.year}."
        )
    if request.campaign_names and not request.campaign_ids:
        questions.append("¿Cuáles son los Campaign IDs de las campañas identificadas por nombre?")
    if not request.campaign_ids and not request.campaign_names and not request.origin_sources:
        questions.append("¿Qué campañas o fuentes de origen debe incluir el reporte?")
    if "período" in request.missing_information:
        questions.append("¿Qué año o período debe cubrir el reporte?")
    if "campos requeridos" in request.missing_information:
        questions.append("¿Qué campos debe incluir el reporte?")
    needs_clarification = bool(questions)
    if request.report_type == "altas_por_campaña":
        title = f"Altas {request.year or ''} por campaña".strip()
        description = "Informe de altas y datos de donación para las campañas solicitadas."
    else:
        title = request.report_type.replace("_", " ").strip().capitalize() or "Reporte Salesforce"
        if request.year:
            title = f"{title} {request.year}"
        description = f"{title} para el alcance solicitado."
    filters = []
    if request.campaign_ids:
        filters.append("CampaignId en campañas solicitadas")
    if request.origin_sources and origin_source_field:
        filters.append(f"{origin_source_field} en fuentes de origen solicitadas")
    if request.year:
        filters.append(f"Año {request.year}")
    return SalesforceReportPlan(
        task_id=request.task_id,
        title=title,
        description=description,
        primary_object=primary_object,
        selected_fields=list(dict.fromkeys(selected)),
        filters=filters,
        campaign_ids=request.campaign_ids,
        origin_sources=request.origin_sources,
        origin_source_field=origin_source_field,
        date_filter_description=f"Desde {request.year}-01-01 hasta antes de {request.year + 1}-01-01"
        if request.year
        else None,
        joins_or_relationships=[validated_person_prefix] if validated_person_prefix else [],
        warnings=warnings,
        needs_clarification=needs_clarification,
        clarification_questions=questions,
    )
