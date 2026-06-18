from __future__ import annotations

import re
import unicodedata
from typing import Any

from sf_report_agent.models.report_plan import (
    DerivedFieldPlan,
    SalesforceFilter,
    SalesforceReportPlan,
    SalesforceReportPlanBundle,
)
from sf_report_agent.models.report_request import SalesforceReportRequest
from sf_report_agent.salesforce.business_semantics import (
    BusinessEntity,
    BusinessSemantics,
    ReportProfile,
    contains_term,
)


def _variant_id(value: str) -> str:
    folded = "".join(
        char
        for char in unicodedata.normalize("NFKD", value.casefold())
        if not unicodedata.combining(char)
    )
    return re.sub(r"[^a-z0-9]+", "_", folded).strip("_") or "default"


def _object_fields(snapshot: dict[str, Any], object_name: str) -> list[dict[str, Any]]:
    fields = snapshot.get("objects", {}).get(object_name, {}).get("fields", [])
    return [field for field in fields if isinstance(field, dict)]


def _inferred_relationship_name(field_name: str) -> str | None:
    if field_name.endswith("__c"):
        return field_name[:-3] + "__r"
    if field_name.endswith("Id"):
        return field_name[:-2]
    return None


def _visible_field(snapshot: dict[str, Any], object_name: str, field_path: str) -> bool:
    if snapshot.get("offline"):
        return True
    segments = field_path.split(".")
    current_object = object_name
    for index, segment in enumerate(segments):
        fields = _object_fields(snapshot, current_object)
        if index == len(segments) - 1:
            return any(field.get("name") == segment for field in fields)
        lookup = next(
            (
                field
                for field in fields
                if field.get("relationshipName") == segment
                or _inferred_relationship_name(str(field.get("name") or "")) == segment
            ),
            None,
        )
        if lookup is None:
            return False
        references = lookup.get("referenceTo", [])
        if not isinstance(references, list) or not references:
            return False
        current_object = str(references[0])
    return False


def _supports_contains_filter(
    snapshot: dict[str, Any], object_name: str, field_name: str
) -> bool:
    if snapshot.get("offline"):
        return True
    field = next(
        (
            value
            for value in _object_fields(snapshot, object_name)
            if value.get("name") == field_name
        ),
        None,
    )
    if field is None or field.get("filterable") is not True:
        return False
    field_type = str(field.get("type") or "").casefold()
    return field_type in {
        "email",
        "encryptedstring",
        "phone",
        "string",
        "textarea",
        "url",
    }


def _metadata_value_labels(
    snapshot: dict[str, Any], object_name: str, field_name: str
) -> dict[str, str]:
    field = next(
        (
            value
            for value in _object_fields(snapshot, object_name)
            if value.get("name") == field_name
        ),
        None,
    )
    if field is None:
        return {}
    values = field.get("picklistValues", [])
    if not isinstance(values, list):
        return {}
    return {
        str(item["value"]): str(item["label"])
        for item in values
        if isinstance(item, dict) and item.get("value") and item.get("label")
    }


def _select_date_concept(entity: BusinessEntity, text: str) -> str | None:
    preferred = [
        name
        for name, concept in entity.concepts.items()
        if concept.terms and contains_term(text, concept.terms)
        and name in {"signup_date", "end_date", "transaction_date"}
    ]
    if preferred:
        return preferred[0]
    for fallback in ("transaction_date", "signup_date", "end_date"):
        if fallback in entity.concepts:
            return fallback
    return None


def _profile_fields(
    snapshot: dict[str, Any],
    entity: BusinessEntity,
    configured_fields: list[str],
) -> tuple[list[str], list[str], list[str]]:
    selected = ["Id"]
    hidden = ["Id"] if "Id" not in configured_fields else []
    warnings: list[str] = []
    for field_name in configured_fields:
        if _visible_field(snapshot, entity.object, field_name):
            selected.append(field_name)
        else:
            warnings.append(
                f"El campo de negocio {entity.object}.{field_name} no está visible y se omitió."
            )
    return list(dict.fromkeys(selected)), hidden, warnings


def _configured_profile_fields(
    profile: ReportProfile | None,
    request: SalesforceReportRequest,
    text: str,
    snapshot: dict[str, Any],
    entity: BusinessEntity,
) -> tuple[list[str], list[str], list[str]]:
    if profile is None:
        return [], [], []
    fields = list(profile.fields)
    derived_fields = list(profile.derived_fields)
    warnings: list[str] = []
    requested_fields = {*request.person_fields, *request.donation_fields}
    for group in profile.conditional_field_groups:
        if requested_fields.intersection(group.request_fields) or contains_term(
            text, group.terms
        ):
            visible_fields = [
                field_name
                for field_name in group.fields
                if _visible_field(snapshot, entity.object, field_name)
            ]
            fields.extend(visible_fields)
            missing_fields = [
                field_name for field_name in group.fields if field_name not in visible_fields
            ]
            if missing_fields:
                visible_fallbacks = [
                    field_name
                    for field_name in group.fallback_fields
                    if _visible_field(snapshot, entity.object, field_name)
                ]
                fields.extend(visible_fallbacks)
                if visible_fallbacks:
                    warnings.append(
                        "No están visibles todos los campos preferidos "
                        f"({', '.join(missing_fields)}); se usó "
                        f"{', '.join(visible_fallbacks)} como fallback."
                    )
                else:
                    warnings.append(
                        "No están visibles los campos solicitados "
                        f"{', '.join(missing_fields)} y no hay fallback visible; se omitieron."
                    )
            derived_fields.extend(group.derived_fields)
    return (
        list(dict.fromkeys(fields)),
        list(dict.fromkeys(derived_fields)),
        warnings,
    )


def build_business_plan_bundle(
    request: SalesforceReportRequest,
    snapshot: dict[str, Any],
    semantics: BusinessSemantics,
) -> SalesforceReportPlanBundle | None:
    text = request.source_text
    selected_entity = semantics.select_entity(text)
    if selected_entity is None:
        return None
    entity_name, entity = selected_entity
    profile = semantics.select_profile(entity_name, text)
    profile_fields, profile_derived_fields, profile_warnings = (
        _configured_profile_fields(profile, request, text, snapshot, entity)
    )
    warnings: list[str] = list(profile_warnings)
    questions: list[str] = []

    if not snapshot.get("offline") and entity.object not in snapshot.get("objects", {}):
        questions.append(
            f"¿Qué objeto visible representa {entity.description}? {entity.object} no está accesible."
        )
    selected_fields, hidden_fields, field_warnings = _profile_fields(
        snapshot, entity, profile_fields
    )
    warnings.extend(field_warnings)

    derived_fields: list[DerivedFieldPlan] = []
    for derived_name in profile_derived_fields:
        definition = semantics.derived_fields.get(derived_name)
        if definition is None:
            warnings.append(f"No existe la definición del campo derivado {derived_name}.")
            continue
        visible_sources = [
            field_name
            for field_name in definition.source_fields
            if _visible_field(snapshot, entity.object, field_name)
        ]
        if not visible_sources:
            warnings.append(
                f"No hay fuentes visibles para calcular el campo derivado {definition.label}."
            )
            continue
        selected_fields.extend(visible_sources)
        hidden_fields.extend(
            field_name for field_name in visible_sources if field_name not in profile_fields
        )
        derived_fields.append(
            DerivedFieldPlan(
                output_field=definition.output_field,
                label=definition.label,
                kind=definition.kind,
                source_fields=visible_sources,
                strategy=definition.strategy,
            )
        )

    scope_filters: list[SalesforceFilter] = []
    joins: list[str] = []
    contact_required = bool(
        entity.contact
        and (
            (profile and profile.requires_contact)
            or request.person_fields
            or contains_term(text, entity.contact.required_terms)
        )
    )
    if contact_required and entity.contact:
        if not _visible_field(snapshot, entity.object, entity.contact.lookup_field):
            questions.append(
                f"¿Qué relación visible conecta {entity.object} con Contact para este reporte?"
            )
        elif entity.contact.missing_policy == "exclude":
            scope_filters.append(
                SalesforceFilter(
                    field=entity.contact.lookup_field,
                    operator="not_null",
                    description="Solo donaciones vinculadas a Contact",
                )
            )
            joins.append(entity.contact.relationship)
        elif entity.contact.missing_policy == "warn":
            warnings.append("El reporte puede incluir donaciones sin Contact vinculado.")
        else:
            questions.append(
                "¿Debe excluirse del reporte cualquier donación que no tenga Contact vinculado?"
            )

    status = entity.concepts.get("status")
    if status and _visible_field(snapshot, entity.object, status.field):
        for value in status.values.values():
            if contains_term(text, value.terms):
                scope_filters.append(
                    SalesforceFilter(
                        field=status.field,
                        operator="equals",
                        values=[value.value],
                        description=f"{status.label} = {value.value}",
                    )
                )
                break

    date_concept_name = _select_date_concept(entity, text)
    date_concept = entity.concepts.get(date_concept_name or "")
    date_field: str | None = None
    if date_concept and _visible_field(snapshot, entity.object, date_concept.field):
        date_field = date_concept.field
        selected_fields.append(date_field)
    elif request.year or request.date_from or request.date_to:
        questions.append(
            f"¿Qué campo visible de {entity.object} representa la fecha de este reporte?"
        )

    dimension = semantics.campaign_dimensions.get("main_origin")
    if dimension is not None and dimension.entity != entity_name:
        dimension = None
    campaign_values = semantics.campaign_group_values(
        [*request.campaign_names, *request.origin_sources]
    )
    campaign_dimension_ready = False
    main_campaign_requested = bool(
        dimension
        and (
            contains_term(text, dimension.terms)
            or bool(campaign_values)
        )
    )
    if main_campaign_requested and dimension:
        if not _visible_field(snapshot, entity.object, dimension.field):
            questions.append(
                f"La dimensión de negocio {dimension.label} no está visible en {entity.object}."
            )
        elif not _supports_contains_filter(snapshot, entity.object, dimension.field):
            questions.append(
                f"La dimensión {dimension.label} existe en el reporte Salesforce, pero no está "
                "disponible/filtrable por SOQL con el usuario actual."
            )
        elif not campaign_values:
            questions.append(
                f"¿Qué valores de {dimension.label} deben incluirse en el reporte?"
            )
        else:
            selected_fields.append(dimension.field)
            campaign_dimension_ready = True

    selected_fields = list(dict.fromkeys(selected_fields))
    hidden_fields = list(dict.fromkeys(hidden_fields))
    value_labels = {
        field_name: labels
        for field_name in selected_fields
        if (labels := _metadata_value_labels(snapshot, entity.object, field_name))
    }
    for concept in entity.concepts.values():
        if concept.field not in selected_fields:
            continue
        semantic_labels = {
            value.value: value.label
            for value in concept.values.values()
            if value.label
        }
        if semantic_labels:
            value_labels[concept.field] = {
                **value_labels.get(concept.field, {}),
                **semantic_labels,
            }
    needs_clarification = bool(questions)
    title_base = "Informe de " + (
        "Altas" if date_concept_name == "signup_date" else
        "Bajas" if date_concept_name == "end_date" else
        "Donaciones"
    )
    if request.year:
        title_base += f" {request.year}"

    plan_values: list[str | None] = list(campaign_values) if campaign_values else [None]
    plans: list[SalesforceReportPlan] = []
    for campaign_value in plan_values:
        plan_filters = list(scope_filters)
        if campaign_value and dimension and campaign_dimension_ready:
            plan_filters.append(
                SalesforceFilter(
                    field=dimension.field,
                    operator="contains",
                    values=[campaign_value],
                    description=f"{dimension.label} contiene {campaign_value}",
                )
            )
        variant_label = campaign_value or title_base
        title = f"{title_base} - {campaign_value}" if campaign_value else title_base
        plans.append(
            SalesforceReportPlan(
                task_id=request.task_id,
                variant_id=_variant_id(variant_label),
                variant_label=variant_label,
                ambiguity_reason=(
                    f"Segmentación por la dimensión de negocio {dimension.label}."
                    if campaign_value and dimension
                    else None
                ),
                title=title,
                description=entity.description,
                primary_object=entity.object,
                selected_fields=selected_fields,
                filters=[item.description for item in plan_filters],
                scope_filters=plan_filters,
                campaign_ids=request.campaign_ids,
                origin_sources=request.origin_sources,
                date_filter_field=date_field,
                date_filter_mode="range",
                date_filter_description=(
                    f"Desde {request.year}-01-01 hasta antes de {request.year + 1}-01-01"
                    if request.year and date_field
                    else None
                ),
                joins_or_relationships=joins,
                derived_fields=derived_fields,
                hidden_fields=hidden_fields,
                output_order=list(profile.output_order) if profile else [],
                value_labels=value_labels,
                warnings=warnings,
                needs_clarification=needs_clarification,
                clarification_questions=questions,
            )
        )

    ambiguity_note = (
        f"Se generó un informe por cada valor de {dimension.label}."
        if len(plans) > 1 and dimension
        else None
    )
    return SalesforceReportPlanBundle(
        task_id=request.task_id,
        plans=plans,
        ambiguity_note=ambiguity_note,
        needs_clarification=needs_clarification,
        clarification_questions=list(dict.fromkeys(questions)),
        warnings=list(dict.fromkeys(warnings)),
    )
