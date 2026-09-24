from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from app.core.config import Settings, get_settings
from app.integrations.elevenlabs import (
    ElevenLabsSpeechGenerationError,
    generate_elevenlabs_speech,
)
from app.integrations.generated_audio import (
    OGG_OPUS_CONTENT_TYPE,
    GeneratedAudioConversionError,
    GeneratedAudioStorageError,
    GeneratedAudioStoredFileNotFoundError,
    convert_audio_to_ogg_opus,
    resolve_stored_generated_audio_file,
    store_generated_audio,
)

GENERATED_AUDIO_ROUTE_PREFIX = "/generated-audio"


class GeneratedAudioError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        error_code: str,
        status_code: int | None = None,
        attempt_count: int = 0,
    ) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.status_code = status_code
        self.attempt_count = attempt_count


class GeneratedAudioNotFoundError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class GeneratedAudioAsset:
    media_id: str
    media_url: str
    content_type: str
    filename: str
    text: str
    attempt_count: int = 1


@dataclass(frozen=True, slots=True)
class GeneratedAudioFile:
    path: Path
    content_type: str


def prepare_generated_audio(
    *,
    text: str,
    settings: Settings | None = None,
) -> GeneratedAudioAsset:
    current_settings = settings or get_settings()
    audio_text = _normalize_audio_text(text, max_chars=current_settings.generated_audio_max_chars)
    if not audio_text:
        raise GeneratedAudioError("Generated audio text is empty.", error_code="audio_text_empty")

    public_base_url = _resolve_public_base_url(current_settings)
    try:
        speech = generate_elevenlabs_speech(text=audio_text, settings=current_settings)
    except ElevenLabsSpeechGenerationError as exc:
        raise GeneratedAudioError(
            str(exc),
            error_code=exc.error_code,
            status_code=exc.status_code,
            attempt_count=exc.attempt_count,
        ) from exc

    content = speech.content
    content_type = speech.content_type
    source_extension = _source_extension(content_type, speech.output_format)

    if current_settings.generated_audio_convert_to_ogg_opus:
        try:
            content = convert_audio_to_ogg_opus(content, source_extension=source_extension)
        except GeneratedAudioConversionError as exc:
            raise GeneratedAudioError(
                str(exc),
                error_code=exc.error_code,
                attempt_count=speech.attempt_count,
            ) from exc
        except GeneratedAudioStorageError as exc:
            raise _storage_error(exc, attempt_count=speech.attempt_count) from exc
        content_type = OGG_OPUS_CONTENT_TYPE
        extension = ".ogg"
    else:
        extension = _delivery_extension(content_type, attempt_count=speech.attempt_count)

    storage_dir = Path(current_settings.generated_audio_storage_dir)
    try:
        filename = store_generated_audio(
            content,
            extension=extension,
            storage_dir=storage_dir,
            ttl_seconds=current_settings.generated_audio_ttl_seconds,
        )
    except GeneratedAudioStorageError as exc:
        raise _storage_error(exc, attempt_count=speech.attempt_count) from exc

    return GeneratedAudioAsset(
        media_id=f"generated-audio:{filename.removesuffix(extension)}",
        media_url=f"{public_base_url}{GENERATED_AUDIO_ROUTE_PREFIX}/{filename}",
        content_type=content_type,
        filename=filename,
        text=audio_text,
        attempt_count=speech.attempt_count,
    )


def resolve_generated_audio_file(
    filename: str,
    *,
    settings: Settings | None = None,
) -> GeneratedAudioFile:
    current_settings = settings or get_settings()
    try:
        stored_file = resolve_stored_generated_audio_file(
            filename,
            storage_dir=Path(current_settings.generated_audio_storage_dir),
            ttl_seconds=current_settings.generated_audio_ttl_seconds,
        )
    except GeneratedAudioStoredFileNotFoundError as exc:
        raise GeneratedAudioNotFoundError("Generated audio file not found.") from exc
    except GeneratedAudioStorageError as exc:
        raise _storage_error(exc) from exc

    return GeneratedAudioFile(path=stored_file.path, content_type=stored_file.content_type)


def _normalize_audio_text(text: str, *, max_chars: int) -> str:
    normalized = " ".join(text.strip().split())
    if max_chars and len(normalized) > max_chars:
        return normalized[:max_chars].rsplit(" ", maxsplit=1)[0].strip()

    return normalized


def _resolve_public_base_url(settings: Settings) -> str:
    candidate = _public_base_url_candidate(settings)
    if not candidate:
        raise GeneratedAudioError(
            "GENERATED_AUDIO_PUBLIC_BASE_URL or CLOUDFLARE_TUNNEL_HOSTNAME is required.",
            error_code="generated_audio_public_base_url_missing",
        )

    parsed_url = urlparse(candidate)
    if parsed_url.scheme != "https" or not parsed_url.netloc:
        raise GeneratedAudioError(
            "Generated audio public base URL must be absolute HTTPS.",
            error_code="generated_audio_public_base_url_invalid",
        )

    return candidate.rstrip("/")


def _public_base_url_candidate(settings: Settings) -> str | None:
    configured_url = (settings.generated_audio_public_base_url or "").strip()
    if configured_url:
        return configured_url

    hostname = (settings.cloudflare_tunnel_hostname or "").strip()
    if hostname:
        return hostname if "://" in hostname else f"https://{hostname}"

    return None


def _source_extension(content_type: str, output_format: str) -> str:
    normalized_content_type = content_type.split(";", maxsplit=1)[0].strip().lower()
    if normalized_content_type in {"audio/mpeg", "audio/mp3"}:
        return ".mp3"
    if normalized_content_type == "audio/wav":
        return ".wav"
    if normalized_content_type == "audio/basic":
        return ".ulaw"
    if normalized_content_type == "audio/ogg":
        return ".ogg"
    if output_format.startswith("mp3"):
        return ".mp3"
    if output_format.startswith("pcm"):
        return ".wav"
    if output_format.startswith("ulaw"):
        return ".ulaw"
    return ".audio"


def _delivery_extension(content_type: str, *, attempt_count: int) -> str:
    normalized_content_type = content_type.split(";", maxsplit=1)[0].strip().lower()
    if normalized_content_type in {"audio/mpeg", "audio/mp3"}:
        return ".mp3"
    if normalized_content_type == "audio/ogg":
        return ".ogg"
    raise GeneratedAudioError(
        "Generated audio content type is not supported for direct delivery.",
        error_code="generated_audio_content_type_unsupported",
        attempt_count=attempt_count,
    )


def _storage_error(
    exc: GeneratedAudioStorageError,
    *,
    attempt_count: int = 0,
) -> GeneratedAudioError:
    return GeneratedAudioError(
        str(exc),
        error_code="generated_audio_storage_error",
        attempt_count=attempt_count,
    )


__all__ = [
    "GENERATED_AUDIO_ROUTE_PREFIX",
    "GeneratedAudioAsset",
    "GeneratedAudioError",
    "GeneratedAudioFile",
    "GeneratedAudioNotFoundError",
    "prepare_generated_audio",
    "resolve_generated_audio_file",
]
