from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from langchain_core.messages import BaseMessage, HumanMessage

from app.agent import get_thread_state, run_agent, serialize_thread_state
from app.application.delivery import build_response_parts
from app.application.dto import (
    ChatTurnResult,
    ResponseAudioResult,
    SerializedMessageResult,
    ThreadStateResult,
)
from app.application.whatsapp import split_whatsapp_messages
from app.core.config import Settings

MessageContent = str | list[dict[str, Any]]


def run_chat_turn(
    *,
    message: MessageContent,
    thread_id: str,
    graph=None,
    session_id: str | None = None,
    user_id: str | None = None,
    metadata: dict[str, object] | None = None,
    tags: tuple[str, ...] | None = None,
    settings: Settings | None = None,
) -> ChatTurnResult:
    result = run_agent(
        {"messages": [HumanMessage(content=message)]},
        session_id=session_id or thread_id,
        user_id=user_id,
        tags=tags,
        metadata=metadata,
        config={"configurable": {"thread_id": thread_id}},
        graph=graph,
        settings=settings,
    )
    return _chat_turn_result(result, thread_id=thread_id)


def run_chat_turn_from_history(
    *,
    history: Sequence[BaseMessage | dict[str, Any]],
    thread_id: str,
    resume_context: str | None = None,
    graph=None,
    session_id: str | None = None,
    user_id: str | None = None,
    metadata: dict[str, object] | None = None,
    tags: tuple[str, ...] | None = None,
    settings: Settings | None = None,
) -> ChatTurnResult:
    state: dict[str, Any] = {"messages": list(history)}
    normalized_context = (resume_context or "").strip()
    if normalized_context:
        state["resume_context"] = normalized_context

    result = run_agent(
        state,
        session_id=session_id or thread_id,
        user_id=user_id,
        tags=tags,
        metadata=metadata,
        config={"configurable": {"thread_id": thread_id}},
        graph=graph,
        settings=settings,
        replace_messages=True,
    )
    return _chat_turn_result(result, thread_id=thread_id)


def _chat_turn_result(result: dict[str, Any], *, thread_id: str) -> ChatTurnResult:
    response_text = result.get("response_text", "")
    response_messages = split_whatsapp_messages(response_text)
    response_audio = _response_audio_from_state(result.get("response_audio"))
    return ChatTurnResult(
        thread_id=thread_id,
        intent=result.get("intent"),
        intent_reason=result.get("intent_reason"),
        response_text=response_text,
        status=result.get("status", ""),
        response_messages=response_messages,
        response_parts=build_response_parts(
            response_messages=response_messages,
            response_media=list(result.get("response_media") or []),
        ),
        response_audio=response_audio,
    )


def _response_audio_from_state(value: object) -> ResponseAudioResult | None:
    if not isinstance(value, dict):
        return None

    text = str(value.get("text") or "").strip()
    reason = str(value.get("reason") or "").strip()
    if not text or not reason:
        return None

    return ResponseAudioResult(text=text, reason=reason)


def fetch_thread_state(
    thread_id: str,
    *,
    graph=None,
) -> ThreadStateResult | None:
    snapshot = get_thread_state(thread_id, graph=graph)
    if snapshot is None:
        return None

    payload = serialize_thread_state(snapshot)
    return ThreadStateResult(
        thread_id=payload["thread_id"],
        latest_user_message=payload.get("latest_user_message"),
        intent=payload.get("intent"),
        intent_reason=payload.get("intent_reason"),
        response_text=payload.get("response_text"),
        status=payload.get("status"),
        messages=[
            SerializedMessageResult(
                role=message["role"],
                content=message["content"],
            )
            for message in payload.get("messages", [])
        ],
    )
