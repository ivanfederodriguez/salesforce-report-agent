from __future__ import annotations

import unicodedata
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field


def fold_text(value: str) -> str:
    return "".join(
        char
        for char in unicodedata.normalize("NFKD", value.casefold())
        if not unicodedata.combining(char)
    )


def contains_term(text: str, terms: list[str]) -> bool:
    folded = fold_text(text)
    return any(fold_text(term) in folded for term in terms)


class SemanticValue(BaseModel):
    value: str
    terms: list[str] = Field(default_factory=list)


class SemanticConcept(BaseModel):
    field: str
    label: str
    terms: list[str] = Field(default_factory=list)
    values: dict[str, SemanticValue] = Field(default_factory=dict)


class ContactPolicy(BaseModel):
    lookup_field: str
    relationship: str
    required_terms: list[str] = Field(default_factory=list)
    missing_policy: Literal["exclude", "warn", "clarify"] = "clarify"


class BusinessEntity(BaseModel):
    object: str
    description: str
    priority: int = 0
    terms: list[str] = Field(default_factory=list)
    concepts: dict[str, SemanticConcept] = Field(default_factory=dict)
    contact: ContactPolicy | None = None


class ConditionalFieldGroup(BaseModel):
    request_fields: list[str] = Field(default_factory=list)
    terms: list[str] = Field(default_factory=list)
    fields: list[str] = Field(default_factory=list)
    derived_fields: list[str] = Field(default_factory=list)


class ReportProfile(BaseModel):
    entity: str
    terms: list[str] = Field(default_factory=list)
    fields: list[str] = Field(default_factory=list)
    derived_fields: list[str] = Field(default_factory=list)
    conditional_field_groups: list[ConditionalFieldGroup] = Field(default_factory=list)
    requires_contact: bool = False


class DerivedFieldDefinition(BaseModel):
    output_field: str
    label: str
    kind: Literal["age_years", "first_non_empty"]
    source_fields: list[str]
    terms: list[str] = Field(default_factory=list)


class CampaignDimension(BaseModel):
    entity: str
    field: str
    label: str
    terms: list[str] = Field(default_factory=list)


class CampaignGroup(BaseModel):
    value: str
    aliases: list[str] = Field(default_factory=list)


class PlanningPolicies(BaseModel):
    variants_for_technical_lookups: bool = False
    combined_campaign_variant: bool = False


class BusinessSemantics(BaseModel):
    version: int = 1
    entities: dict[str, BusinessEntity]
    report_profiles: dict[str, ReportProfile] = Field(default_factory=dict)
    derived_fields: dict[str, DerivedFieldDefinition] = Field(default_factory=dict)
    campaign_dimensions: dict[str, CampaignDimension] = Field(default_factory=dict)
    campaign_groups: list[CampaignGroup] = Field(default_factory=list)
    policies: PlanningPolicies = Field(default_factory=PlanningPolicies)

    def select_entity(self, text: str) -> tuple[str, BusinessEntity] | None:
        ranked = sorted(
            (
                (
                    sum(1 for term in entity.terms if contains_term(text, [term])),
                    entity.priority,
                    name,
                    entity,
                )
                for name, entity in self.entities.items()
            ),
            reverse=True,
            key=lambda item: (item[0], item[1]),
        )
        if not ranked or ranked[0][0] == 0:
            return None
        return ranked[0][2], ranked[0][3]

    def select_profile(self, entity_name: str, text: str) -> ReportProfile | None:
        candidates = [
            profile
            for profile in self.report_profiles.values()
            if profile.entity == entity_name
        ]
        if not candidates:
            return None
        return max(
            candidates,
            key=lambda profile: sum(
                1 for term in profile.terms if contains_term(text, [term])
            ),
        )

    def campaign_group_values(self, values: list[str]) -> list[str]:
        folded_values = {fold_text(value) for value in values}
        matched: list[str] = []
        for group in self.campaign_groups:
            aliases = {fold_text(group.value), *(fold_text(value) for value in group.aliases)}
            if aliases & folded_values:
                matched.append(group.value)
        return list(dict.fromkeys(matched))


def load_business_semantics(path: Path) -> BusinessSemantics:
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"No se pudo leer BUSINESS_SEMANTICS_PATH={path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("business_semantics debe ser un objeto YAML")
    return BusinessSemantics.model_validate(payload)
