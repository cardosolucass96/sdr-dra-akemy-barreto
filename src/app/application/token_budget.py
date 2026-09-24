from __future__ import annotations

import math
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from app.agent.messages import message_to_text

APPROX_CHARS_PER_TOKEN = 4


@dataclass(frozen=True, slots=True)
class LeadTokenUsage:
    current_tokens: int
    incoming_tokens: int
    total_tokens: int
    max_tokens: int

    @property
    def exceeded(self) -> bool:
        return self.max_tokens > 0 and self.total_tokens >= self.max_tokens


def normalize_max_tokens(max_tokens: int | None) -> int:
    if max_tokens is None or max_tokens <= 0:
        return 0
    return max_tokens


def count_text_tokens(text: str, *, model_name: str | None = None) -> int:
    normalized_text = text.strip()
    if not normalized_text:
        return 0

    encoder = _resolve_tiktoken_encoder(model_name)
    if encoder is not None:
        return len(encoder.encode(normalized_text))

    return max(1, math.ceil(len(normalized_text) / APPROX_CHARS_PER_TOKEN))


def count_messages_tokens(messages: list[Any], *, model_name: str | None = None) -> int:
    return sum(
        count_text_tokens(_message_text(message), model_name=model_name) for message in messages
    )


def build_lead_token_usage(
    *,
    messages: list[Any],
    incoming_text: str,
    max_tokens: int | None,
    model_name: str | None = None,
) -> LeadTokenUsage:
    normalized_max_tokens = normalize_max_tokens(max_tokens)
    current_tokens = count_messages_tokens(messages, model_name=model_name)
    incoming_tokens = count_text_tokens(incoming_text, model_name=model_name)
    return LeadTokenUsage(
        current_tokens=current_tokens,
        incoming_tokens=incoming_tokens,
        total_tokens=current_tokens + incoming_tokens,
        max_tokens=normalized_max_tokens,
    )


def _message_text(message: Any) -> str:
    if isinstance(message, dict):
        return message_to_text(message)

    if hasattr(message, "content"):
        content = message.content
        if isinstance(content, str):
            return content
        return message_to_text(message)

    return message_to_text(message)


@lru_cache(maxsize=16)
def _resolve_tiktoken_encoder(model_name: str | None):
    try:
        import tiktoken
    except ImportError:
        return None

    if model_name:
        try:
            return tiktoken.encoding_for_model(model_name)
        except KeyError:
            pass

    for encoding_name in ("o200k_base", "cl100k_base"):
        try:
            return tiktoken.get_encoding(encoding_name)
        except ValueError:
            continue

    return None
