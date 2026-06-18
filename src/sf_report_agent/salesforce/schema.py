from __future__ import annotations

import json
import re
import unicodedata
from copy import deepcopy
from pathlib import Path
from typing import Any

from sf_report_agent.models.report_plan import SalesforceReportPlan, SalesforceReportPlanBundle
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
                        "filterable": field.get("filterable"),
                        "picklistValues": [
                            {
                                "value": item.get("value"),
                                "label": item.get("label"),
                                "active": item.get("active"),
                            }
                            for item in field.get("picklistValues", [])
                            if isinstance(item, dict)
                        ],
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


def _object_fields_by_name(
    snapshot: dict[str, Any], object_name: str
) -> dict[str, dict[str, Any]]:
    return {
        str(field["name"]): field
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


def _relationship_name_for_field(field_name: str, field: dict[str, Any] | None) -> str | None:
    if field is not None:
        relationship_name = field.get("relationshipName")
        if isinstance(relationship_name, str) and relationship_name:
            return relationship_name
    if field_name.endswith("__c"):
        return field_name[:-3] + "__r"
    if field_name.endswith("Id"):
        return field_name[:-2]
    return None


def _references_campaign(field: dict[str, Any] | None) -> bool:
    if field is None:
        return False
    references = field.get("referenceTo", [])
    return isinstance(references, list) and "Campaign" in references


def _configured_api_names(value: Any) -> list[str]:
    values = value if isinstance(value, list) else [value]
    return [item for item in values if isinstance(item, str) and item]


def _fold(value: str) -> str:
    return "".join(
        char
        for char in unicodedata.normalize("NFKD", value.casefold())
        if not unicodedata.combining(char)
    )


def _variant_id(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", _fold(value)).strip("_")
    return normalized or "variant"


def _field_label(field_name: str, field: dict[str, Any] | None) -> str:
    if field is not None:
        label = field.get("label")
        if isinstance(label, str) and label.strip():
            return label.strip()
    return field_name.replace("__c", "").replace("_", " ").strip()


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
    field_details = _object_fields_by_name(snapshot, primary_object)
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

    configured_campaign_fields = donation.get("campaign_filter_fields")
    has_explicit_campaign_fields = configured_campaign_fields is not None
    if has_explicit_campaign_fields:
        campaign_candidates = _configured_api_names(configured_campaign_fields)
        if not isinstance(configured_campaign_fields, (str, list)) or not campaign_candidates:
            warnings.append("donation.campaign_filter_fields no contiene API names válidos.")
    else:
        campaign_candidates = _configured_api_names(
            [
                _mapped_value(donation_mapping, "campaña_origen"),
                _mapped_value(donation_mapping, "campaña"),
            ]
        )
    if not offline:
        campaign_candidates.extend(
            field_name
            for field_name, field in field_details.items()
            if _references_campaign(field)
        )
    campaign_candidates = list(dict.fromkeys(campaign_candidates))
    campaign_relationships_value = donation.get("campaign_relationships", {})
    campaign_relationships = (
        campaign_relationships_value
        if isinstance(campaign_relationships_value, dict)
        else {}
    )
    if not isinstance(campaign_relationships_value, dict):
        warnings.append("donation.campaign_relationships no es un objeto válido.")

    campaign_filter_fields: list[str] = []
    for field_name in campaign_candidates:
        field = field_details.get(field_name)
        if offline:
            is_campaign_field = has_explicit_campaign_fields or field_name.endswith(
                ("Id", "__c")
            )
        else:
            is_campaign_field = _references_campaign(field)
        if not is_campaign_field:
            if has_explicit_campaign_fields:
                warnings.append(
                    f"El campo {primary_object}.{field_name} no referencia Campaign."
                )
            continue
        if not offline and field_name not in available:
            warnings.append(
                f"El campo de campaña {primary_object}.{field_name} no está visible."
            )
            continue
        campaign_filter_fields.append(field_name)
        selected.append(field_name)
        configured_relationship = campaign_relationships.get(field_name)
        relationship_name = (
            configured_relationship
            if isinstance(configured_relationship, str) and configured_relationship
            else _relationship_name_for_field(field_name, field)
        )
        if relationship_name:
            selected.append(f"{relationship_name}.Name")

    campaign_filter_fields = list(dict.fromkeys(campaign_filter_fields))
    if request.campaign_ids and not campaign_filter_fields:
        questions.append(
            f"¿Qué campos visibles de {primary_object} referencian las campañas solicitadas?"
        )

    date_field: str | None = None
    if request.year is not None or request.date_from is not None or request.date_to is not None:
        date_semantics = (
            ("fecha_alta", "fecha_establecida")
            if request.report_type == "altas_por_campaña"
            else ("fecha_establecida", "fecha_alta")
        )
        date_candidates = _configured_api_names(donation.get("date_field"))
        for semantic_name in date_semantics:
            date_candidates.extend(
                _configured_api_names(_mapped_value(donation_mapping, semantic_name))
            )
        if not offline:
            date_candidates.extend(
                field_name
                for field_name, field in field_details.items()
                if any(
                    hint in _fold(str(field.get("label") or ""))
                    for hint in ("fecha establecida", "fecha de alta", "fecha alta")
                )
            )
        date_candidates = list(dict.fromkeys(date_candidates))
        visible_date_candidates = [
            candidate for candidate in date_candidates if offline or candidate in available
        ]
        if not visible_date_candidates:
            warnings.append("No hay un campo de fecha primario configurado para el reporte.")
            questions.append(
                f"¿Qué campo de {primary_object} representa la fecha del reporte?"
            )
        else:
            date_field = visible_date_candidates[0]
            selected.extend(visible_date_candidates)
        missing_date_candidates = [
            candidate for candidate in date_candidates if candidate not in visible_date_candidates
        ]
        if missing_date_candidates:
            warnings.append(
                f"Campos de fecha sugeridos no visibles en {primary_object}: "
                + ", ".join(missing_date_candidates)
            )
        if len(visible_date_candidates) > 1:
            warnings.append(
                "Se encontraron varias fechas compatibles con alta; se filtró por "
                f"{date_field} y se incluyeron las alternativas visibles."
            )

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
            if origin_mapping in campaign_filter_fields and not request.campaign_ids:
                questions.append(
                    "¿Cuáles son los Campaign IDs que representan las fuentes de origen solicitadas?"
                )

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
    if request.campaign_names and not request.campaign_ids:
        questions.append("¿Cuáles son los Campaign IDs de las campañas identificadas por nombre?")
    if not request.campaign_ids and not request.campaign_names and not request.origin_sources:
        questions.append("¿Qué campañas o fuentes de origen debe incluir el reporte?")
    if "período" in request.missing_information:
        questions.append("¿Qué año o período debe cubrir el reporte?")
    if request.year is None and request.date_from is None and request.date_to is None:
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
    if request.campaign_ids and campaign_filter_fields:
        filters.append(
            " o ".join(campaign_filter_fields) + " en campañas solicitadas"
        )
    if (
        request.origin_sources
        and origin_source_field
        and origin_source_field not in campaign_filter_fields
    ):
        filters.append(f"{origin_source_field} en fuentes de origen solicitadas")
    if request.year and date_field:
        filters.append(f"Año {request.year} según {date_field}")
    elif date_field and (request.date_from or request.date_to):
        filters.append(f"Período solicitado según {date_field}")
    return SalesforceReportPlan(
        task_id=request.task_id,
        variant_id="combined" if len(campaign_filter_fields) > 1 else "default",
        variant_label="Campañas combinadas" if len(campaign_filter_fields) > 1 else title,
        title=title,
        description=description,
        primary_object=primary_object,
        selected_fields=list(dict.fromkeys(selected)),
        filters=filters,
        campaign_ids=request.campaign_ids,
        campaign_filter_fields=campaign_filter_fields,
        origin_sources=request.origin_sources,
        origin_source_field=origin_source_field,
        origin_sources_resolved_by_campaign_ids=bool(
            request.campaign_ids
            and origin_source_field
            and origin_source_field in campaign_filter_fields
        ),
        date_filter_field=date_field,
        date_filter_description=(
            f"CALENDAR_YEAR({date_field}) = {request.year}"
            if request.year and date_field
            else (
                f"Desde {request.date_from or 'inicio'} hasta {request.date_to or 'fin'}"
                if date_field and (request.date_from or request.date_to)
                else None
            )
        ),
        joins_or_relationships=[validated_person_prefix] if validated_person_prefix else [],
        warnings=warnings,
        needs_clarification=needs_clarification,
        clarification_questions=questions,
    )


def build_report_plan_bundle(
    request: SalesforceReportRequest,
    snapshot: dict[str, Any],
    *,
    allow_report_without_person_fields: bool = False,
) -> SalesforceReportPlanBundle:
    base_plan = build_report_plan(
        request,
        snapshot,
        allow_report_without_person_fields=allow_report_without_person_fields,
    )
    questions = list(dict.fromkeys(base_plan.clarification_questions))
    warnings = list(dict.fromkeys(base_plan.warnings))
    if base_plan.needs_clarification:
        return SalesforceReportPlanBundle(
            task_id=request.task_id,
            plans=[base_plan],
            needs_clarification=True,
            clarification_questions=questions,
            warnings=warnings,
        )

    campaign_fields = base_plan.campaign_filter_fields
    if len(campaign_fields) <= 1 or not request.campaign_ids:
        plan = base_plan.model_copy(
            update={
                "variant_id": "default",
                "variant_label": base_plan.title,
                "ambiguity_reason": None,
            }
        )
        return SalesforceReportPlanBundle(task_id=request.task_id, plans=[plan], warnings=warnings)

    field_details = _object_fields_by_name(snapshot, base_plan.primary_object)
    ambiguity_note = (
        "El pedido admite más de una interpretación segura del campo campaña; "
        "se generó una variante por lookup y una variante combinada."
    )
    plans: list[SalesforceReportPlan] = []
    used_ids: set[str] = set()
    for index, field_name in enumerate(campaign_fields, start=1):
        label = _field_label(field_name, field_details.get(field_name))
        candidate_id = _variant_id(label)
        variant_id = candidate_id if candidate_id not in used_ids else f"{candidate_id}_{index}"
        used_ids.add(variant_id)
        plans.append(
            base_plan.model_copy(
                update={
                    "variant_id": variant_id,
                    "variant_label": label,
                    "ambiguity_reason": f"Campañas filtradas únicamente por {label}.",
                    "campaign_filter_fields": [field_name],
                    "filters": [
                        f"{field_name} en campañas solicitadas",
                        *[
                            value
                            for value in base_plan.filters
                            if " en campañas solicitadas" not in value
                        ],
                    ],
                }
            )
        )
    plans.append(
        base_plan.model_copy(
            update={
                "variant_id": "combined",
                "variant_label": "Campañas combinadas",
                "ambiguity_reason": "Campañas filtradas por cualquiera de los lookups detectados.",
            }
        )
    )
    return SalesforceReportPlanBundle(
        task_id=request.task_id,
        plans=plans,
        ambiguity_note=ambiguity_note,
        warnings=warnings,
    )
