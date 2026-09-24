from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

PipefacilDeliveryStatus = Literal["sent", "failed"]
PipefacilDeliveryErrorCode = Literal[
    "pipefacil_api_key_missing",
    "recipient_phone_missing",
    "response_text_empty",
    "response_media_url_missing",
    "response_media_url_invalid",
    "response_media_type_unsupported",
    "pipefacil_transport_error",
    "pipefacil_upstream_error",
]
MESSAGE_ID_KEYS = ("id", "messageId", "message_id")
MESSAGE_EXTERNAL_ID_KEYS = ("externalId", "external_id", "externalID", "wamid")
MESSAGE_BODY_KEYS = ("body", "text", "content", "messageBody", "message_body")
MESSAGE_TYPE_KEYS = ("type", "messageType", "message_type", "kind")
MESSAGE_TIMESTAMP_KEYS = (
    "timestamp",
    "messageTimestamp",
    "message_timestamp",
    "createdAt",
    "created_at",
)
MESSAGE_MEDIA_KEYS = (
    "media",
    "attachment",
    "file",
    "audio",
    "image",
    "sticker",
    "document",
    "downloadUrl",
    "download_url",
    "downloadURL",
    "mediaUrl",
    "media_url",
    "mediaLink",
    "media_link",
    "url",
    "link",
    "mimeType",
    "mime_type",
    "mimetype",
    "mime",
    "contentType",
    "content_type",
    "content-type",
    "mediaType",
    "media_type",
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
MESSAGE_CONTAINER_KEYS = (
    "message",
    "messages",
    "messageData",
    "message_data",
    "messagePayload",
    "message_payload",
    "payload",
    "event",
    "object",
    "resource",
    "record",
)


class EventMessagePayload(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    externalId: str | None = None
    body: str | None = None
    type: str
    timestamp: datetime
    media: dict[str, Any] | None = None

    @model_validator(mode="before")
    @classmethod
    def normalize_message_aliases(cls, value: Any) -> Any:
        if not isinstance(value, Mapping):
            return value

        payload = dict(value)
        _copy_first_alias(payload, target_key="id", source_keys=MESSAGE_ID_KEYS)
        _copy_first_alias(
            payload,
            target_key="externalId",
            source_keys=MESSAGE_EXTERNAL_ID_KEYS,
        )
        _copy_first_alias(payload, target_key="body", source_keys=MESSAGE_BODY_KEYS)
        _copy_first_alias(payload, target_key="type", source_keys=MESSAGE_TYPE_KEYS)
        _copy_first_alias(
            payload,
            target_key="timestamp",
            source_keys=MESSAGE_TIMESTAMP_KEYS,
        )

        if "type" not in payload and _string_value(payload.get("body")):
            payload["type"] = "text"

        return payload


class EventChannelPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    phoneNumberId: str
    phoneNumber: str
    displayName: str | None = None


class EventContactPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    name: str | None = None
    phone: str | None = None
    email: str | None = None


class EventDealStagePayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    name: str


class EventDealPayload(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    seq: int | None = None
    name: str | None = None
    stage: EventDealStagePayload | None = None


class MessageReceivedEventData(BaseModel):
    model_config = ConfigDict(extra="ignore")

    message: EventMessagePayload
    # Pipefacil may deliver multiple inbound messages in a single webhook.
    # Preserve the complete ordered list for the application layer instead of
    # using it only to resolve the backwards-compatible ``message`` field.
    messages: list[EventMessagePayload] = Field(default_factory=list)
    channel: EventChannelPayload
    contact: EventContactPayload
    deal: EventDealPayload | None = None


class MessageReceivedEventRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    type: Literal["message.received"]
    timestamp: datetime
    data: MessageReceivedEventData

    @model_validator(mode="before")
    @classmethod
    def normalize_message_received_payload(cls, value: Any) -> Any:
        if not isinstance(value, Mapping):
            return value

        payload = dict(value)
        data = payload.get("data")
        if not isinstance(data, Mapping):
            return payload

        normalized_data = dict(data)
        messages = normalized_data.get("messages")
        message = normalized_data.get("message")
        if isinstance(message, list) and (not isinstance(messages, list) or not messages):
            normalized_data["messages"] = message
        if not isinstance(message, Mapping):
            resolved_message = _resolve_message_from_data(
                normalized_data,
                event_timestamp=payload.get("timestamp"),
            )
            if resolved_message:
                normalized_data["message"] = resolved_message

        payload["data"] = normalized_data
        return payload

    def messages_for_processing(self) -> tuple[MessageReceivedEventRequest, ...]:
        """Expand an inbound batch into ordered single-message events.

        Each returned event keeps its own message identity so the application
        can deduplicate retries per item, while preserving the established
        single-message interfaces for mappings, logs, and delivery.
        """
        messages = self.data.messages or [self.data.message]
        return tuple(
            self.model_copy(
                update={"data": self.data.model_copy(update={"message": message, "messages": []})}
            )
            for message in messages
        )


def _copy_first_alias(
    payload: dict[str, Any],
    *,
    target_key: str,
    source_keys: tuple[str, ...],
) -> None:
    if payload.get(target_key) is not None:
        return

    for key in source_keys:
        value = payload.get(key)
        if value is not None:
            payload[target_key] = value
            return


def _first_present_value(payload: Mapping[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        value = payload.get(key)
        if value is not None:
            return value
    return None


def _string_value(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _resolve_message_from_data(
    data: Mapping[str, Any],
    *,
    event_timestamp: Any,
) -> dict[str, Any] | None:
    flattened_message = _build_message_from_flattened_data(
        data,
        event_timestamp=event_timestamp,
        message_value=data.get("message"),
    )
    if flattened_message:
        return flattened_message

    nested_message = _find_nested_message(data, event_timestamp=event_timestamp)
    if nested_message:
        return nested_message

    return None


def _find_nested_message(
    value: Any,
    *,
    event_timestamp: Any,
    depth: int = 0,
) -> dict[str, Any] | None:
    if depth > 3:
        return None

    if isinstance(value, Mapping):
        for key in MESSAGE_CONTAINER_KEYS:
            candidate = _message_from_candidate_value(
                value.get(key),
                event_timestamp=event_timestamp,
                depth=depth + 1,
            )
            if candidate:
                return candidate

        for key, nested_value in value.items():
            if key in {"channel", "contact", "deal"}:
                continue
            candidate = _message_from_candidate_value(
                nested_value,
                event_timestamp=event_timestamp,
                depth=depth + 1,
            )
            if candidate:
                return candidate

    return None


def _message_from_candidate_value(
    value: Any,
    *,
    event_timestamp: Any,
    depth: int,
) -> dict[str, Any] | None:
    if isinstance(value, str):
        return _build_message_from_flattened_data(
            {},
            event_timestamp=event_timestamp,
            message_value=value,
        )

    if isinstance(value, Mapping):
        if _looks_like_message_mapping(value):
            return _build_message_from_flattened_data(
                value,
                event_timestamp=event_timestamp,
                message_value=value.get("message"),
            )

        return _find_nested_message(value, event_timestamp=event_timestamp, depth=depth)

    if isinstance(value, list):
        for item in value:
            candidate = _message_from_candidate_value(
                item,
                event_timestamp=event_timestamp,
                depth=depth,
            )
            if candidate:
                return candidate

    return None


def _looks_like_message_mapping(value: Mapping[str, Any]) -> bool:
    has_message_content = any(value.get(key) is not None for key in MESSAGE_BODY_KEYS)
    has_media_content = any(value.get(key) is not None for key in MESSAGE_MEDIA_KEYS)
    has_message_identity = any(value.get(key) is not None for key in MESSAGE_ID_KEYS)
    has_message_type = any(value.get(key) is not None for key in MESSAGE_TYPE_KEYS)
    return has_message_content or has_media_content or (has_message_identity and has_message_type)


def _build_message_from_flattened_data(
    data: Mapping[str, Any],
    *,
    event_timestamp: Any,
    message_value: Any,
) -> dict[str, Any] | None:
    body = _first_present_value(data, MESSAGE_BODY_KEYS)
    if body is None and isinstance(message_value, str):
        body = message_value

    message_id = _first_present_value(data, MESSAGE_ID_KEYS)
    message_type = _first_present_value(data, MESSAGE_TYPE_KEYS)
    timestamp = _first_present_value(data, MESSAGE_TIMESTAMP_KEYS) or event_timestamp
    media = _first_present_value(data, MESSAGE_MEDIA_KEYS)

    if body is None and media is None and message_type is None:
        return None

    message: dict[str, Any] = {
        "id": message_id,
        "externalId": _first_present_value(data, MESSAGE_EXTERNAL_ID_KEYS),
        "body": body,
        "type": message_type or ("text" if _string_value(body) else None),
        "timestamp": timestamp,
    }

    if isinstance(media, dict):
        message["media"] = media

    for key in MESSAGE_MEDIA_KEYS:
        value = data.get(key)
        if value is not None and key not in message:
            message[key] = value

    return {key: value for key, value in message.items() if value is not None}
