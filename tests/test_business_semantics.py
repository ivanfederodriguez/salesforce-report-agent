from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

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
) -> dict[str, Any]:
    return {
        "name": name,
        "label": label,
        "type": field_type,
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

    assert result["__derived__.age"].tolist() == [26, 36]
    assert result["__derived__.province"].tolist() == ["Santa Fe", "Córdoba"]


def test_main_campaign_dimension_does_not_create_technical_lookup_variants() -> None:
    semantics = load_business_semantics(SEMANTICS_PATH)
    request = SalesforceReportRequest(
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

    bundle = build_business_plan_bundle(request, _schema(), semantics)

    assert bundle is not None
    assert not bundle.needs_clarification
    assert [plan.variant_label for plan in bundle.plans] == [
        "[IND] Campañas Pauta Digital",
        "[IND] Redes Sociales",
    ]
    assert all(
        any(item.field == "Campa_a_Principal__c" for item in plan.scope_filters)
        for plan in bundle.plans
    )
    assert all(
        "npe03__Recurring_Donation_Campaign__c" not in plan.campaign_filter_fields
        and "Campa_a_de_recupero__c" not in plan.campaign_filter_fields
        for plan in bundle.plans
    )
