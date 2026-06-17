from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from sf_report_agent.models.task import ExternalTask


@pytest.fixture
def fixture_dir() -> Path:
    return Path(__file__).parent / "fixtures"


@pytest.fixture
def micaela_task(fixture_dir: Path) -> ExternalTask:
    payload = json.loads((fixture_dir / "micaela_salesforce_task.json").read_text(encoding="utf-8"))
    return ExternalTask.model_validate(payload)


def create_source_database(path: Path, task: ExternalTask) -> None:
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE tasks (
                id INTEGER PRIMARY KEY,
                created_at TEXT,
                channel_id TEXT,
                message_ts TEXT,
                user_id TEXT,
                sender_label TEXT,
                conversation_label TEXT,
                summary TEXT,
                requested_action TEXT,
                priority TEXT,
                category TEXT,
                status TEXT,
                classification_json TEXT,
                public_request_text TEXT,
                thread_ts TEXT,
                requester_label TEXT,
                updated_at TEXT
            );
            CREATE TABLE message_links (
                id INTEGER PRIMARY KEY,
                channel_id TEXT,
                message_ts TEXT,
                url TEXT,
                url_type TEXT,
                title TEXT,
                metadata_json TEXT
            );
            """
        )
        connection.execute(
            """
            INSERT INTO tasks (
                id, created_at, channel_id, message_ts, user_id, sender_label,
                conversation_label, summary, requested_action, priority, category,
                status, classification_json, public_request_text, thread_ts,
                requester_label, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task.id,
                task.created_at,
                "D123",
                "1718600000.000100",
                "U123",
                task.sender_label,
                task.conversation_label,
                task.classification_json.get("summary"),
                task.requested_action,
                task.priority,
                task.category,
                task.status,
                json.dumps(task.classification_json, ensure_ascii=False),
                task.public_request_text,
                "1718600000.000100",
                task.sender_label,
                task.created_at,
            ),
        )
        for index, link in enumerate(task.message_links, start=1):
            connection.execute(
                """
                INSERT INTO message_links(
                    id, channel_id, message_ts, url, url_type, title, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    index,
                    "D123",
                    "1718600000.000100",
                    link["url"],
                    link["url_type"],
                    link["title"],
                    json.dumps(link["metadata_json"], ensure_ascii=False),
                ),
            )


def write_field_mapping(
    path: Path,
    *,
    include_relationship: bool = True,
    include_origin: bool = True,
) -> Path:
    payload: dict[str, Any] = {
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
                "fecha_de_finalizacion": "EndDate__c",
                "campaña": "CampaignId",
                "campaña_origen": "LeadSource" if include_origin else None,
            },
        },
    }
    if include_relationship:
        payload["relationships"] = {"person_from_donation": "Contact"}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


class FakeSalesforceClient:
    username = "report-user@example.org"
    instance_url = "https://example.my.salesforce.com"

    def __init__(self) -> None:
        self.campaign_ids = ["7011W000001buEh", "701Pe00000VtQrK", "701Pe00000QysD4IAJ"]
        self.queried_soql: list[str] = []

    def describe_global(self) -> dict[str, Any]:
        names = [
            "Campaign",
            "CampaignMember",
            "Contact",
            "Account",
            "Opportunity",
            "npe03__Recurring_Donation__c",
            "Recurring_Donation__c",
            "npe01__OppPayment__c",
            "Payment",
        ]
        return {"sobjects": [{"name": name} for name in names]}

    def describe_object(self, object_name: str) -> dict[str, Any]:
        common = ["Id", "Name", "CreatedDate", "Status"]
        object_fields = {
            "Contact": [
                "FirstName",
                "LastName",
                "Birthdate",
                "MailingCity",
                "MailingState",
                "MailingCountry",
                "OtherCity",
                "OtherState",
                "OtherCountry",
            ],
            "Opportunity": [
                "Amount",
                "StageName",
                "CloseDate",
                "CampaignId",
                "LeadSource",
                "ContactId",
                "EndDate__c",
            ],
        }
        names = common + object_fields.get(object_name, [])
        return {
            "fields": [
                {
                    "name": name,
                    "label": name,
                    "type": "reference" if name in {"CampaignId", "ContactId"} else "string",
                    "referenceTo": ["Campaign"]
                    if name == "CampaignId"
                    else (["Contact"] if name == "ContactId" else []),
                    "relationshipName": "Campaign"
                    if name == "CampaignId"
                    else ("Contact" if name == "ContactId" else None),
                }
                for name in names
            ]
        }

    def test_query(self, object_name: str) -> bool:
        return True

    def get_campaigns_by_ids(self, campaign_ids: list[str]) -> list[dict[str, Any]]:
        return [
            {"Id": value, "Name": f"Campaign {index}"} for index, value in enumerate(campaign_ids)
        ]

    def query_all(self, soql: str) -> list[dict[str, Any]]:
        self.queried_soql.append(soql)
        return [
            {
                "attributes": {"type": "Opportunity"},
                "Id": "006000000000001",
                "CloseDate": "2026-02-15",
                "StageName": "Activa",
                "Amount": 1500,
                "CampaignId": self.campaign_ids[0],
                "LeadSource": "amplify",
                "EndDate__c": None,
                "Contact": {
                    "Name": "Persona Uno",
                    "Birthdate": "1990-01-01",
                    "MailingCity": "Córdoba",
                    "MailingState": "Córdoba",
                    "MailingCountry": "Argentina",
                },
            },
            {
                "attributes": {"type": "Opportunity"},
                "Id": "006000000000002",
                "CloseDate": "2026-03-20",
                "StageName": "Activa",
                "Amount": 2000,
                "CampaignId": self.campaign_ids[1],
                "LeadSource": "orgánico web",
                "EndDate__c": None,
                "Contact": {
                    "Name": "Persona Dos",
                    "Birthdate": "1985-05-10",
                    "MailingCity": "Rosario",
                    "MailingState": "Santa Fe",
                    "MailingCountry": "Argentina",
                },
            },
        ]


@pytest.fixture
def fake_salesforce() -> FakeSalesforceClient:
    return FakeSalesforceClient()
