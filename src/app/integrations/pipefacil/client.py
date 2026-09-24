from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx

from app.core.config import Settings, get_settings
from app.integrations.pipefacil.contracts import PipefacilDeliveryErrorCode
from app.integrations.pipefacil.conversation import (
    PipefacilConversationHistory,
    PipefacilConversationHistoryError,
    normalize_conversation_history,
)

PUBLIC_MESSAGES_PATH = "/api/v1/messages"
PUBLIC_DEALS_PATH = "/api/v1/deals"
MEDIA_DOWNLOAD_CHUNK_SIZE = 64 * 1024
OUTBOUND_MEDIA_MESSAGE_TYPES = {"image", "video", "audio", "document"}


class PipefacilSendMessageError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        error_code: PipefacilDeliveryErrorCode,
        status_code: int | None = None,
        request_id: str | None = None,
        response_body: Any | None = None,
    ) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.status_code = status_code
        self.request_id = request_id
        self.response_body = response_body


class PipefacilMediaDownloadError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        error_code: str,
        status_code: int | None = None,
        content_length: int | None = None,
    ) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.status_code = status_code
        self.content_length = content_length


class PipefacilDealLookupError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        error_code: str,
        status_code: int | None = None,
        request_id: str | None = None,
        response_body: Any | None = None,
    ) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.status_code = status_code
        self.request_id = request_id
        self.response_body = response_body


class PipefacilConversationHistoryLookupError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        error_code: str,
        status_code: int | None = None,
        request_id: str | None = None,
        response_body: Any | None = None,
    ) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.status_code = status_code
        self.request_id = request_id
        self.response_body = response_body


class PipefacilDealUpdateError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        error_code: str,
        status_code: int | None = None,
        request_id: str | None = None,
        response_body: Any | None = None,
    ) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.status_code = status_code
        self.request_id = request_id
        self.response_body = response_body


@dataclass(slots=True)
class PipefacilSendMessageResult:
    status_code: int
    request_id: str | None
    payload: dict[str, Any] | None


@dataclass(frozen=True, slots=True)
class PipefacilDownloadedMedia:
    content: bytes
    content_type: str | None
    content_length: int
    status_code: int


@dataclass(frozen=True, slots=True)
class PipefacilDealUpdateResult:
    status_code: int
    request_id: str | None
    payload: dict[str, Any] | None


def fetch_deal_by_seq(
    *,
    seq: int | None,
    settings: Settings | None = None,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    current_settings = settings or get_settings()
    if not current_settings.pipefacil_api_key:
        raise PipefacilDealLookupError(
            "PIPEFACIL_API_KEY is required to fetch Pipefacil deal details.",
            error_code="pipefacil_api_key_missing",
        )

    if seq is None:
        raise PipefacilDealLookupError(
            "Pipefacil deal seq is required to fetch deal details.",
            error_code="pipefacil_deal_seq_missing",
        )

    owns_client = client is None
    current_client = client or _build_client(current_settings)

    try:
        response = current_client.get(
            f"{PUBLIC_DEALS_PATH}/{seq}",
            headers=_build_headers(current_settings),
        )
    except httpx.HTTPError as exc:
        raise PipefacilDealLookupError(
            f"Pipefacil deal lookup request failed: {exc}",
            error_code="pipefacil_transport_error",
        ) from exc
    finally:
        if owns_client:
            current_client.close()

    response_payload = _safe_json(response)
    request_id = response.headers.get("x-request-id")

    if response.is_error:
        raise PipefacilDealLookupError(
            "Pipefacil deal lookup returned an error response.",
            error_code="pipefacil_upstream_error",
            status_code=response.status_code,
            request_id=request_id,
            response_body=response_payload,
        )

    if not isinstance(response_payload, dict):
        raise PipefacilDealLookupError(
            "Pipefacil deal lookup returned an empty response.",
            error_code="pipefacil_response_empty",
            status_code=response.status_code,
            request_id=request_id,
        )

    deal_payload = response_payload.get("data")
    if isinstance(deal_payload, dict):
        return deal_payload

    return response_payload


def fetch_pipefacil_conversation_history(
    *,
    deal_seq: int | None = None,
    deal_id: str | None = None,
    contact_id: str | None = None,
    channel_id: str | None = None,
    limit: int | None = None,
    settings: Settings | None = None,
    client: httpx.Client | None = None,
) -> PipefacilConversationHistory:
    current_settings = settings or get_settings()
    if not current_settings.pipefacil_api_key:
        raise PipefacilConversationHistoryLookupError(
            "PIPEFACIL_API_KEY is required to fetch conversation history.",
            error_code="pipefacil_api_key_missing",
        )

    query_params: dict[str, Any] = {}
    if deal_seq is not None:
        query_params["dealSeq"] = deal_seq
    if deal_id and deal_id.strip():
        query_params["dealId"] = deal_id.strip()
    if contact_id and contact_id.strip():
        query_params["contactId"] = contact_id.strip()
    if channel_id and channel_id.strip():
        query_params["channelId"] = channel_id.strip()
    if limit is not None:
        query_params["limit"] = limit

    if not query_params:
        raise PipefacilConversationHistoryLookupError(
            "At least one conversation identifier is required to fetch history.",
            error_code="pipefacil_history_identifier_missing",
        )

    history_path = (current_settings.pipefacil_conversation_history_path or "").strip()
    if not history_path.startswith("/"):
        raise PipefacilConversationHistoryLookupError(
            "PIPEFACIL_CONVERSATION_HISTORY_PATH must start with '/'.",
            error_code="pipefacil_history_path_invalid",
        )

    owns_client = client is None
    current_client = client or _build_client(current_settings)

    try:
        response = current_client.get(
            history_path,
            params=query_params,
            headers=_build_headers(current_settings),
        )
    except httpx.HTTPError as exc:
        raise PipefacilConversationHistoryLookupError(
            f"Pipefacil conversation history request failed: {exc}",
            error_code="pipefacil_transport_error",
        ) from exc
    finally:
        if owns_client:
            current_client.close()

    response_payload = _safe_json(response)
    request_id = response.headers.get("x-request-id")

    if response.is_error:
        raise PipefacilConversationHistoryLookupError(
            "Pipefacil conversation history request returned an error response.",
            error_code="pipefacil_upstream_error",
            status_code=response.status_code,
            request_id=request_id,
            response_body=response_payload,
        )

    try:
        return normalize_conversation_history(response_payload)
    except PipefacilConversationHistoryError as exc:
        raise PipefacilConversationHistoryLookupError(
            str(exc),
            error_code="pipefacil_history_response_invalid",
            status_code=response.status_code,
            request_id=request_id,
            response_body=response_payload,
        ) from exc


def update_deal_properties(
    *,
    seq: int | None,
    properties: dict[str, Any],
    settings: Settings | None = None,
    client: httpx.Client | None = None,
) -> PipefacilDealUpdateResult:
    current_settings = settings or get_settings()
    if not current_settings.pipefacil_api_key:
        raise PipefacilDealUpdateError(
            "PIPEFACIL_API_KEY is required to update Pipefacil deal details.",
            error_code="pipefacil_api_key_missing",
        )

    if seq is None:
        raise PipefacilDealUpdateError(
            "Pipefacil deal seq is required to update deal details.",
            error_code="pipefacil_deal_seq_missing",
        )

    if not properties:
        raise PipefacilDealUpdateError(
            "Pipefacil deal properties update cannot be empty.",
            error_code="pipefacil_deal_properties_empty",
        )

    owns_client = client is None
    current_client = client or _build_client(current_settings)

    try:
        response = current_client.patch(
            f"{PUBLIC_DEALS_PATH}/{seq}",
            json={"customFields": properties},
            headers=_build_headers(current_settings),
        )
    except httpx.HTTPError as exc:
        raise PipefacilDealUpdateError(
            f"Pipefacil deal update request failed: {exc}",
            error_code="pipefacil_transport_error",
        ) from exc
    finally:
        if owns_client:
            current_client.close()

    response_payload = _safe_json(response)
    request_id = response.headers.get("x-request-id")

    if response.is_error:
        raise PipefacilDealUpdateError(
            "Pipefacil deal update returned an error response.",
            error_code="pipefacil_upstream_error",
            status_code=response.status_code,
            request_id=request_id,
            response_body=response_payload,
        )

    return PipefacilDealUpdateResult(
        status_code=response.status_code,
        request_id=request_id,
        payload=response_payload,
    )


def update_deal_stage(
    *,
    seq: int | None,
    stage_id: str | None,
    settings: Settings | None = None,
    client: httpx.Client | None = None,
) -> PipefacilDealUpdateResult:
    current_settings = settings or get_settings()
    if not current_settings.pipefacil_api_key:
        raise PipefacilDealUpdateError(
            "PIPEFACIL_API_KEY is required to update Pipefacil deal stage.",
            error_code="pipefacil_api_key_missing",
        )

    if seq is None:
        raise PipefacilDealUpdateError(
            "Pipefacil deal seq is required to update deal stage.",
            error_code="pipefacil_deal_seq_missing",
        )

    normalized_stage_id = (stage_id or "").strip()
    if not normalized_stage_id:
        raise PipefacilDealUpdateError(
            "Pipefacil stage id is required to update deal stage.",
            error_code="pipefacil_stage_id_missing",
        )

    owns_client = client is None
    current_client = client or _build_client(current_settings)

    try:
        response = current_client.patch(
            f"{PUBLIC_DEALS_PATH}/{seq}",
            json={"stageId": normalized_stage_id},
            headers=_build_headers(current_settings),
        )
    except httpx.HTTPError as exc:
        raise PipefacilDealUpdateError(
            f"Pipefacil deal stage update request failed: {exc}",
            error_code="pipefacil_transport_error",
        ) from exc
    finally:
        if owns_client:
            current_client.close()

    response_payload = _safe_json(response)
    request_id = response.headers.get("x-request-id")

    if response.is_error:
        raise PipefacilDealUpdateError(
            "Pipefacil deal stage update returned an error response.",
            error_code="pipefacil_upstream_error",
            status_code=response.status_code,
            request_id=request_id,
            response_body=response_payload,
        )

    return PipefacilDealUpdateResult(
        status_code=response.status_code,
        request_id=request_id,
        payload=response_payload,
    )


def send_public_text_message(
    *,
    to: str | None,
    text: str,
    sender_phone_number_id: str | None = None,
    profile_name: str | None = None,
    settings: Settings | None = None,
    client: httpx.Client | None = None,
) -> PipefacilSendMessageResult:
    current_settings = settings or get_settings()
    phone = (to or "").strip()
    message_text = text.strip()

    if not current_settings.pipefacil_api_key:
        raise PipefacilSendMessageError(
            "PIPEFACIL_API_KEY is required to send Pipefacil outbound messages.",
            error_code="pipefacil_api_key_missing",
        )

    if not phone:
        raise PipefacilSendMessageError(
            "Recipient phone is required to send Pipefacil outbound messages.",
            error_code="recipient_phone_missing",
        )

    if not message_text:
        raise PipefacilSendMessageError(
            "Response text cannot be empty when sending Pipefacil outbound messages.",
            error_code="response_text_empty",
        )

    payload: dict[str, Any] = {
        "to": phone,
        "type": "text",
        "text": message_text,
    }
    if sender_phone_number_id:
        payload["senderPhoneNumberId"] = sender_phone_number_id
    # Kept in the function signature for caller compatibility. Pipefacil'
    # PublicSendMessageRequest rejects profileName as an unsupported field.
    _ = profile_name

    owns_client = client is None
    current_client = client or _build_client(current_settings)

    try:
        response = current_client.post(
            PUBLIC_MESSAGES_PATH,
            json=payload,
            headers=_build_headers(current_settings),
        )
    except httpx.HTTPError as exc:
        raise PipefacilSendMessageError(
            f"Pipefacil outbound request failed: {exc}",
            error_code="pipefacil_transport_error",
        ) from exc
    finally:
        if owns_client:
            current_client.close()

    response_payload = _safe_json(response)
    request_id = response.headers.get("x-request-id")

    if response.is_error:
        raise PipefacilSendMessageError(
            "Pipefacil outbound message request returned an error response.",
            error_code="pipefacil_upstream_error",
            status_code=response.status_code,
            request_id=request_id,
            response_body=response_payload,
        )

    return PipefacilSendMessageResult(
        status_code=response.status_code,
        request_id=request_id,
        payload=response_payload,
    )


def send_whatsapp_media_message(
    *,
    to: str | None,
    media_type: str,
    media_url: str | None,
    caption: str | None = None,
    filename: str | None = None,
    mime_type: str | None = None,
    channel_id: str | None = None,
    sender_phone_number_id: str | None = None,
    settings: Settings | None = None,
    client: httpx.Client | None = None,
) -> PipefacilSendMessageResult:
    current_settings = settings or get_settings()
    phone = (to or "").strip()
    normalized_media_type = media_type.strip()
    outbound_media_url = (media_url or "").strip()

    if not current_settings.pipefacil_api_key:
        raise PipefacilSendMessageError(
            "PIPEFACIL_API_KEY is required to send Pipefacil outbound messages.",
            error_code="pipefacil_api_key_missing",
        )

    if not phone:
        raise PipefacilSendMessageError(
            "Recipient phone is required to send Pipefacil outbound messages.",
            error_code="recipient_phone_missing",
        )

    if normalized_media_type not in OUTBOUND_MEDIA_MESSAGE_TYPES:
        raise PipefacilSendMessageError(
            "Unsupported Pipefacil outbound media type.",
            error_code="response_media_type_unsupported",
        )

    if not outbound_media_url:
        raise PipefacilSendMessageError(
            "Outbound media URL is required to send Pipefacil media messages.",
            error_code="response_media_url_missing",
        )

    parsed_url = urlparse(outbound_media_url)
    if parsed_url.scheme != "https" or not parsed_url.netloc:
        raise PipefacilSendMessageError(
            "Outbound media URL must be absolute HTTPS.",
            error_code="response_media_url_invalid",
        )

    payload: dict[str, Any] = {
        "to": phone,
        "type": normalized_media_type,
        "text": None,
        "channelId": channel_id.strip() if channel_id and channel_id.strip() else None,
        "senderPhoneNumberId": (
            sender_phone_number_id.strip()
            if sender_phone_number_id and sender_phone_number_id.strip()
            else None
        ),
        "mediaAssetId": None,
        "mediaLink": outbound_media_url,
        "caption": caption.strip() if caption and caption.strip() else None,
        "filename": filename.strip() if filename and filename.strip() else None,
        "mimeType": mime_type.strip() if mime_type and mime_type.strip() else None,
        "templateName": None,
        "templateLanguageCode": None,
        "templateComponents": None,
        "quotedMessageId": None,
    }

    owns_client = client is None
    current_client = client or _build_client(current_settings)

    try:
        response = current_client.post(
            PUBLIC_MESSAGES_PATH,
            json=payload,
            headers=_build_headers(current_settings),
        )
    except httpx.HTTPError as exc:
        raise PipefacilSendMessageError(
            f"Pipefacil outbound media request failed: {exc}",
            error_code="pipefacil_transport_error",
        ) from exc
    finally:
        if owns_client:
            current_client.close()

    response_payload = _safe_json(response)
    request_id = response.headers.get("x-request-id")

    if response.is_error:
        raise PipefacilSendMessageError(
            "Pipefacil outbound media request returned an error response.",
            error_code="pipefacil_upstream_error",
            status_code=response.status_code,
            request_id=request_id,
            response_body=response_payload,
        )

    return PipefacilSendMessageResult(
        status_code=response.status_code,
        request_id=request_id,
        payload=response_payload,
    )


def download_pipefacil_media(
    *,
    download_url: str | None,
    settings: Settings | None = None,
    client: httpx.Client | None = None,
) -> PipefacilDownloadedMedia:
    current_settings = settings or get_settings()
    media_url = (download_url or "").strip()
    parsed_url = urlparse(media_url)
    if parsed_url.scheme != "https" or not parsed_url.netloc:
        raise PipefacilMediaDownloadError(
            "Pipefacil media download URL must be absolute HTTPS.",
            error_code="pipefacil_media_download_url_invalid",
        )

    owns_client = client is None
    current_client = client or httpx.Client(timeout=current_settings.pipefacil_timeout_seconds)

    try:
        with current_client.stream(
            "GET",
            media_url,
            headers={
                "Accept": "*/*",
                "User-Agent": _build_user_agent(current_settings),
            },
            follow_redirects=True,
        ) as response:
            content_length = _content_length_header(response)
            if (
                content_length is not None
                and content_length > current_settings.pipefacil_media_max_bytes
            ):
                raise PipefacilMediaDownloadError(
                    "Pipefacil media is larger than the configured limit.",
                    error_code="pipefacil_media_too_large",
                    status_code=response.status_code,
                    content_length=content_length,
                )

            if response.is_error:
                raise PipefacilMediaDownloadError(
                    "Pipefacil media download returned an error response.",
                    error_code="pipefacil_media_download_upstream_error",
                    status_code=response.status_code,
                    content_length=content_length,
                )

            chunks: list[bytes] = []
            downloaded_size = 0
            for chunk in response.iter_bytes(MEDIA_DOWNLOAD_CHUNK_SIZE):
                downloaded_size += len(chunk)
                if downloaded_size > current_settings.pipefacil_media_max_bytes:
                    raise PipefacilMediaDownloadError(
                        "Pipefacil media is larger than the configured limit.",
                        error_code="pipefacil_media_too_large",
                        status_code=response.status_code,
                        content_length=downloaded_size,
                    )
                chunks.append(chunk)
    except PipefacilMediaDownloadError:
        raise
    except httpx.HTTPError as exc:
        raise PipefacilMediaDownloadError(
            f"Pipefacil media download failed: {exc}",
            error_code="pipefacil_media_download_transport_error",
        ) from exc
    finally:
        if owns_client:
            current_client.close()

    content = b"".join(chunks)
    if not content:
        raise PipefacilMediaDownloadError(
            "Pipefacil media download returned an empty file.",
            error_code="pipefacil_media_empty",
            status_code=response.status_code,
            content_length=0,
        )

    return PipefacilDownloadedMedia(
        content=content,
        content_type=response.headers.get("content-type"),
        content_length=len(content),
        status_code=response.status_code,
    )


def _build_client(settings: Settings) -> httpx.Client:
    base_url = settings.pipefacil_base_url.rstrip("/")
    return httpx.Client(
        base_url=base_url,
        timeout=settings.pipefacil_timeout_seconds,
        headers=_build_headers(settings),
    )


def _build_user_agent(settings: Settings) -> str:
    return f"SDR-Pipefacil/{settings.app_version}"


def _build_headers(settings: Settings) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {settings.pipefacil_api_key}",
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": _build_user_agent(settings),
    }


def _safe_json(response: httpx.Response) -> dict[str, Any] | None:
    if not response.content:
        return None

    try:
        payload = response.json()
    except ValueError:
        return None

    if isinstance(payload, dict):
        return payload

    return {"data": payload}


def _content_length_header(response: httpx.Response) -> int | None:
    value = response.headers.get("content-length")
    if value is None:
        return None

    try:
        return int(value)
    except ValueError:
        return None
