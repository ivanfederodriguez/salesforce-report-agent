from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pandas as pd
import pytest

import sf_report_agent.graph.nodes as graph_nodes_module
from sf_report_agent.graph.nodes import ReportGraphNodes
from sf_report_agent.models.report_plan import DerivedFieldPlan
from sf_report_agent.models.report_request import SalesforceReportRequest
from sf_report_agent.reports.transforms import apply_derived_fields
from sf_report_agent.salesforce.business_planner import build_business_plan_bundle
from sf_report_agent.salesforce.business_semantics import load_business_semantics

SEMANTICS_PATH = Path(__file__).parents[1] / "config" / "business_semantics.yaml"


def _field(
    name: str,
    label: str,
    field_type: str = "string",
    *,
    reference_to: list[str] | None = None,
    relationship_name: str | None = None,
    filterable: bool = True,
) -> dict[str, Any]:
    return {
        "name": name,
        "label": label,
        "type": field_type,
        "filterable": filterable,
        "referenceTo": reference_to or [],
        "relationshipName": relationship_name,
    }


def _schema() -> dict[str, Any]:
    return {
        "objects": {
            "npe03__Recurring_Donation__c": {
                "fields": [
                    _field("Id", "ID", "id"),
                    _field("npe03__Amount__c", "Importe", "currency"),
                    _field("npsp__Status__c", "Estado", "picklist"),
                    _field("npe03__Date_Established__c", "Fecha establecida", "date"),
                    _field("npsp__EndDate__c", "Fecha de finalización", "date"),
                    _field(
                        "npe03__Contact__c",
                        "Contacto",
                        "reference",
                        reference_to=["Contact"],
                        relationship_name="npe03__Contact__r",
                    ),
                    _field("Campa_a_Principal__c", "Campaña Principal de Origen"),
                    _field(
                        "Campa_a_de_origen__c",
                        "Campaña de origen",
                        "reference",
                        reference_to=["Campaign"],
                        relationship_name="Campa_a_de_origen__r",
                    ),
                    _field(
                        "npe03__Recurring_Donation_Campaign__c",
                        "Campaña para las donaciones futuras",
                        "reference",
                        reference_to=["Campaign"],
                        relationship_name="npe03__Recurring_Donation_Campaign__r",
                    ),
                    _field(
                        "Campa_a_de_recupero__c",
                        "Campaña de recupero",
                        "reference",
                        reference_to=["Campaign"],
                        relationship_name="Campa_a_de_recupero__r",
                    ),
                ]
            },
            "Contact": {
                "fields": [
                    _field("Id", "ID", "id"),
                    _field("FirstName", "Nombre"),
                    _field("LastName", "Apellido"),
                    _field("Birthdate", "Fecha de nacimiento", "date"),
                    _field("MailingState", "Provincia"),
                    _field("OtherState", "Otra provincia"),
                    _field("Name", "Nombre completo"),
                ]
            },
            "Opportunity": {
                "fields": [
                    _field("Id", "ID", "id"),
                    _field("CloseDate", "Fecha de cierre", "date"),
                    _field("Amount", "Importe", "currency"),
                    _field("StageName", "Etapa", "picklist"),
                ]
            },
        },
        "field_mapping": {},
        "warnings": [],
    }


def _request(text: str, *, year: int | None = None) -> SalesforceReportRequest:
    return SalesforceReportRequest(
        task_id=7,
        source_text=text,
        report_type="reporte_salesforce",
        year=year,
    )


def _task_23_request() -> SalesforceReportRequest:
    return SalesforceReportRequest(
        task_id=23,
        source_text="Altas 2026 por campaña principal para personas",
        report_type="altas_por_campaña",
        year=2026,
        campaign_names=[
            "[IND] Campañas Pauta Digital",
            "[IND] Redes Sociales",
            "[IND] Redes Sociales - Instagram",
        ],
        origin_sources=["amplify", "orgánico web"],
        person_fields=["nombre_y_apellido"],
    )


def _plan(text: str, *, year: int | None = None) -> Any:
    semantics = load_business_semantics(SEMANTICS_PATH)
    bundle = build_business_plan_bundle(_request(text, year=year), _schema(), semantics)
    assert bundle is not None
    assert not bundle.needs_clarification
    assert bundle.plans
    return bundle.plans[0]


def test_loads_business_semantics_yaml() -> None:
    semantics = load_business_semantics(SEMANTICS_PATH)

    assert semantics.entities["recurring_donation"].object == (
        "npe03__Recurring_Donation__c"
    )
    assert semantics.entities["opportunity"].concepts["amount"].field == "Amount"
    assert semantics.policies.variants_for_technical_lookups is False


def test_alta_uses_date_established_and_recurring_amount() -> None:
    plan = _plan("Informe de altas 2026 con importe actual", year=2026)

    assert plan.primary_object == "npe03__Recurring_Donation__c"
    assert plan.date_filter_field == "npe03__Date_Established__c"
    assert "npe03__Amount__c" in plan.selected_fields
    assert "CloseDate" not in plan.selected_fields


def test_baja_uses_end_date_and_closed_status() -> None:
    plan = _plan("Bajas de donantes cerrados en 2026", year=2026)

    assert plan.date_filter_field == "npsp__EndDate__c"
    assert any(
        item.field == "npsp__Status__c" and item.values == ["Closed"]
        for item in plan.scope_filters
    )


def test_active_donor_filters_active_status_and_requires_contact() -> None:
    plan = _plan("Donantes activos y cuánto dona actualmente cada persona")

    assert any(
        item.field == "npsp__Status__c" and item.values == ["Active"]
        for item in plan.scope_filters
    )
    assert any(
        item.field == "npe03__Contact__c" and item.operator == "not_null"
        for item in plan.scope_filters
    )
    assert "npe03__Contact__r" in plan.joins_or_relationships
    assert "npe03__Contact__r.Birthdate" not in plan.selected_fields
    assert "npe03__Contact__r.MailingState" not in plan.selected_fields


def test_monthly_payment_uses_opportunity_amount() -> None:
    plan = _plan("Cuánto pagó cada donante en marzo de 2026", year=2026)

    assert plan.primary_object == "Opportunity"
    assert plan.date_filter_field == "CloseDate"
    assert "Amount" in plan.selected_fields
    assert "npe03__Amount__c" not in plan.selected_fields


def test_age_and_province_are_derived_locally() -> None:
    plan = _plan("Altas de personas con edad y provincia")
    dataframe = pd.DataFrame(
        {
            "npe03__Contact__r.Birthdate": ["2000-06-19", "1990-01-01"],
            "npe03__Contact__r.MailingState": ["", "Córdoba"],
            "npe03__Contact__r.OtherState": ["Santa Fe", "Mendoza"],
        }
    )

    result = apply_derived_fields(dataframe, plan.derived_fields, as_of=date(2026, 6, 18))

    age = next(field for field in plan.derived_fields if field.label == "Edad")
    assert age.strategy == "round"
    assert result["__derived__.age"].tolist() == [26, 36]
    assert result["__derived__.province"].tolist() == ["Santa Fe", "Córdoba"]


def test_age_years_supports_round_floor_and_calendar_age() -> None:
    dataframe = pd.DataFrame({"Birthdate": ["2000-12-01"]})

    values = {}
    for strategy in ("floor", "round", "calendar_age"):
        result = apply_derived_fields(
            dataframe,
            [
                DerivedFieldPlan(
                    output_field="Age",
                    label="Edad",
                    kind="age_years",
                    source_fields=["Birthdate"],
                    strategy=strategy,
                )
            ],
            as_of=date(2026, 6, 18),
        )
        values[strategy] = result.loc[0, "Age"]

    assert values == {"floor": 25, "round": 26, "calendar_age": 25}


def test_province_uses_only_visible_address_sources() -> None:
    semantics = load_business_semantics(SEMANTICS_PATH)
    schema = _schema()
    contact_fields = schema["objects"]["Contact"]["fields"]
    schema["objects"]["Contact"]["fields"] = [
        field for field in contact_fields if field["name"] != "OtherState"
    ]

    bundle = build_business_plan_bundle(
        _request("Altas de personas con provincia"), schema, semantics
    )

    assert bundle is not None
    assert not bundle.needs_clarification
    province = next(
        field for field in bundle.plans[0].derived_fields if field.label == "Provincia"
    )
    assert province.source_fields == ["npe03__Contact__r.MailingState"]


def test_contact_name_falls_back_to_name_when_first_and_last_are_not_visible() -> None:
    semantics = load_business_semantics(SEMANTICS_PATH)
    schema = _schema()
    contact_fields = schema["objects"]["Contact"]["fields"]
    schema["objects"]["Contact"]["fields"] = [
        field
        for field in contact_fields
        if field["name"] not in {"FirstName", "LastName"}
    ]

    bundle = build_business_plan_bundle(
        _request("Altas de personas con nombre y apellido"), schema, semantics
    )

    assert bundle is not None
    assert not bundle.needs_clarification
    assert "npe03__Contact__r.Name" in bundle.plans[0].selected_fields
    assert any("como fallback" in warning for warning in bundle.warnings)


def test_task_23_generates_exactly_two_main_campaign_business_plans() -> None:
    semantics = load_business_semantics(SEMANTICS_PATH)
    bundle = build_business_plan_bundle(_task_23_request(), _schema(), semantics)

    assert bundle is not None
    assert not bundle.needs_clarification
    assert len(bundle.plans) == 2
    assert {plan.variant_label for plan in bundle.plans} == {
        "[IND] Campañas Pauta Digital",
        "[IND] Redes Sociales",
    }
    assert all(
        plan.primary_object == "npe03__Recurring_Donation__c"
        for plan in bundle.plans
    )
    assert all(
        any(
            item.field == "npe03__Contact__c" and item.operator == "not_null"
            for item in plan.scope_filters
        )
        for plan in bundle.plans
    )
    assert all(
        any(
            item.field == "Campa_a_Principal__c"
            and item.operator == "contains"
            and item.values == [plan.variant_label]
            for item in plan.scope_filters
        )
        for plan in bundle.plans
    )
    assert all(
        plan.date_filter_field == "npe03__Date_Established__c"
        and plan.date_filter_mode == "range"
        for plan in bundle.plans
    )
    assert all(plan.campaign_filter_fields == [] for plan in bundle.plans)
    assert all(
        plan.value_labels["npsp__Status__c"] == {
            "Active": "Activo",
            "Closed": "Cerrado",
        }
        for plan in bundle.plans
    )
    assert all(
        plan.output_order[:6]
        == [
            "npe03__Contact__r.FirstName",
            "npe03__Contact__r.LastName",
            "npe03__Contact__r.Name",
            "npe03__Contact__r.Birthdate",
            "__derived__.age",
            "__derived__.province",
        ]
        for plan in bundle.plans
    )
    assert all(
        not any(
            item.field
            in {
                "npe03__Recurring_Donation_Campaign__c",
                "Campa_a_de_recupero__c",
            }
            for item in plan.scope_filters
        )
        for plan in bundle.plans
    )


def test_non_filterable_main_campaign_needs_clarification_without_technical_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    semantics = load_business_semantics(SEMANTICS_PATH)
    schema = _schema()
    campaign_field = next(
        field
        for field in schema["objects"]["npe03__Recurring_Donation__c"]["fields"]
        if field["name"] == "Campa_a_Principal__c"
    )
    campaign_field["filterable"] = False

    def fail_technical_fallback(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("No debe invocarse el planner técnico")

    monkeypatch.setattr(
        graph_nodes_module, "build_report_plan_bundle", fail_technical_fallback
    )
    nodes = ReportGraphNodes(
        SimpleNamespace(
            settings=SimpleNamespace(allow_report_without_person_fields=False)
        )
    )

    result = nodes.build_report_plan(
        {
            "request": _task_23_request(),
            "schema_snapshot": schema,
            "business_semantics": semantics,
            "warnings": [],
        }
    )

    bundle = result["plan_bundle"]
    assert bundle is not None
    assert bundle.needs_clarification
    assert all(
        plan.primary_object == "npe03__Recurring_Donation__c" for plan in bundle.plans
    )
    assert bundle.clarification_questions == [
        "La dimensión Campaña Principal de Origen existe en el reporte Salesforce, "
        "pero no está disponible/filtrable por SOQL con el usuario actual."
    ]
    assert all(
        not any(item.field == "Campa_a_Principal__c" for item in plan.scope_filters)
        for plan in bundle.plans
    )
