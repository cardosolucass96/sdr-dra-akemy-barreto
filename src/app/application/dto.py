from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from app.agent.state import IntentType
from app.integrations.pipefacil import PipefacilDeliveryErrorCode, PipefacilDeliveryStatus


@dataclass(frozen=True, slots=True)
class SerializedMessageResult:
    role: str
    content: str


ResponsePartType = Literal["text", "image", "video", "audio", "document"]


@dataclass(frozen=True, slots=True)
class ResponsePartResult:
    type: ResponsePartType
    text: str | None = None
    media_id: str | None = None
    caption: str | None = None
    content_type: str | None = None
    filename: str | None = None


@dataclass(frozen=True, slots=True)
class ResponseAudioResult:
    text: str
    reason: str


@dataclass(frozen=True, slots=True)
class ChatTurnResult:
    thread_id: str
    intent: IntentType | None
    intent_reason: str | None
    response_text: str
    status: str
    response_messages: list[str] = field(default_factory=list)
    response_parts: list[ResponsePartResult] = field(default_factory=list)
    delivery_status: PipefacilDeliveryStatus | None = None
    delivery_error: PipefacilDeliveryErrorCode | None = None
    response_audio: ResponseAudioResult | None = None


@dataclass(frozen=True, slots=True)
class ThreadStateResult:
    thread_id: str
    latest_user_message: str | None = None
    intent: IntentType | None = None
    intent_reason: str | None = None
    response_text: str | None = None
    status: str | None = None
    messages: list[SerializedMessageResult] = field(default_factory=list)
