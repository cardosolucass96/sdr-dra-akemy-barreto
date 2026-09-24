from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.agent.state import IntentType


class IntentClassification(BaseModel):
    intent: IntentType
    reason: str = Field(min_length=1)
    requires_specialist: bool = False
    specialist_name: str | None = None
    specialist_reason: str | None = None


class LianaLeadProgress(BaseModel):
    explicit_interest: bool
    stated_objective: str | None
    appointment_requested: bool
    preferred_period: str | None
    confidence: float = Field(ge=0, le=1)


class OpenAILianaLeadProgress(BaseModel):
    explicit_interest: bool
    stated_objective: str | None
    appointment_requested: bool
    preferred_period: str | None
    confidence: float


class OutboundMediaChoice(BaseModel):
    media_id: str = Field(min_length=1)
    caption: str | None = None
    reason: str = Field(min_length=1)


class OutboundMediaClassification(BaseModel):
    """Validated decision to use, or not use, one catalog media item."""

    action: Literal["send", "none"]
    media_id: str | None = None
    reason: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_media_id_for_action(self) -> OutboundMediaClassification:
        has_media_id = bool(self.media_id and self.media_id.strip())
        if self.action == "send" and not has_media_id:
            raise ValueError("media_id is required when action is 'send'.")
        if self.action == "none" and self.media_id is not None:
            raise ValueError("media_id must be null when action is 'none'.")
        return self


class GeneratedAudioChoice(BaseModel):
    text: str = Field(
        min_length=1,
        max_length=1600,
        description=("Spoken explanation only. Exact or copyable facts belong in response_text."),
    )
    reason: str = Field(
        min_length=1,
        description="Why spoken audio is more useful than text for this part of the reply.",
    )


class AgentResponsePlan(BaseModel):
    response_text: str = Field(
        min_length=1,
        description=(
            "WhatsApp text reply, including all exact, scannable, or copyable information."
        ),
    )
    media_choices: list[OutboundMediaChoice] = Field(default_factory=list)
    generated_audio: GeneratedAudioChoice | None = Field(
        default=None,
        description=(
            "Optional spoken explanation. Leave empty for text-only replies; combine with "
            "response_text for hybrid replies."
        ),
    )


# GPT-5.6 native JSON Schema output requires every property to be required and
# does not accept application-only defaults or min/max constraints. These wire
# schemas keep that provider contract separate from Pipefacil's app validation.
class OpenAIIntentClassification(BaseModel):
    intent: IntentType
    reason: str
    requires_specialist: bool
    specialist_name: str | None
    specialist_reason: str | None


class OpenAIOutboundMediaChoice(BaseModel):
    media_id: str
    caption: str | None
    reason: str


class OpenAIOutboundMediaClassification(BaseModel):
    action: Literal["send", "none"]
    media_id: str | None
    reason: str


class OpenAIGeneratedAudioChoice(BaseModel):
    text: str
    reason: str


class OpenAIAgentResponsePlan(BaseModel):
    response_text: str
    media_choices: list[OpenAIOutboundMediaChoice]
    generated_audio: OpenAIGeneratedAudioChoice | None
