from pathlib import Path
from typing import Any

from rich.console import Console

from conftest import FakeSalesforceClient
from sf_report_agent.salesforce.ids import normalize_salesforce_id
from sf_report_agent.salesforce.permissions_doctor import (
    FIXTURE_CAMPAIGN_IDS,
    SalesforcePermissionsDoctor,
)


def test_permissions_doctor_checks_objects_and_campaigns(
    tmp_path: Path, fake_salesforce: FakeSalesforceClient
) -> None:
    doctor = SalesforcePermissionsDoctor(fake_salesforce, artifacts_dir=tmp_path)  # type: ignore[arg-type]
    report = doctor.run()
    path = doctor.save(report)

    assert report.login_ok is True
    assert report.api_ok is True
    assert report.describe_global_ok is True
    assert len(report.object_checks) == 9
    assert all(report.campaign_id_checks.values())
    opportunity = next(item for item in report.object_checks if item.object_name == "Opportunity")
    assert opportunity.query_ok is True
    assert "Amount" in opportunity.readable_fields
    assert path.exists()


def test_normalize_salesforce_id_uses_case_insensitive_15_character_key() -> None:
    assert normalize_salesforce_id("7011W000001buEhQAI") == "7011W000001BUEH"
    assert normalize_salesforce_id("7011w000001bueh") == "7011W000001BUEH"


def test_permissions_doctor_matches_requested_15_character_id_to_returned_18_character_id(
    tmp_path: Path,
) -> None:
    class SalesforceReturningLongIds(FakeSalesforceClient):
        def get_campaigns_by_ids(self, campaign_ids: list[str]) -> list[dict[str, Any]]:
            return [
                {"Id": "7011W000001buEhQAI", "Name": "Campaña 1"},
                {"Id": "701Pe00000VtQrKIAV", "Name": "Campaña 2"},
                {"Id": "701Pe00000QysD4IAJ", "Name": "Campaña 3"},
            ]

    doctor = SalesforcePermissionsDoctor(  # type: ignore[arg-type]
        SalesforceReturningLongIds(), artifacts_dir=tmp_path
    )
    report = doctor.run()

    assert all(report.campaign_id_checks.values())
    assert report.campaign_id_matches == {
        FIXTURE_CAMPAIGN_IDS[0]: "7011W000001buEhQAI",
        FIXTURE_CAMPAIGN_IDS[1]: "701Pe00000VtQrKIAV",
        FIXTURE_CAMPAIGN_IDS[2]: "701Pe00000QysD4IAJ",
    }

    console = Console(record=True, width=120)
    doctor.print_report(report, console)
    rendered = console.export_text()
    assert "7011W000001buEh" in rendered
    assert "7011W000001buEhQAI" in rendered
