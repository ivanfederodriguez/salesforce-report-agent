from datetime import date
from typing import Literal

from pydantic import BaseModel, Field


def _default_output_formats() -> list[Literal["csv", "xlsx"]]:
    return ["csv", "xlsx"]


class SalesforceReportRequest(BaseModel):
    task_id: int
    report_type: str
    year: int | None = None
    date_from: date | None = None
    date_to: date | None = None
    campaign_ids: list[str] = Field(default_factory=list)
    campaign_names: list[str] = Field(default_factory=list)
    origin_sources: list[str] = Field(default_factory=list)
    person_fields: list[str] = Field(default_factory=list)
    donation_fields: list[str] = Field(default_factory=list)
    output_formats: list[Literal["csv", "xlsx"]] = Field(default_factory=_default_output_formats)
    missing_information: list[str] = Field(default_factory=list)
