from sf_report_agent.models.task import ExternalTask
from sf_report_agent.salesforce.field_mapper import extract_campaign_ids


def test_extracts_all_campaign_ids(micaela_task: ExternalTask) -> None:
    text = "\n".join(str(link["url"]) for link in micaela_task.message_links)
    assert extract_campaign_ids(text) == [
        "7011W000001buEh",
        "701Pe00000VtQrK",
        "701Pe00000QysD4IAJ",
    ]


def test_deduplicates_campaign_ids() -> None:
    assert extract_campaign_ids("7011W000001buEh 7011W000001buEh") == ["7011W000001buEh"]
