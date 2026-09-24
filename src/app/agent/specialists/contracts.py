from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.agent.state import IntentType


class SpecialistRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    objective: str = Field(min_length=1)
    latest_user_message: str = Field(min_length=1)
    intent: IntentType | None = None
    intent_reason: str | None = None
    conversation_history: list[dict[str, str]] = Field(default_factory=list)


class SpecialistResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str = Field(default="completed", min_length=1)
    summary: str = ""
    response_guidance: str = ""
    extracted_fields: dict[str, Any] = Field(default_factory=dict)
    confidence: float | None = Field(default=None, ge=0, le=1)
    error_code: str | None = None


def failed_specialist_result(error_code: str, *, summary: str = "") -> SpecialistResult:
    return SpecialistResult(
        status="failed",
        summary=summary,
        response_guidance=(
            "Continue without specialist output and ask a concise follow-up if needed."
        ),
        error_code=error_code,
    )


def skipped_specialist_result(reason: str) -> SpecialistResult:
    return SpecialistResult(
        status="skipped",
        summary=reason,
        response_guidance="Continue with the regular responder flow.",
        error_code=reason,
    )
