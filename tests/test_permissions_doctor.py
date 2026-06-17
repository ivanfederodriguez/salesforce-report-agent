from pathlib import Path

from conftest import FakeSalesforceClient
from sf_report_agent.salesforce.permissions_doctor import SalesforcePermissionsDoctor


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
