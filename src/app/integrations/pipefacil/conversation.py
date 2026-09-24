from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

ConversationRole = Literal["user", "assistant"]

_CONTENT_KEYS = (
    "body",
    "text",
    "content",
    "messageBody",
    "message_body",
    "transcription",
    "transcript",
)
_ID_KEYS = ("id", "messageId", "message_id", "externalId", "external_id")
_TIMESTAMP_KEYS = (
    "timestamp",
    "messageTimestamp",
    "message_timestamp",
    "createdAt",
    "created_at",
    "sentAt",
    "sent_at",
)
_ROLE_KEYS = (
    "role",
    "messageRole",
    "message_role",
    "senderRole",
    "sender_role",
    "senderType",
    "sender_type",
)
_DIRECTION_KEYS = ("direction", "messageDirection", "message_direction")
_USER_ROLE_VALUES = {
    "client",
    "contact",
    "customer",
    "from_contact",
    "in",
    "inbound",
    "incoming",
    "lead",
    "received",
    "user",
}
_ASSISTANT_ROLE_VALUES = {
    "agent",
    "ai",
    "assistant",
    "bot",
    "from_agent",
    "out",
    "outbound",
    "outgoing",
    "sent",
    "seller",
    "team",
}
_BOOLEAN_USER_KEYS = (
    "isIncoming",
    "is_incoming",
    "received",
    "incoming",
    "isInbound",
    "is_inbound",
)
_BOOLEAN_ASSISTANT_KEYS = (
    "fromMe",
    "from_me",
    "isFromMe",
    "is_from_me",
    "sentByMe",
    "sent_by_me",
    "isOutgoing",
    "is_outgoing",
    "outgoing",
)
_COLLECTION_KEYS = (
    "messages",
    "items",
    "results",
    "records",
    "conversationMessages",
    "conversation_messages",
)


@dataclass(frozen=True, slots=True)
class PipefacilConversationMessage:
    role: ConversationRole
    content: str
    message_id: str | None = None
    timestamp: str | None = None


@dataclass(frozen=True, slots=True)
class PipefacilConversationHistory:
    messages: list[PipefacilConversationMessage]
    contact_phone: str | None = None
    channel_id: str | None = None
    sender_phone_number_id: str | None = None
    profile_name: str | None = None


class PipefacilConversationHistoryError(ValueError):
    """Raised when Pipefacil history cannot be converted into chat messages."""


def normalize_conversation_history(payload: Any) -> PipefacilConversationHistory:
    items = _extract_message_items(payload)
    messages: list[PipefacilConversationMessage] = []
    seen_ids: set[str] = set()

    for index, item in enumerate(items):
        if not isinstance(item, Mapping):
            continue

        message_id = _first_string(item, _ID_KEYS)
        if message_id and message_id in seen_ids:
            continue

        content = _message_content(item)
        if not content:
            continue

        role = _message_role(item)
        if role is None:
            identifier = f" '{message_id}'" if message_id else f" at index {index}"
            raise PipefacilConversationHistoryError(
                f"Conversation history message{identifier} has no recognized role."
            )

        if message_id:
            seen_ids.add(message_id)
        messages.append(
            PipefacilConversationMessage(
                role=role,
                content=content,
                message_id=message_id,
                timestamp=_timestamp(item),
            )
        )

    if messages and all(message.timestamp for message in messages):
        messages.sort(key=lambda message: message.timestamp or "")

    return PipefacilConversationHistory(
        messages=messages,
        contact_phone=_metadata_value(payload, "contact", ("phone", "phoneNumber")),
        channel_id=_metadata_value(payload, "channel", ("id", "channelId", "channel_id")),
        sender_phone_number_id=_metadata_value(
            payload,
            "channel",
            ("phoneNumberId", "phone_number_id", "senderPhoneNumberId"),
        ),
        profile_name=_metadata_value(payload, "contact", ("name", "profileName")),
    )


def _extract_message_items(payload: Any, *, depth: int = 0) -> list[Any]:
    if depth > 3:
        return []
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, Mapping):
        return []

    for key in ("data", *_COLLECTION_KEYS):
        value = payload.get(key)
        if isinstance(value, list):
            return value
        if isinstance(value, Mapping):
            nested_items = _extract_message_items(value, depth=depth + 1)
            if nested_items:
                return nested_items

    return [payload] if _looks_like_message(payload) else []


def _looks_like_message(payload: Mapping[str, Any]) -> bool:
    return any(key in payload for key in (*_CONTENT_KEYS, *_ROLE_KEYS, *_DIRECTION_KEYS))


def _message_content(payload: Mapping[str, Any]) -> str:
    for key in _CONTENT_KEYS:
        value = payload.get(key)
        text = _text_value(value)
        if text:
            return text

    message_type = _first_string(payload, ("type", "messageType", "message_type", "kind"))
    if message_type:
        return f"[Mensagem de {message_type.strip().lower()}]"
    return ""


def _text_value(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, Mapping):
        for key in _CONTENT_KEYS:
            text = _text_value(value.get(key))
            if text:
                return text
        return ""
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        parts = [_text_value(item) for item in value]
        return "\n".join(part for part in parts if part).strip()
    return ""


def _message_role(payload: Mapping[str, Any]) -> ConversationRole | None:
    for key in _ROLE_KEYS:
        role = _role_value(payload.get(key))
        if role:
            return role

    for key in _DIRECTION_KEYS:
        role = _role_value(payload.get(key))
        if role:
            return role

    for key in _BOOLEAN_ASSISTANT_KEYS:
        if isinstance(payload.get(key), bool):
            return "assistant" if payload[key] else "user"

    for key in _BOOLEAN_USER_KEYS:
        if isinstance(payload.get(key), bool):
            return "user" if payload[key] else "assistant"

    for key in ("sender", "author", "from"):
        value = payload.get(key)
        if isinstance(value, Mapping):
            nested_type = _role_value(value.get("type"))
            if nested_type:
                return nested_type
            nested_role = _message_role(value)
            if nested_role:
                return nested_role
        else:
            role = _role_value(value)
            if role:
                return role

    return None


def _role_value(value: Any) -> ConversationRole | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower().replace("-", "_").replace(" ", "_")
    if normalized in _USER_ROLE_VALUES:
        return "user"
    if normalized in _ASSISTANT_ROLE_VALUES:
        return "assistant"
    return None


def _first_string(payload: Mapping[str, Any], keys: Sequence[str]) -> str | None:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _timestamp(payload: Mapping[str, Any]) -> str | None:
    for key in _TIMESTAMP_KEYS:
        value = payload.get(key)
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _metadata_value(
    payload: Any,
    container_key: str,
    keys: Sequence[str],
) -> str | None:
    for source in _metadata_sources(payload):
        container = source.get(container_key)
        if isinstance(container, Mapping):
            value = _first_string(container, keys)
            if value:
                return value
    return None


def _metadata_sources(payload: Any) -> list[Mapping[str, Any]]:
    if not isinstance(payload, Mapping):
        return []

    sources = [payload]
    for key in ("data", "conversation", "thread"):
        value = payload.get(key)
        if isinstance(value, Mapping):
            sources.append(value)
    return sources


__all__ = [
    "PipefacilConversationHistory",
    "PipefacilConversationHistoryError",
    "PipefacilConversationMessage",
    "normalize_conversation_history",
]
