from __future__ import annotations

import time
from typing import Any

import httpx

from app.core.config import Settings, get_settings
from app.integrations.elevenlabs.contracts import (
    ElevenLabsGeneratedSpeech,
    ElevenLabsSpeechGenerationError,
)
from app.observability import observe_span

ELEVENLABS_TTS_STREAM_PATH = "/v1/text-to-speech/{voice_id}/stream"
OPUS_OUTPUT_FORMAT = "opus_48000_96"
OGG_OPUS_CONTENT_TYPE = "audio/ogg"
_OGG_CONTENT_TYPES = {"audio/ogg", "audio/opus", "application/ogg"}


def generate_elevenlabs_speech(
    *,
    text: str,
    settings: Settings | None = None,
    client: httpx.Client | None = None,
) -> ElevenLabsGeneratedSpeech:
    current_settings = settings or get_settings()
    speech_text = text.strip()
    api_key = (current_settings.elevenlabs_api_key or "").strip()
    voice_id = (current_settings.elevenlabs_voice_id or "").strip()

    if not api_key:
        raise ElevenLabsSpeechGenerationError(
            "ELEVENLABS_API_KEY is required to generate outbound audio.",
            error_code="elevenlabs_api_key_missing",
        )

    if not voice_id:
        raise ElevenLabsSpeechGenerationError(
            "ELEVENLABS_VOICE_ID is required to generate outbound audio.",
            error_code="elevenlabs_voice_id_missing",
        )

    if not speech_text:
        raise ElevenLabsSpeechGenerationError(
            "Audio text cannot be empty.",
            error_code="audio_text_empty",
        )

    owns_client = client is None
    current_client = client or _build_client(current_settings)
    output_format = current_settings.elevenlabs_output_format.strip() or OPUS_OUTPUT_FORMAT
    model_id = current_settings.elevenlabs_model_id.strip() or "eleven_v3"
    max_attempts = current_settings.elevenlabs_max_attempts

    try:
        with observe_span(
            name="generate-elevenlabs-speech",
            as_type="generation",
            input={"text": speech_text, "text_length": len(speech_text)},
            metadata=_observation_metadata(
                voice_id=voice_id,
                output_format=output_format,
                model_id=model_id,
            ),
        ) as observation:
            for attempt_count in range(1, max_attempts + 1):
                try:
                    response = current_client.post(
                        ELEVENLABS_TTS_STREAM_PATH.format(voice_id=voice_id),
                        params={"output_format": output_format},
                        headers={
                            "xi-api-key": api_key,
                            "Accept": (
                                OGG_OPUS_CONTENT_TYPE
                                if output_format == OPUS_OUTPUT_FORMAT
                                else "audio/mpeg"
                            ),
                            "Content-Type": "application/json",
                        },
                        json=_build_tts_payload(speech_text, current_settings),
                    )
                except httpx.HTTPError as exc:
                    if attempt_count < max_attempts:
                        _wait_before_retry(current_settings)
                        continue
                    _update_failed_observation(
                        observation,
                        error_code="elevenlabs_transport_error",
                        attempt_count=attempt_count,
                        voice_id=voice_id,
                        output_format=output_format,
                        model_id=model_id,
                        status_message=str(exc),
                    )
                    raise ElevenLabsSpeechGenerationError(
                        f"ElevenLabs speech generation request failed: {exc}",
                        error_code="elevenlabs_transport_error",
                        attempt_count=attempt_count,
                    ) from exc

                if _is_retryable_response(response) and attempt_count < max_attempts:
                    _wait_before_retry(current_settings)
                    continue

                if not response.is_success:
                    _update_failed_observation(
                        observation,
                        error_code="elevenlabs_upstream_error",
                        attempt_count=attempt_count,
                        voice_id=voice_id,
                        output_format=output_format,
                        model_id=model_id,
                        upstream_status_code=response.status_code,
                    )
                    raise ElevenLabsSpeechGenerationError(
                        "ElevenLabs speech generation returned an error response.",
                        error_code="elevenlabs_upstream_error",
                        status_code=response.status_code,
                        response_body=_safe_json(response),
                        attempt_count=attempt_count,
                    )

                if not response.content:
                    _update_failed_observation(
                        observation,
                        error_code="elevenlabs_audio_empty",
                        attempt_count=attempt_count,
                        voice_id=voice_id,
                        output_format=output_format,
                        model_id=model_id,
                        upstream_status_code=response.status_code,
                    )
                    raise ElevenLabsSpeechGenerationError(
                        "ElevenLabs speech generation returned empty audio.",
                        error_code="elevenlabs_audio_empty",
                        status_code=response.status_code,
                        attempt_count=attempt_count,
                    )

                content_type = _resolve_content_type(response, output_format)
                if not content_type.startswith("audio/"):
                    _update_failed_observation(
                        observation,
                        error_code="elevenlabs_content_type_invalid",
                        attempt_count=attempt_count,
                        voice_id=voice_id,
                        output_format=output_format,
                        model_id=model_id,
                        upstream_status_code=response.status_code,
                    )
                    raise ElevenLabsSpeechGenerationError(
                        "ElevenLabs speech generation returned a non-audio response.",
                        error_code="elevenlabs_content_type_invalid",
                        status_code=response.status_code,
                        attempt_count=attempt_count,
                    )

                if output_format == OPUS_OUTPUT_FORMAT:
                    if content_type not in _OGG_CONTENT_TYPES:
                        _update_failed_observation(
                            observation,
                            error_code="elevenlabs_content_type_invalid",
                            attempt_count=attempt_count,
                            voice_id=voice_id,
                            output_format=output_format,
                            model_id=model_id,
                            upstream_status_code=response.status_code,
                        )
                        raise ElevenLabsSpeechGenerationError(
                            "ElevenLabs speech generation did not return Ogg audio.",
                            error_code="elevenlabs_content_type_invalid",
                            status_code=response.status_code,
                            attempt_count=attempt_count,
                        )
                    if not response.content.startswith(b"OggS"):
                        _update_failed_observation(
                            observation,
                            error_code="elevenlabs_ogg_header_invalid",
                            attempt_count=attempt_count,
                            voice_id=voice_id,
                            output_format=output_format,
                            model_id=model_id,
                            upstream_status_code=response.status_code,
                        )
                        raise ElevenLabsSpeechGenerationError(
                            "ElevenLabs speech generation did not return an Ogg container.",
                            error_code="elevenlabs_ogg_header_invalid",
                            status_code=response.status_code,
                            attempt_count=attempt_count,
                        )
                    content_type = OGG_OPUS_CONTENT_TYPE

                billed_characters = _resolve_billed_characters(
                    response=response,
                    text=speech_text,
                )
                request_id = _clean_header_value(response.headers.get("request-id"))
                provider_trace_id = _clean_header_value(response.headers.get("x-trace-id"))
                if observation is not None:
                    observation.update(
                        model=f"elevenlabs/{model_id}",
                        model_parameters={
                            "voice_id": voice_id,
                            "output_format": output_format,
                        },
                        output={
                            "content_type": content_type,
                            "audio_bytes": len(response.content),
                        },
                        usage_details={"characters": billed_characters},
                        cost_details=_build_cost_details(
                            billed_characters=billed_characters,
                            settings=current_settings,
                        ),
                        metadata=_observation_metadata(
                            voice_id=voice_id,
                            output_format=output_format,
                            model_id=model_id,
                            attempt_count=attempt_count,
                            request_id=request_id,
                            provider_trace_id=provider_trace_id,
                            billed_characters=billed_characters,
                            upstream_status_code=response.status_code,
                        ),
                    )

                return ElevenLabsGeneratedSpeech(
                    content=response.content,
                    content_type=content_type,
                    output_format=output_format,
                    attempt_count=attempt_count,
                    billed_characters=billed_characters,
                    request_id=request_id,
                    provider_trace_id=provider_trace_id,
                    model_id=model_id,
                )
    finally:
        if owns_client:
            current_client.close()

    raise AssertionError("ElevenLabs retry loop completed without a result.")


def _build_client(settings: Settings) -> httpx.Client:
    return httpx.Client(
        base_url=settings.elevenlabs_base_url.rstrip("/"),
        timeout=settings.elevenlabs_timeout_seconds,
    )


def _build_tts_payload(text: str, settings: Settings) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "text": text,
        "model_id": settings.elevenlabs_model_id.strip() or "eleven_v3",
    }
    voice_settings = _build_voice_settings(settings)
    if voice_settings:
        payload["voice_settings"] = voice_settings

    return payload


def _build_voice_settings(settings: Settings) -> dict[str, Any]:
    values: dict[str, Any] = {}
    if settings.elevenlabs_voice_stability is not None:
        values["stability"] = settings.elevenlabs_voice_stability
    if settings.elevenlabs_voice_similarity_boost is not None:
        values["similarity_boost"] = settings.elevenlabs_voice_similarity_boost
    if settings.elevenlabs_voice_style is not None:
        values["style"] = settings.elevenlabs_voice_style
    if settings.elevenlabs_voice_use_speaker_boost is not None:
        values["use_speaker_boost"] = settings.elevenlabs_voice_use_speaker_boost
    if settings.elevenlabs_voice_speed is not None:
        values["speed"] = settings.elevenlabs_voice_speed

    return values


def _is_retryable_response(response: httpx.Response) -> bool:
    return response.status_code == 429 or response.status_code >= 500


def _wait_before_retry(settings: Settings) -> None:
    if settings.elevenlabs_retry_backoff_seconds > 0:
        time.sleep(settings.elevenlabs_retry_backoff_seconds)


def _resolve_content_type(response: httpx.Response, output_format: str) -> str:
    content_type = response.headers.get("content-type")
    if content_type:
        return content_type.split(";", maxsplit=1)[0].strip().lower()

    if output_format.startswith("mp3"):
        return "audio/mpeg"
    if output_format.startswith("pcm"):
        return "audio/wav"
    if output_format.startswith("ulaw"):
        return "audio/basic"
    if output_format.startswith("opus"):
        return OGG_OPUS_CONTENT_TYPE

    return "application/octet-stream"


def _safe_json(response: httpx.Response) -> dict[str, Any] | None:
    if not response.content:
        return None

    try:
        payload = response.json()
    except ValueError:
        return None

    return payload if isinstance(payload, dict) else {"data": payload}


def _resolve_billed_characters(*, response: httpx.Response, text: str) -> int:
    character_cost = _clean_header_value(response.headers.get("character-cost"))
    if character_cost is not None:
        try:
            billed_characters = int(character_cost)
        except ValueError:
            billed_characters = None
        else:
            if billed_characters >= 0:
                return billed_characters

    return len(text)


def _build_cost_details(
    *,
    billed_characters: int,
    settings: Settings,
) -> dict[str, float] | None:
    rate_per_1k_chars = settings.elevenlabs_tts_cost_per_1k_chars_usd
    if rate_per_1k_chars is None:
        return None

    return {
        "characters": round((billed_characters / 1000) * rate_per_1k_chars, 8),
    }


def _observation_metadata(
    *,
    voice_id: str,
    output_format: str,
    model_id: str,
    attempt_count: int | None = None,
    request_id: str | None = None,
    provider_trace_id: str | None = None,
    billed_characters: int | None = None,
    upstream_status_code: int | None = None,
    error_code: str | None = None,
) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "provider": "elevenlabs",
        "voice_id": voice_id,
        "output_format": output_format,
        "model_id": model_id,
    }
    if attempt_count is not None:
        metadata["attempt_count"] = attempt_count
    if request_id:
        metadata["request_id"] = request_id
    if provider_trace_id:
        metadata["provider_trace_id"] = provider_trace_id
    if billed_characters is not None:
        metadata["billed_characters"] = billed_characters
    if upstream_status_code is not None:
        metadata["upstream_status_code"] = upstream_status_code
    if error_code:
        metadata["error_code"] = error_code
    return metadata


def _update_failed_observation(
    observation: Any | None,
    *,
    error_code: str,
    attempt_count: int,
    voice_id: str,
    output_format: str,
    model_id: str,
    status_message: str | None = None,
    upstream_status_code: int | None = None,
) -> None:
    if observation is None:
        return

    observation.update(
        level="ERROR",
        status_message=status_message or error_code,
        metadata=_observation_metadata(
            voice_id=voice_id,
            output_format=output_format,
            model_id=model_id,
            attempt_count=attempt_count,
            upstream_status_code=upstream_status_code,
            error_code=error_code,
        ),
    )


def _clean_header_value(value: str | None) -> str | None:
    if value is None:
        return None

    normalized = value.strip()
    return normalized or None


__all__ = ["generate_elevenlabs_speech"]
