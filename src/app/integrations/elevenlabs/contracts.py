from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class ElevenLabsSpeechGenerationError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        error_code: str,
        status_code: int | None = None,
        response_body: Any | None = None,
        attempt_count: int = 0,
    ) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.status_code = status_code
        self.response_body = response_body
        self.attempt_count = attempt_count


@dataclass(frozen=True, slots=True)
class ElevenLabsGeneratedSpeech:
    content: bytes
    content_type: str
    output_format: str
    attempt_count: int
    billed_characters: int | None = None
    request_id: str | None = None
    provider_trace_id: str | None = None
    model_id: str | None = None


__all__ = [
    "ElevenLabsGeneratedSpeech",
    "ElevenLabsSpeechGenerationError",
]
