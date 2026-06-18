from pydantic import BaseModel, Field


class SalesforceReportPlan(BaseModel):
    task_id: int
    variant_id: str = "default"
    variant_label: str = "Reporte"
    ambiguity_reason: str | None = None
    title: str
    description: str
    primary_object: str
    selected_fields: list[str]
    filters: list[str]
    campaign_ids: list[str]
    campaign_filter_fields: list[str] = Field(default_factory=list)
    origin_sources: list[str] = Field(default_factory=list)
    origin_source_field: str | None = None
    origin_sources_resolved_by_campaign_ids: bool = False
    date_filter_field: str | None = None
    date_filter_description: str | None = None
    joins_or_relationships: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    needs_clarification: bool = False
    clarification_questions: list[str] = Field(default_factory=list)


class SalesforceReportPlanBundle(BaseModel):
    task_id: int
    plans: list[SalesforceReportPlan] = Field(default_factory=list)
    ambiguity_note: str | None = None
    needs_clarification: bool = False
    clarification_questions: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
