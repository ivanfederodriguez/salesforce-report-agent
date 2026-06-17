import json
from pathlib import Path

from conftest import FakeSalesforceClient
from sf_report_agent.config import Settings
from sf_report_agent.main import command_inspect_schema


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        source_db_path=tmp_path / "source.db",
        worker_db_path=tmp_path / "worker.db",
        artifacts_dir=tmp_path / "artifacts",
        field_mapping_path=None,
        model_provider="ollama",
        ollama_model="test",
        ollama_base_url="http://127.0.0.1:11434",
        ollama_temperature=0,
        salesforce_username=None,
        salesforce_password=None,
        salesforce_security_token=None,
        salesforce_domain="login",
        sf_read_only=True,
        max_export_rows=100,
        require_human_approval_for_pii=True,
        log_pii=False,
        update_source_task=False,
        allow_report_without_person_fields=False,
    )


def test_inspect_schema_with_fake_salesforce_saves_filtered_artifact(tmp_path: Path) -> None:
    client = FakeSalesforceClient()

    status = command_inspect_schema(
        _settings(tmp_path),
        object_name="Opportunity",
        filter_text="campaign",
        client=client,
    )

    assert status == 0
    paths = list((tmp_path / "artifacts" / "schema").glob("Opportunity_describe_*.json"))
    assert len(paths) == 1
    payload = json.loads(paths[0].read_text(encoding="utf-8"))
    assert payload["object"] == "Opportunity"
    assert payload["filter"] == "campaign"
    assert payload["visible_field_count"] > payload["matched_field_count"]
    assert payload["fields"] == [
        {
            "label": "CampaignId",
            "name": "CampaignId",
            "type": "reference",
            "referenceTo": ["Campaign"],
        }
    ]
    assert client.queried_soql == []
