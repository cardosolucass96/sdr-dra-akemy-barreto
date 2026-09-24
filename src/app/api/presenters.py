from __future__ import annotations

from dataclasses import asdict
from typing import Any

from app.application.dto import ChatTurnResult

INTERNAL_CHAT_TURN_FIELDS = {
    "response_audio",
}


def chat_turn_response_payload(result: ChatTurnResult) -> dict[str, Any]:
    payload = asdict(result)
    for field_name in INTERNAL_CHAT_TURN_FIELDS:
        payload.pop(field_name, None)

    return payload
