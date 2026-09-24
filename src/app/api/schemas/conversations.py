from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.api.schemas.chat import ChatResponse


class ConversationResumeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    thread_id: str = Field(min_length=1, max_length=255)
    deal_seq: int | None = Field(default=None, ge=1)
    deal_id: str | None = Field(default=None, min_length=1, max_length=255)
    contact_id: str | None = Field(default=None, min_length=1, max_length=255)
    channel_id: str | None = Field(default=None, min_length=1, max_length=255)
    recipient_phone: str | None = Field(default=None, min_length=1, max_length=64)
    sender_phone_number_id: str | None = Field(default=None, min_length=1, max_length=255)
    profile_name: str | None = Field(default=None, min_length=1, max_length=255)
    context: str | None = Field(default=None, max_length=4000)
    send_response: bool = True
    history_limit: int = Field(default=100, ge=1, le=500)

    @model_validator(mode="after")
    def require_history_identifier(self) -> Any:
        if not any((self.deal_seq, self.deal_id, self.contact_id, self.channel_id)):
            raise ValueError(
                "At least one of deal_seq, deal_id, contact_id, or channel_id is required."
            )
        return self


class ConversationResumeResponse(ChatResponse):
    history_message_count: int
    history_source: str = "pipefacil"
