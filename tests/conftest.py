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
            "Opportunity": ["Amount", "StageName", "CloseDate", "CampaignId"],
        }
        names = common + object_fields.get(object_name, [])
        return {
            "fields": [
                {"name": name, "label": name, "type": "string", "referenceTo": []}
                for name in names
            ]
        }

    def test_query(self, object_name: str) -> bool:
        return True

    def get_campaigns_by_ids(self, campaign_ids: list[str]) -> list[dict[str, Any]]:
        return [{"Id": value, "Name": f"Campaign {index}"} for index, value in enumerate(campaign_ids)]

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
            },
            {
                "attributes": {"type": "Opportunity"},
                "Id": "006000000000002",
                "CloseDate": "2026-03-20",
                "StageName": "Activa",
                "Amount": 2000,
                "CampaignId": self.campaign_ids[1],
            },
        ]


@pytest.fixture
def fake_salesforce() -> FakeSalesforceClient:
    return FakeSalesforceClient()
