from __future__ import annotations

import base64
import mimetypes
import re
import subprocess
import tempfile
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.core.config import Settings
from app.integrations.openai_audio import OpenAITranscriptionError, transcribe_audio_file
from app.integrations.pipefacil.client import (
    PipefacilDownloadedMedia,
    PipefacilMediaDownloadError,
    download_pipefacil_media,
)
from app.integrations.pipefacil.contracts import MessageReceivedEventRequest

PHONE_DIGITS_PATTERN = re.compile(r"\D+")
TRACE_ID_WHITESPACE_PATTERN = re.compile(r"\s+")
TRACE_ID_MAX_LENGTH = 200
FFMPEG_TIMEOUT_SECONDS = 30.0
MEDIA_DOWNLOAD_URL_KEYS = (
    "downloadUrl",
    "download_url",
    "downloadURL",
    "mediaUrl",
    "media_url",
    "mediaLink",
    "media_link",
    "url",
    "link",
)
MEDIA_BINARY_KEYS = ("base64", "data", "content", "bytes")
MEDIA_FILENAME_KEYS = ("filename", "fileName", "file_name", "name")
MEDIA_MIME_TYPE_KEYS = (
    "mimeType",
    "mime_type",
    "mimetype",
    "mime",
    "contentType",
    "content_type",
    "content-type",
)
MEDIA_NESTED_KEYS = ("media", "attachment", "file", "audio", "image", "sticker", "document")
MEDIA_TYPE_KEYS = ("type", "mediaType", "media_type", "messageType", "message_type", "kind")
MEDIA_TOP_LEVEL_KEYS = (
    *MEDIA_DOWNLOAD_URL_KEYS,
    *MEDIA_MIME_TYPE_KEYS,
    *MEDIA_TYPE_KEYS,
    "id",
    "mediaId",
    "media_id",
    "assetId",
    "asset_id",
    "filename",
    "fileName",
    "file_name",
    "size",
    "fileSize",
    "file_size",
    "sizeBytes",
    "size_bytes",
    "duration",
    "durationSeconds",
    "duration_seconds",
)
SUPPORTED_VISUAL_MESSAGE_TYPES = {"image", "sticker"}
VISUAL_MESSAGE_TYPE_ALIASES = {
    "image": "image",
    "photo": "image",
    "picture": "image",
    "sticker": "sticker",
}
SUPPORTED_VISUAL_MIME_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
DEFAULT_VISUAL_MIME_TYPE = "image/jpeg"
DEFAULT_STICKER_MIME_TYPE = "image/webp"
FILE_MESSAGE_TYPES = {
    "attachment",
    "document",
    "document_message",
    "file",
    "file_message",
}
DEFAULT_FILE_MIME_TYPE = "application/octet-stream"
AUDIO_MESSAGE_TYPES = {
    "audio",
    "voice",
    "ptt",
    "voice_note",
    "voice-note",
    "audio_message",
}
SOURCE_AUDIO_SUFFIXES = {
    "audio/ogg": ".ogg",
    "audio/webm": ".webm",
    "audio/mpeg": ".mp3",
    "audio/mp4": ".mp4",
    "audio/mpga": ".mpga",
    "audio/m4a": ".m4a",
    "audio/wav": ".wav",
}
MEDIA_LOG_FIELD_ALIASES = {
    "id": "media_id",
    "assetId": "media_id",
    "asset_id": "media_id",
    "mediaId": "media_id",
    "media_id": "media_id",
    "type": "media_type",
    "mediaType": "media_type",
    "media_type": "media_type",
    "kind": "media_type",
    "mimeType": "media_mime_type",
    "mime_type": "media_mime_type",
    "contentType": "media_mime_type",
    "content_type": "media_mime_type",
    "size": "media_size",
    "fileSize": "media_size",
    "file_size": "media_size",
    "sizeBytes": "media_size",
    "size_bytes": "media_size",
    "duration": "media_duration",
    "durationSeconds": "media_duration",
    "duration_seconds": "media_duration",
}
MessageContent = str | list[dict[str, Any]]
MediaDownloadedCallback = Callable[["InboundMediaDownload"], None]


@dataclass(frozen=True, slots=True)
class NormalizedInboundMessage:
    message_type: str
    content: MessageContent
    text: str
    has_media: bool = False
    media_mime_type: str | None = None
    media_size: int | None = None
    media_status_code: int | None = None
    transcription_length: int | None = None


@dataclass(frozen=True, slots=True)
class InboundMediaDownload:
    message_type: str
    media_mime_type: str | None
    media_size: int
    media_status_code: int


class PipefacilInboundMessageError(ValueError):
    """Raised when an inbound Pipefacil event cannot be handled by the chat flow."""


class PipefacilMediaProcessingError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        error_code: str,
        status_code: int | None = None,
        media_mime_type: str | None = None,
        media_size: int | None = None,
    ) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.status_code = status_code
        self.media_mime_type = media_mime_type
        self.media_size = media_size


def normalize_phone_number(phone: str | None) -> str | None:
    if not phone:
        return None

    digits = PHONE_DIGITS_PATTERN.sub("", phone)
    if not digits:
        return None

    return f"+{digits}"


def resolve_message_received_session_id(payload: MessageReceivedEventRequest) -> str:
    candidates = (
        payload.data.deal.id if payload.data.deal else None,
        payload.data.contact.id,
        payload.data.message.externalId,
        payload.data.message.id,
    )
    for candidate in candidates:
        if candidate:
            return candidate[:255]

    raise PipefacilInboundMessageError("Could not resolve thread_id from inbound event payload.")


def resolve_message_received_pipeline_run_id(payload: MessageReceivedEventRequest) -> str:
    return payload.data.message.externalId or payload.data.message.id


def _normalize_trace_identity_part(value: str | None) -> str | None:
    if not value:
        return None

    normalized = unicodedata.normalize("NFKD", value)
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii")
    cleaned_value = TRACE_ID_WHITESPACE_PATTERN.sub(" ", ascii_value).strip()
    return cleaned_value or None


def _truncate_trace_identity(value: str) -> str:
    if len(value) <= TRACE_ID_MAX_LENGTH:
        return value
    return value[:TRACE_ID_MAX_LENGTH].rstrip()


def resolve_message_received_trace_user_id(
    payload: MessageReceivedEventRequest,
    *,
    mode: str = "contact_id",
) -> str:
    contact_id = _normalize_trace_identity_part(payload.data.contact.id)
    contact_identity = f"contact:{contact_id}" if contact_id else None
    if mode == "contact_name_phone":
        name = _normalize_trace_identity_part(
            payload.data.contact.name
            or (payload.data.deal.name if payload.data.deal is not None else None)
        )
        phone = normalize_phone_number(payload.data.contact.phone)
        stable_identity_parts = [part for part in (phone, contact_identity) if part]
        stable_identity = " | ".join(stable_identity_parts)
        if name and stable_identity:
            max_name_length = max(
                TRACE_ID_MAX_LENGTH - len(stable_identity) - len(" | "),
                0,
            )
            recognizable_name = name[:max_name_length].rstrip()
            if recognizable_name:
                return f"{recognizable_name} | {stable_identity}"
        if stable_identity:
            return _truncate_trace_identity(stable_identity)
        if name:
            return _truncate_trace_identity(name)

    if contact_identity:
        return _truncate_trace_identity(contact_identity)

    return _truncate_trace_identity(f"contact-message:{payload.data.message.id}")


def resolve_message_received_trace_user_scope(
    payload: MessageReceivedEventRequest,
    *,
    user_id: str | None = None,
) -> str:
    resolved_user_id = user_id or resolve_message_received_trace_user_id(payload)
    if " | " in resolved_user_id and normalize_phone_number(payload.data.contact.phone):
        return "contact_name_phone"
    return "contact"


def resolve_message_received_trace_session_scope(payload: MessageReceivedEventRequest) -> str:
    if payload.data.deal is not None and payload.data.deal.id:
        return "lead"
    if payload.data.contact.id:
        return "contact"
    return "message"


def extract_message_received_text(payload: MessageReceivedEventRequest) -> str:
    if payload.data.message.type != "text":
        raise PipefacilInboundMessageError(
            "Only text inbound messages are supported in this endpoint."
        )

    message_text = (payload.data.message.body or "").strip()
    if not message_text:
        raise PipefacilInboundMessageError("Inbound text message body cannot be empty.")

    return message_text


def normalize_message_received_content(
    payload: MessageReceivedEventRequest,
    *,
    settings: Settings,
    on_media_downloaded: MediaDownloadedCallback | None = None,
) -> NormalizedInboundMessage:
    message = payload.data.message
    media = _resolve_message_media(message)
    message_type = _normalize_message_type(message.type, media=media)

    if message_type == "text":
        message_text = (message.body or "").strip()
        if not message_text:
            raise PipefacilInboundMessageError("Inbound text message body cannot be empty.")
        return NormalizedInboundMessage(
            message_type=message_type,
            content=message_text,
            text=message_text,
        )

    if message_type in SUPPORTED_VISUAL_MESSAGE_TYPES:
        return _normalize_visual_message(
            message_type=message_type,
            media=media,
            message_body=message.body,
            settings=settings,
            on_media_downloaded=on_media_downloaded,
        )

    if message_type == "audio":
        return _normalize_audio_message(
            media=media,
            settings=settings,
            on_media_downloaded=on_media_downloaded,
        )

    if message_type == "file":
        return _normalize_file_message(
            media=media,
            message_body=message.body,
            settings=settings,
            on_media_downloaded=on_media_downloaded,
        )

    raise PipefacilInboundMessageError(f"Inbound message type is not supported: {message.type}.")


def validate_message_received_content(payload: MessageReceivedEventRequest) -> None:
    """Validate inbound content without downloading media or calling external services."""
    message = payload.data.message
    media = _resolve_message_media(message)
    message_type = _normalize_message_type(message.type, media=media)

    if message_type == "text" and not (message.body or "").strip():
        raise PipefacilInboundMessageError("Inbound text message body cannot be empty.")

    supported_message_types = {"text", "audio", "file", *SUPPORTED_VISUAL_MESSAGE_TYPES}
    if message_type not in supported_message_types:
        raise PipefacilInboundMessageError(
            f"Inbound message type is not supported: {message.type}."
        )


def build_message_received_metadata(
    payload: MessageReceivedEventRequest,
    *,
    session_id: str,
    user_id: str,
) -> dict[str, Any]:
    message_metadata = _safe_message_payload(
        payload.data.message.model_dump(mode="json", exclude_none=True)
    )

    return {
        "event_type": payload.type,
        "event_timestamp": payload.timestamp.isoformat(),
        "source": "message.received",
        "trace_session_id": session_id,
        "trace_session_scope": resolve_message_received_trace_session_scope(payload),
        "trace_user_id": user_id,
        "trace_user_scope": resolve_message_received_trace_user_scope(
            payload,
            user_id=user_id,
        ),
        "message": message_metadata,
        "channel": payload.data.channel.model_dump(mode="json", exclude_none=True),
        "contact": payload.data.contact.model_dump(mode="json", exclude_none=True),
        "deal": (
            payload.data.deal.model_dump(mode="json", exclude_none=True)
            if payload.data.deal is not None
            else None
        ),
    }


def build_message_received_raw_log_payload(payload: MessageReceivedEventRequest) -> dict[str, Any]:
    payload_data = payload.model_dump(mode="json", exclude_none=True)
    data = payload_data["data"]
    data["message"] = _safe_message_payload(data["message"])
    data["messages"] = [_safe_message_payload(message) for message in data["messages"]]
    return payload_data


def build_message_received_log_context(
    payload: MessageReceivedEventRequest,
    *,
    thread_id: str | None = None,
    user_id: str | None = None,
    delivery_status: str | None = None,
) -> dict[str, Any]:
    message = payload.data.message
    media = _resolve_message_media(message)
    context: dict[str, Any] = {
        "pipeline_run_id": resolve_message_received_pipeline_run_id(payload),
        "event_type": payload.type,
        "message_id": message.id,
        "external_message_id": message.externalId,
        "message_type": message.type,
        "has_media": bool(media),
    }
    if media:
        context.update(_message_media_log_context(media))
    if thread_id:
        context["thread_id"] = thread_id
    if user_id:
        context["user_id"] = user_id
    if delivery_status:
        context["delivery_status"] = delivery_status
    return context


def _message_media_log_context(media: dict[str, Any]) -> dict[str, Any]:
    context: dict[str, Any] = {
        "media_keys": sorted(str(key) for key in media),
    }
    for source_key, target_key in MEDIA_LOG_FIELD_ALIASES.items():
        value = _media_value(media, (source_key,))
        if value is not None and target_key not in context:
            context[target_key] = value

    return context


def _normalize_message_type(raw_message_type: str, *, media: dict[str, Any] | None) -> str:
    message_type = raw_message_type.strip().lower()
    if message_type in VISUAL_MESSAGE_TYPE_ALIASES:
        return VISUAL_MESSAGE_TYPE_ALIASES[message_type]
    if message_type in AUDIO_MESSAGE_TYPES:
        return "audio"
    if message_type in FILE_MESSAGE_TYPES:
        return "file"

    media_type = _resolve_media_type(media)
    if media_type in VISUAL_MESSAGE_TYPE_ALIASES:
        return VISUAL_MESSAGE_TYPE_ALIASES[media_type]
    if media_type in AUDIO_MESSAGE_TYPES:
        return "audio"
    if media_type in FILE_MESSAGE_TYPES:
        return "file"

    media_mime_type = _resolve_media_mime_type(media, None)
    if media_mime_type:
        if media_mime_type.startswith("audio/"):
            return "audio"
        if media_mime_type.startswith("image/"):
            return "image"
        return "file"

    if _resolve_media_download_url(media):
        return "file"

    return message_type


def _resolve_message_media(message: Any) -> dict[str, Any] | None:
    media: dict[str, Any] = {}
    if isinstance(message.media, dict):
        media.update(message.media)

    message_extra = getattr(message, "model_extra", None)
    if isinstance(message_extra, dict):
        for key in MEDIA_NESTED_KEYS:
            value = message_extra.get(key)
            if isinstance(value, dict):
                media.update(
                    {str(nested_key): nested_value for nested_key, nested_value in value.items()}
                )

        for key in MEDIA_TOP_LEVEL_KEYS:
            value = message_extra.get(key)
            if value is not None and key not in media:
                media[key] = value

    return media or None


def _normalize_visual_message(
    *,
    message_type: str,
    media: dict[str, Any] | None,
    message_body: str | None,
    settings: Settings,
    on_media_downloaded: MediaDownloadedCallback | None,
) -> NormalizedInboundMessage:
    downloaded = _download_required_media(media, settings=settings)
    default_mime_type = (
        DEFAULT_STICKER_MIME_TYPE if message_type == "sticker" else DEFAULT_VISUAL_MIME_TYPE
    )
    mime_type = _resolve_media_mime_type(media, downloaded) or default_mime_type
    _notify_media_downloaded(
        on_media_downloaded,
        message_type=message_type,
        mime_type=mime_type,
        downloaded=downloaded,
    )
    if mime_type not in SUPPORTED_VISUAL_MIME_TYPES:
        raise PipefacilMediaProcessingError(
            "Inbound visual media MIME type is not supported.",
            error_code="pipefacil_media_mime_type_unsupported",
            status_code=downloaded.status_code,
            media_mime_type=mime_type,
            media_size=downloaded.content_length,
        )

    label = "figurinha" if message_type == "sticker" else "imagem"
    caption = (message_body or "").strip()
    text_parts = [
        f"Tipo de mensagem: {message_type}",
        f"Arquivo recebido: {label} anexada para analise.",
    ]
    if caption:
        text_parts.insert(1, f"Legenda: {caption}")
    text = "\n".join(text_parts)
    content = [
        {"type": "text", "text": text},
        {
            "type": "image",
            "base64": base64.b64encode(downloaded.content).decode("ascii"),
            "mime_type": mime_type,
        },
    ]
    return NormalizedInboundMessage(
        message_type=message_type,
        content=content,
        text=text,
        has_media=True,
        media_mime_type=mime_type,
        media_size=downloaded.content_length,
        media_status_code=downloaded.status_code,
    )


def _normalize_audio_message(
    *,
    media: dict[str, Any] | None,
    settings: Settings,
    on_media_downloaded: MediaDownloadedCallback | None,
) -> NormalizedInboundMessage:
    downloaded = _download_required_media(media, settings=settings)
    mime_type = _resolve_media_mime_type(media, downloaded) or "audio/ogg"
    _notify_media_downloaded(
        on_media_downloaded,
        message_type="audio",
        mime_type=mime_type,
        downloaded=downloaded,
    )
    try:
        transcription = _transcribe_downloaded_audio(
            downloaded,
            mime_type=mime_type,
            settings=settings,
        )
    except OpenAITranscriptionError as exc:
        raise PipefacilMediaProcessingError(
            "Inbound audio transcription failed.",
            error_code=exc.error_code,
            status_code=downloaded.status_code,
            media_mime_type=mime_type,
            media_size=downloaded.content_length,
        ) from exc

    text = f"Tipo de mensagem: audio\nTranscricao: {transcription}"
    return NormalizedInboundMessage(
        message_type="audio",
        content=text,
        text=text,
        has_media=True,
        media_mime_type=mime_type,
        media_size=downloaded.content_length,
        media_status_code=downloaded.status_code,
        transcription_length=len(transcription),
    )


def _normalize_file_message(
    *,
    media: dict[str, Any] | None,
    message_body: str | None,
    settings: Settings,
    on_media_downloaded: MediaDownloadedCallback | None,
) -> NormalizedInboundMessage:
    downloaded = _download_required_media(media, settings=settings)
    mime_type = _resolve_media_mime_type(media, downloaded) or DEFAULT_FILE_MIME_TYPE
    filename = _resolve_media_filename(media, mime_type=mime_type)
    _notify_media_downloaded(
        on_media_downloaded,
        message_type="file",
        mime_type=mime_type,
        downloaded=downloaded,
    )

    caption = (message_body or "").strip()
    text_parts = [
        "Tipo de mensagem: file",
        f"Arquivo recebido: {filename} ({mime_type}) para analise.",
        "Se o arquivo nao puder ser lido pelo modelo, explique isso e peca o conteudo em texto.",
    ]
    if caption:
        text_parts.insert(1, f"Legenda: {caption}")
    text = "\n".join(text_parts)
    content = [
        {"type": "text", "text": text},
        {
            "type": "file",
            "base64": base64.b64encode(downloaded.content).decode("ascii"),
            "mime_type": mime_type,
            "filename": filename,
        },
    ]
    return NormalizedInboundMessage(
        message_type="file",
        content=content,
        text=text,
        has_media=True,
        media_mime_type=mime_type,
        media_size=downloaded.content_length,
        media_status_code=downloaded.status_code,
    )


def _notify_media_downloaded(
    callback: MediaDownloadedCallback | None,
    *,
    message_type: str,
    mime_type: str | None,
    downloaded: PipefacilDownloadedMedia,
) -> None:
    if callback is None:
        return

    callback(
        InboundMediaDownload(
            message_type=message_type,
            media_mime_type=mime_type,
            media_size=downloaded.content_length,
            media_status_code=downloaded.status_code,
        )
    )


def _download_required_media(
    media: dict[str, Any] | None,
    *,
    settings: Settings,
) -> PipefacilDownloadedMedia:
    download_url = _resolve_media_download_url(media)
    if not download_url:
        raise PipefacilMediaProcessingError(
            "Inbound media payload did not include a download URL.",
            error_code="pipefacil_media_download_url_missing",
            media_mime_type=_resolve_media_mime_type(media, None),
        )

    try:
        return download_pipefacil_media(download_url=download_url, settings=settings)
    except PipefacilMediaDownloadError as exc:
        raise PipefacilMediaProcessingError(
            "Inbound media download failed.",
            error_code=exc.error_code,
            status_code=exc.status_code,
            media_mime_type=_resolve_media_mime_type(media, None),
            media_size=exc.content_length,
        ) from exc


def _transcribe_downloaded_audio(
    downloaded: PipefacilDownloadedMedia,
    *,
    mime_type: str,
    settings: Settings,
) -> str:
    source_suffix = SOURCE_AUDIO_SUFFIXES.get(mime_type, ".audio")
    with tempfile.TemporaryDirectory(prefix="pipefacil-audio-") as temp_dir:
        temp_path = Path(temp_dir)
        source_path = temp_path / f"inbound{source_suffix}"
        wav_path = temp_path / "inbound.wav"
        source_path.write_bytes(downloaded.content)
        _convert_audio_to_wav(source_path, wav_path)
        return transcribe_audio_file(wav_path, settings=settings)


def _convert_audio_to_wav(source_path: Path, wav_path: Path) -> None:
    command = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(source_path),
        "-vn",
        "-acodec",
        "pcm_s16le",
        "-ar",
        "16000",
        "-ac",
        "1",
        str(wav_path),
    ]
    try:
        subprocess.run(
            command,
            check=True,
            capture_output=True,
            timeout=FFMPEG_TIMEOUT_SECONDS,
        )
    except FileNotFoundError as exc:
        raise OpenAITranscriptionError(
            "ffmpeg is required to convert inbound audio before transcription.",
            error_code="ffmpeg_missing",
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise OpenAITranscriptionError(
            "ffmpeg timed out while converting inbound audio.",
            error_code="ffmpeg_conversion_timeout",
        ) from exc
    except subprocess.CalledProcessError as exc:
        raise OpenAITranscriptionError(
            "ffmpeg failed to convert inbound audio before transcription.",
            error_code="ffmpeg_conversion_failed",
        ) from exc


def _resolve_media_download_url(media: dict[str, Any] | None) -> str | None:
    if not media:
        return None

    value = _media_value(media, MEDIA_DOWNLOAD_URL_KEYS)
    if isinstance(value, str) and value.strip():
        return value.strip()

    return None


def _resolve_media_type(media: dict[str, Any] | None) -> str | None:
    if not media:
        return None

    value = _media_value(media, MEDIA_TYPE_KEYS)
    if isinstance(value, str) and value.strip():
        return value.strip().lower()

    return None


def _resolve_media_mime_type(
    media: dict[str, Any] | None,
    downloaded: PipefacilDownloadedMedia | None,
) -> str | None:
    if media:
        value = _media_value(media, MEDIA_MIME_TYPE_KEYS)
        if isinstance(value, str) and value.strip():
            return value.split(";", 1)[0].strip().lower()

    if downloaded and downloaded.content_type:
        return downloaded.content_type.split(";", 1)[0].strip().lower()

    return None


def _resolve_media_filename(media: dict[str, Any] | None, *, mime_type: str) -> str:
    if media:
        value = _media_value(media, MEDIA_FILENAME_KEYS)
        if isinstance(value, str) and value.strip():
            filename = _sanitize_filename(value)
            if filename:
                return _ensure_filename_extension(filename, mime_type=mime_type)

    return _default_filename_for_mime_type(mime_type)


def _sanitize_filename(value: str) -> str | None:
    filename = value.strip().replace("\\", "/").rsplit("/", 1)[-1]
    filename = re.sub(r"[\x00-\x1f\x7f]+", "", filename).strip()
    filename = filename.strip(".")
    if not filename:
        return None
    return filename[:160]


def _ensure_filename_extension(filename: str, *, mime_type: str) -> str:
    if Path(filename).suffix:
        return filename

    extension = _guess_extension(mime_type)
    return f"{filename}{extension}" if extension else filename


def _default_filename_for_mime_type(mime_type: str) -> str:
    extension = _guess_extension(mime_type) or ".bin"
    return f"inbound-file{extension}"


def _guess_extension(mime_type: str) -> str | None:
    extension = mimetypes.guess_extension(mime_type)
    if extension == ".jpe":
        return ".jpg"
    return extension


def _safe_message_payload(message: dict[str, Any]) -> dict[str, Any]:
    safe_payload: dict[str, Any] = {}
    download_url_present = False
    binary_present = False

    for key, value in message.items():
        if _key_matches(key, MEDIA_DOWNLOAD_URL_KEYS):
            download_url_present = True
            continue
        if _key_matches(key, MEDIA_BINARY_KEYS):
            binary_present = True
            continue
        if key == "media" and isinstance(value, dict):
            safe_payload[key] = _safe_media_payload(value)
            continue
        if key in MEDIA_NESTED_KEYS and isinstance(value, dict):
            safe_payload[key] = _safe_media_payload(value)
            continue
        if isinstance(value, dict):
            safe_payload[str(key)] = _safe_media_payload(value)
            continue
        safe_payload[str(key)] = value

    if download_url_present:
        safe_payload["download_url_present"] = True
    if binary_present:
        safe_payload["binary_payload_present"] = True

    return safe_payload


def _safe_media_payload(media: dict[str, Any]) -> dict[str, Any]:
    safe_payload: dict[str, Any] = {}
    download_url_present = False
    binary_present = False

    for key, value in media.items():
        if _key_matches(key, MEDIA_DOWNLOAD_URL_KEYS):
            download_url_present = True
            continue
        if _key_matches(key, MEDIA_BINARY_KEYS):
            binary_present = True
            continue
        if isinstance(value, dict):
            safe_payload[str(key)] = _safe_media_payload(value)
            continue
        safe_payload[str(key)] = value

    if download_url_present:
        safe_payload["download_url_present"] = True
    if binary_present:
        safe_payload["binary_payload_present"] = True

    return safe_payload


def _key_matches(key: object, candidates: tuple[str, ...]) -> bool:
    return str(key).lower() in {candidate.lower() for candidate in candidates}


def _media_value(media: dict[str, Any], candidates: tuple[str, ...]) -> Any:
    for key, value in media.items():
        if _key_matches(key, candidates):
            return value
    return None
