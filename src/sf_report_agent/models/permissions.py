from datetime import datetime

from pydantic import BaseModel, Field


class SalesforceObjectPermissionCheck(BaseModel):
    object_name: str
    exists: bool
    describe_ok: bool
    query_ok: bool
    readable_fields: list[str] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)
    error: str | None = None


class SalesforcePermissionReport(BaseModel):
    login_ok: bool
    api_ok: bool
    describe_global_ok: bool
    checked_at: datetime
    username: str | None = None
    instance_url: str | None = None
    object_checks: list[SalesforceObjectPermissionCheck]
    campaign_id_checks: dict[str, bool] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    recommended_salesforce_permissions: list[str] = Field(default_factory=list)

