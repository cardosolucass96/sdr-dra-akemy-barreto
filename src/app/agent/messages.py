from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from langchain_core.messages import BaseMessage

from app.agent.state import AgentState

ROLE_ALIASES = {
    "human": "user",
    "ai": "assistant",
}
SENSITIVE_MULTIMODAL_BLOCK_TYPES = {"audio", "file", "image", "video"}
SENSITIVE_MULTIMODAL_KEYS = {"base64", "data", "file_data", "url"}
MessageLike = BaseMessage | dict[str, Any]


def message_content(message: MessageLike) -> str:
    return message_to_text(message)


def message_role(message: MessageLike) -> str:
    message_type = (
        str(message.get("type", "message"))
        if isinstance(message, dict)
        else getattr(message, "type", "message")
    )
    return ROLE_ALIASES.get(message_type, message_type)


def message_to_text(message: MessageLike) -> str:
    content = message.get("content", "") if isinstance(message, dict) else message.content

    if isinstance(content, str):
        return content

    if isinstance(content, Sequence):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
                continue

            if isinstance(block, dict):
                formatted_block = _content_block_to_text(block)
                if formatted_block:
                    parts.append(formatted_block)
                continue

            parts.append(str(block))

        return "\n".join(part for part in parts if part).strip()

    return str(content)


def _content_block_to_text(block: dict[str, Any]) -> str:
    block_type = str(block.get("type") or "content")
    text = block.get("text")
    if isinstance(text, str) and text.strip():
        return text

    if block_type in {"image", "audio", "video", "file"}:
        mime_type = block.get("mime_type") or block.get("mimeType")
        if isinstance(mime_type, str) and mime_type.strip():
            return f"[{block_type} mime_type={mime_type.strip()}]"
        return f"[{block_type}]"

    return f"[{block_type}]"


def latest_user_message(state: AgentState) -> str:
    messages = state.get("messages", [])
    if not messages:
        return ""

    for message in reversed(messages):
        if message_role(message) == "user":
            return message_to_text(message)

    return message_to_text(messages[-1])


def serialize_messages(messages: Sequence[MessageLike]) -> list[dict[str, str]]:
    serialized_messages: list[dict[str, str]] = []

    for message in messages:
        serialized_messages.append(
            {
                "role": message_role(message),
                "content": message_content(message),
            }
        )

    return serialized_messages


def has_sensitive_multimodal_content(messages: Sequence[MessageLike]) -> bool:
    for message in messages:
        content = message.get("content", "") if isinstance(message, dict) else message.content
        if not isinstance(content, Sequence) or isinstance(content, str):
            continue

        for block in content:
            if not isinstance(block, dict):
                continue

            block_type = str(block.get("type") or "").lower()
            if block_type not in SENSITIVE_MULTIMODAL_BLOCK_TYPES:
                continue

            if any(_has_present_value(block.get(key)) for key in SENSITIVE_MULTIMODAL_KEYS):
                return True

    return False


def _has_present_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, bytes):
        return bool(value)
    return True
