from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.agent.state import IntentType
from app.application.dto import ResponsePartType
from app.integrations.pipefacil import PipefacilDeliveryErrorCode, PipefacilDeliveryStatus


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    thread_id: str = Field(min_length=1, max_length=255)
    message: str = Field(min_length=1)
    user_id: str | None = None
    metadata: dict[str, Any] | None = None


class ResponsePart(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: ResponsePartType
    text: str | None = None
    media_id: str | None = None
    caption: str | None = None
    content_type: str | None = None
    filename: str | None = None


class ChatResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    thread_id: str
    intent: IntentType | None = None
    intent_reason: str | None = None
    response_text: str
    response_messages: list[str] = Field(default_factory=list)
    response_parts: list[ResponsePart] = Field(default_factory=list)
    status: str
    delivery_status: PipefacilDeliveryStatus | None = None
    delivery_error: PipefacilDeliveryErrorCode | None = None
