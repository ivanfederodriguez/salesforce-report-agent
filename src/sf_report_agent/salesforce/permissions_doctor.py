from __future__ import annotations

import json
import unicodedata
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.table import Table

from sf_report_agent.models.permissions import (
    SalesforceObjectPermissionCheck,
    SalesforcePermissionReport,
)
from sf_report_agent.salesforce.client import SalesforceClient, SalesforceClientError
from sf_report_agent.salesforce.schema import CANDIDATE_OBJECTS

FIXTURE_CAMPAIGN_IDS = ["7011W000001buEh", "701Pe00000VtQrK", "701Pe00000QysD4IAJ"]
PERSON_CANDIDATES = {
    "Name",
    "FirstName",
    "LastName",
    "Birthdate",
    "MailingCity",
    "MailingState",
    "MailingCountry",
    "OtherCity",
    "OtherState",
    "OtherCountry",
}
DONATION_CANDIDATES = {
    "Amount",
    "StageName",
    "CloseDate",
    "CampaignId",
    "CreatedDate",
    "StartDate",
    "EndDate",
    "Status",
}
CUSTOM_KEYWORDS = (
    "fecha",
    "monto",
    "estado",
    "finalizacion",
    "campana",
    "campaign",
    "donacion",
    "donation",
)


def _fold(value: str) -> str:
    return "".join(
        char
        for char in unicodedata.normalize("NFKD", value.casefold())
        if not unicodedata.combining(char)
    )


class SalesforcePermissionsDoctor:
    def __init__(self, client: SalesforceClient, *, artifacts_dir: Path) -> None:
        self.client = client
        self.artifacts_dir = artifacts_dir

    def run(self) -> SalesforcePermissionReport:
        warnings: list[str] = []
        checks: list[SalesforceObjectPermissionCheck] = []
        try:
            global_description = self.client.describe_global()
            describe_global_ok = True
            api_ok = True
        except SalesforceClientError as exc:
            global_description = {}
            describe_global_ok = False
            api_ok = False
            warnings.append(str(exc))

        available = {
            str(item["name"])
            for item in global_description.get("sobjects", [])
            if isinstance(item, dict) and item.get("name")
        }
        for object_name in CANDIDATE_OBJECTS:
            checks.append(self._check_object(object_name, object_name in available))

        campaign_id_checks = dict.fromkeys(FIXTURE_CAMPAIGN_IDS, False)
        try:
            for record in self.client.get_campaigns_by_ids(FIXTURE_CAMPAIGN_IDS):
                record_id = str(record.get("Id", ""))
                if record_id in campaign_id_checks:
                    campaign_id_checks[record_id] = True
        except SalesforceClientError as exc:
            warnings.append(f"No se pudieron verificar las campañas del fixture: {exc}")

        recommendations = self._recommend(checks, campaign_id_checks, describe_global_ok)
        return SalesforcePermissionReport(
            login_ok=True,
            api_ok=api_ok,
            describe_global_ok=describe_global_ok,
            checked_at=datetime.now(UTC),
            username=self.client.username,
            instance_url=self.client.instance_url,
            object_checks=checks,
            campaign_id_checks=campaign_id_checks,
            warnings=warnings,
            recommended_salesforce_permissions=recommendations,
        )

    def _check_object(self, object_name: str, exists: bool) -> SalesforceObjectPermissionCheck:
        if not exists:
            return SalesforceObjectPermissionCheck(
                object_name=object_name,
                exists=False,
                describe_ok=False,
                query_ok=False,
                error="Objeto no disponible para el usuario",
            )
        try:
            description = self.client.describe_object(object_name)
            field_items = description.get("fields", [])
            all_fields = {
                str(item["name"])
                for item in field_items
                if isinstance(item, dict) and item.get("name")
            }
            custom_candidates = {
                name
                for name in all_fields
                if name.endswith("__c") and any(word in _fold(name) for word in CUSTOM_KEYWORDS)
            }
            expected = PERSON_CANDIDATES | DONATION_CANDIDATES | custom_candidates
            readable = sorted(all_fields & expected)
            missing = sorted((PERSON_CANDIDATES | DONATION_CANDIDATES) - all_fields)
            self.client.test_query(object_name)
            return SalesforceObjectPermissionCheck(
                object_name=object_name,
                exists=True,
                describe_ok=True,
                query_ok=True,
                readable_fields=readable,
                missing_fields=missing,
            )
        except SalesforceClientError as exc:
            return SalesforceObjectPermissionCheck(
                object_name=object_name,
                exists=True,
                describe_ok=False,
                query_ok=False,
                error=str(exc),
            )

    @staticmethod
    def _recommend(
        checks: list[SalesforceObjectPermissionCheck],
        campaigns: dict[str, bool],
        describe_global_ok: bool,
    ) -> list[str]:
        recommendations: list[str] = []
        if not describe_global_ok:
            recommendations.append("Habilitar el permiso de sistema API Enabled.")
        by_name = {check.object_name: check for check in checks}
        for object_name in ("Campaign", "CampaignMember", "Contact", "Account"):
            check = by_name[object_name]
            if not check.exists or not check.query_ok:
                recommendations.append(f"Conceder acceso read al objeto {object_name}.")
        donation_objects = (
            "Opportunity",
            "npe03__Recurring_Donation__c",
            "Recurring_Donation__c",
            "npe01__OppPayment__c",
            "Payment",
        )
        if not any(by_name[name].query_ok for name in donation_objects):
            recommendations.append("Conceder acceso read al objeto real de altas/donaciones.")
        if any(check.missing_fields for check in checks if check.exists):
            recommendations.append(
                "Hacer visibles por Field-Level Security los campos personales y de donación necesarios."
            )
        if not all(campaigns.values()):
            recommendations.append("Conceder acceso de lectura a los registros de campañas indicados.")
        recommendations.append("No conceder permisos de escritura: el MVP solo requiere lectura.")
        return list(dict.fromkeys(recommendations))

    def save(self, report: SalesforcePermissionReport) -> Path:
        directory = self.artifacts_dir / "permission_reports"
        directory.mkdir(parents=True, exist_ok=True)
        timestamp = report.checked_at.strftime("%Y%m%dT%H%M%SZ")
        path = directory / f"sf_permission_report_{timestamp}.json"
        path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
        return path

    @staticmethod
    def print_report(report: SalesforcePermissionReport, console: Console | None = None) -> None:
        output = console or Console()
        table = Table(title="Salesforce permission doctor")
        for heading in ("Objeto", "Existe", "Describe", "SELECT", "Campos visibles", "Error"):
            table.add_column(heading)
        for check in report.object_checks:
            table.add_row(
                check.object_name,
                "sí" if check.exists else "no",
                "sí" if check.describe_ok else "no",
                "sí" if check.query_ok else "no",
                str(len(check.readable_fields)),
                check.error or "",
            )
        output.print(table)
        output.print_json(json.dumps(report.campaign_id_checks, ensure_ascii=False))
        if report.recommended_salesforce_permissions:
            output.print("[bold]Recomendaciones:[/bold]")
            for recommendation in report.recommended_salesforce_permissions:
                output.print(f"- {recommendation}")

