from typing import Literal

from pydantic import BaseModel, Field


class SalesforceFilter(BaseModel):
    field: str
    operator: Literal["equals", "in", "contains", "not_null"]
    values: list[str] = Field(default_factory=list)
    description: str


class DerivedFieldPlan(BaseModel):
    output_field: str
    label: str
    kind: Literal["age_years", "first_non_empty"]
    source_fields: list[str]


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
    scope_filters: list[SalesforceFilter] = Field(default_factory=list)
    campaign_ids: list[str]
    campaign_filter_fields: list[str] = Field(default_factory=list)
    origin_sources: list[str] = Field(default_factory=list)
    origin_source_field: str | None = None
    origin_sources_resolved_by_campaign_ids: bool = False
    date_filter_field: str | None = None
    date_filter_mode: Literal["calendar_year", "range"] = "calendar_year"
    date_filter_description: str | None = None
    joins_or_relationships: list[str] = Field(default_factory=list)
    derived_fields: list[DerivedFieldPlan] = Field(default_factory=list)
    hidden_fields: list[str] = Field(default_factory=list)
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
