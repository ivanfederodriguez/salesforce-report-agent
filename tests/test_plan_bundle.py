from sf_report_agent.models.report_request import SalesforceReportRequest
from sf_report_agent.salesforce.schema import build_report_plan_bundle


def _snapshot() -> dict[str, object]:
    return {
        "field_mapping": {
            "donation": {
                "object": "npe03__Recurring_Donation__c",
                "date_field": "npe03__Date_Established__c",
                "fields": {
                    "fecha_alta": "npe03__Date_Established__c",
                    "fecha_establecida": "npe03__Date_Established__c",
                    "monto": "npe03__Amount__c",
                    "campaña_origen": "Campa_a_de_origen__c",
                    "campaña": "npe03__Recurring_Donation_Campaign__c",
                },
            }
        },
        "objects": {
            "npe03__Recurring_Donation__c": {
                "fields": [
                    {"name": "Id", "label": "ID", "type": "id"},
                    {
                        "name": "npe03__Date_Established__c",
                        "label": "Fecha establecida",
                        "type": "date",
                    },
                    {
                        "name": "npe03__Amount__c",
                        "label": "Importe",
                        "type": "currency",
                    },
                    {
                        "name": "Campa_a_de_origen__c",
                        "label": "Campaña de origen",
                        "type": "reference",
                        "referenceTo": ["Campaign"],
                        "relationshipName": "Campa_a_de_origen__r",
                    },
                    {
                        "name": "npe03__Recurring_Donation_Campaign__c",
                        "label": "Campaña para las donaciones futuras",
                        "type": "reference",
                        "referenceTo": ["Campaign"],
                        "relationshipName": "npe03__Recurring_Donation_Campaign__r",
                    },
                ]
            }
        },
        "warnings": [],
    }


def test_metadata_planner_uses_established_date_and_generates_safe_campaign_variants() -> None:
    request = SalesforceReportRequest(
        task_id=987,
        report_type="altas_por_campaña",
        year=2026,
        campaign_ids=["7011W000001buEh"],
        donation_fields=["monto"],
    )

    bundle = build_report_plan_bundle(request, _snapshot())

    assert bundle.task_id == 987
    assert bundle.needs_clarification is False
    assert len(bundle.plans) == 3
    assert {plan.variant_id for plan in bundle.plans} == {
        "campana_de_origen",
        "campana_para_las_donaciones_futuras",
        "combined",
    }
    assert all(plan.date_filter_field == "npe03__Date_Established__c" for plan in bundle.plans)
    assert all("CampaignId" not in plan.selected_fields for plan in bundle.plans)
    assert all("CloseDate" not in plan.selected_fields for plan in bundle.plans)
    assert bundle.ambiguity_note is not None


def test_metadata_planner_requires_scope_and_period_when_query_would_be_too_broad() -> None:
    request = SalesforceReportRequest(task_id=42, report_type="altas_por_campaña")

    bundle = build_report_plan_bundle(request, _snapshot())

    assert bundle.needs_clarification is True
    assert any("campañas o fuentes" in question for question in bundle.clarification_questions)
    assert any("año o período" in question for question in bundle.clarification_questions)
