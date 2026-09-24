from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.agent.state import IntentType


class SerializedMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: str
    content: str


class ThreadStateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    thread_id: str
    latest_user_message: str | None = None
    intent: IntentType | None = None
    intent_reason: str | None = None
    response_text: str | None = None
    status: str | None = None
    messages: list[SerializedMessage] = Field(default_factory=list)
