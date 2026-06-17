from pydantic import BaseModel, Field


class SalesforceReportPlan(BaseModel):
    task_id: int
    title: str
    description: str
    primary_object: str
    selected_fields: list[str]
    filters: list[str]
    campaign_ids: list[str]
    date_filter_description: str | None = None
    joins_or_relationships: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    needs_clarification: bool = False
    clarification_questions: list[str] = Field(default_factory=list)

