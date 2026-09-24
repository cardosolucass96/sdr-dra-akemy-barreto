from __future__ import annotations

import re

WHATSAPP_SHORT_MESSAGE_LIMIT = 320
WHATSAPP_TARGET_MESSAGE_CHARS = 420
WHATSAPP_MAX_MESSAGE_CHARS = 760
WHATSAPP_MAX_MESSAGES = 4

_HEADING_PREFIX_PATTERN = re.compile(r"^\s{0,3}#{1,6}\s+")
_HORIZONTAL_RULE_PATTERN = re.compile(r"^\s{0,3}(?:[-*_]\s*){3,}$")
_TABLE_SEPARATOR_PATTERN = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$")
_SENTENCE_BOUNDARY_PATTERN = re.compile(r"(?<=[.!?])\s+")


def split_whatsapp_messages(text: str) -> list[str]:
    normalized_text = _normalize_whatsapp_text(text)
    if not normalized_text:
        return []

    if len(normalized_text) <= WHATSAPP_SHORT_MESSAGE_LIMIT:
        return [normalized_text]

    blocks = _split_paragraph_blocks(normalized_text)
    messages = _pack_blocks(blocks)
    return _merge_to_message_limit(messages)


def _normalize_whatsapp_text(text: str) -> str:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    lines: list[str] = []

    for line in normalized.split("\n"):
        cleaned_line = line.rstrip()
        if _HORIZONTAL_RULE_PATTERN.fullmatch(cleaned_line):
            continue
        if _TABLE_SEPARATOR_PATTERN.fullmatch(cleaned_line):
            continue
        lines.append(_HEADING_PREFIX_PATTERN.sub("", cleaned_line))

    normalized = "\n".join(lines).strip()
    return re.sub(r"\n{3,}", "\n\n", normalized)


def _split_paragraph_blocks(text: str) -> list[str]:
    return [block.strip() for block in re.split(r"\n\s*\n", text) if block.strip()]


def _pack_blocks(blocks: list[str]) -> list[str]:
    messages: list[str] = []
    current_message = ""

    for block in blocks:
        for part in _split_oversized_block(block):
            if not current_message:
                current_message = part
                continue

            candidate = f"{current_message}\n\n{part}"
            if len(candidate) <= WHATSAPP_TARGET_MESSAGE_CHARS:
                current_message = candidate
                continue

            messages.append(current_message.strip())
            current_message = part

    if current_message.strip():
        messages.append(current_message.strip())

    return [message for message in messages if message]


def _split_oversized_block(block: str) -> list[str]:
    if len(block) <= WHATSAPP_MAX_MESSAGE_CHARS:
        return [block]

    sentence_parts = [
        sentence.strip() for sentence in _SENTENCE_BOUNDARY_PATTERN.split(block) if sentence.strip()
    ]
    if len(sentence_parts) <= 1:
        return _split_by_words(block)

    messages: list[str] = []
    current_message = ""

    for sentence in sentence_parts:
        if len(sentence) > WHATSAPP_MAX_MESSAGE_CHARS:
            if current_message:
                messages.append(current_message.strip())
                current_message = ""
            messages.extend(_split_by_words(sentence))
            continue

        if not current_message:
            current_message = sentence
            continue

        candidate = f"{current_message} {sentence}"
        if len(candidate) <= WHATSAPP_MAX_MESSAGE_CHARS:
            current_message = candidate
            continue

        messages.append(current_message.strip())
        current_message = sentence

    if current_message.strip():
        messages.append(current_message.strip())

    return [message for message in messages if message]


def _split_by_words(text: str) -> list[str]:
    words = re.findall(r"\S+", text)
    messages: list[str] = []
    current_message = ""

    for word in words:
        if not current_message:
            current_message = word
            continue

        candidate = f"{current_message} {word}"
        if len(candidate) <= WHATSAPP_MAX_MESSAGE_CHARS:
            current_message = candidate
            continue

        messages.append(current_message.strip())
        current_message = word

    if current_message.strip():
        messages.append(current_message.strip())

    return [message for message in messages if message]


def _merge_to_message_limit(messages: list[str]) -> list[str]:
    merged_messages = [message.strip() for message in messages if message.strip()]

    while len(merged_messages) > WHATSAPP_MAX_MESSAGES:
        merge_index = min(
            range(len(merged_messages) - 1),
            key=lambda index: len(merged_messages[index]) + len(merged_messages[index + 1]),
        )
        merged_messages[merge_index] = (
            f"{merged_messages[merge_index]}\n\n{merged_messages[merge_index + 1]}"
        )
        del merged_messages[merge_index + 1]

    return merged_messages


__all__ = [
    "WHATSAPP_MAX_MESSAGE_CHARS",
    "WHATSAPP_MAX_MESSAGES",
    "WHATSAPP_SHORT_MESSAGE_LIMIT",
    "WHATSAPP_TARGET_MESSAGE_CHARS",
    "split_whatsapp_messages",
]
