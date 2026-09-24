from __future__ import annotations

from pathlib import Path

from openai import OpenAI, OpenAIError

from app.core.config import Settings, get_settings


class OpenAITranscriptionError(RuntimeError):
    def __init__(self, message: str, *, error_code: str) -> None:
        super().__init__(message)
        self.error_code = error_code


def transcribe_audio_file(
    file_path: Path,
    *,
    settings: Settings | None = None,
) -> str:
    current_settings = settings or get_settings()
    api_key = (current_settings.openai_api_key or "").strip()
    if not api_key:
        raise OpenAITranscriptionError(
            "OPENAI_API_KEY is required to transcribe inbound audio.",
            error_code="openai_api_key_missing",
        )

    client = OpenAI(api_key=api_key)
    try:
        with file_path.open("rb") as audio_file:
            transcription = client.audio.transcriptions.create(
                model=current_settings.openai_transcription_model,
                file=audio_file,
            )
    except OpenAIError as exc:
        raise OpenAITranscriptionError(
            f"OpenAI audio transcription failed: {exc}",
            error_code="openai_transcription_error",
        ) from exc

    text = getattr(transcription, "text", transcription)
    if not isinstance(text, str) or not text.strip():
        raise OpenAITranscriptionError(
            "OpenAI audio transcription returned empty text.",
            error_code="openai_transcription_empty",
        )

    return text.strip()
