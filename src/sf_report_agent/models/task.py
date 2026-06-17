from typing import Any

from pydantic import BaseModel, Field


class ExternalTask(BaseModel):
    id: int
    created_at: str | None = None
    sender_label: str | None = None
    conversation_label: str | None = None
    requested_action: str | None = None
    public_request_text: str | None = None
    category: str | None = None
    priority: str | None = None
    status: str | None = None
    classification_json: dict[str, Any] = Field(default_factory=dict)
    message_links: list[dict[str, Any]] = Field(default_factory=list)

