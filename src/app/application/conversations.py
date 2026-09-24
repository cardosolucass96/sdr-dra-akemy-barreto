from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage

from app.application.chat import run_chat_turn_from_history
from app.application.dto import ChatTurnResult
from app.application.pipefacil import (
    PipefacilResponseTarget,
    deliver_pipefacil_response,
)
from app.core.config import Settings
from app.integrations.pipefacil import (
    PipefacilConversationHistoryLookupError,
    PipefacilConversationMessage,
    PipefacilSendMessageError,
    fetch_pipefacil_conversation_history,
)


@dataclass(frozen=True, slots=True)
class PipefacilConversationResumeResult:
    response: ChatTurnResult
    history_message_count: int


class PipefacilConversationResumeError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        error_code: str,
        status_code: int | None = None,
        request_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.status_code = status_code
        self.request_id = request_id


def handle_pipefacil_conversation_resume(
    *,
    thread_id: str,
    resume_context: str | None = None,
    deal_seq: int | None = None,
    deal_id: str | None = None,
    contact_id: str | None = None,
    channel_id: str | None = None,
    recipient_phone: str | None = None,
    sender_phone_number_id: str | None = None,
    profile_name: str | None = None,
    send_response: bool = True,
    history_limit: int | None = None,
    graph: Any | None = None,
    settings: Settings,
) -> PipefacilConversationResumeResult:
    try:
        history = fetch_pipefacil_conversation_history(
            deal_seq=deal_seq,
            deal_id=deal_id,
            contact_id=contact_id,
            channel_id=channel_id,
            limit=history_limit,
            settings=settings,
        )
    except PipefacilConversationHistoryLookupError as exc:
        raise PipefacilConversationResumeError(
            str(exc),
            error_code=exc.error_code,
            status_code=exc.status_code,
            request_id=exc.request_id,
        ) from exc
    history_messages = [_to_langchain_message(message) for message in history.messages]
    normalized_context = (resume_context or "").strip()
    response = run_chat_turn_from_history(
        history=history_messages,
        thread_id=thread_id,
        resume_context=normalized_context,
        graph=graph,
        user_id=contact_id,
        metadata={
            "source": "pipefacil.conversation_resume",
            "history_message_count": len(history_messages),
            "has_resume_context": bool(normalized_context),
        },
        tags=("pipefacil", "conversation-resume"),
        settings=settings,
    )

    if send_response:
        try:
            response = deliver_pipefacil_response(
                response,
                target=PipefacilResponseTarget(
                    recipient_phone=recipient_phone or history.contact_phone,
                    channel_id=channel_id or history.channel_id,
                    sender_phone_number_id=sender_phone_number_id or history.sender_phone_number_id,
                    profile_name=profile_name or history.profile_name,
                ),
                settings=settings,
                log_context={
                    "pipeline_step": "pipefacil.conversation_resume",
                    "thread_id": thread_id,
                    "contact_id": contact_id,
                    "deal_seq": deal_seq,
                    "history_message_count": len(history_messages),
                },
            )
        except PipefacilSendMessageError as exc:
            raise PipefacilConversationResumeError(
                str(exc),
                error_code=exc.error_code,
                status_code=exc.status_code,
                request_id=exc.request_id,
            ) from exc

    return PipefacilConversationResumeResult(
        response=response,
        history_message_count=len(history_messages),
    )


def _to_langchain_message(message: PipefacilConversationMessage):
    if message.role == "assistant":
        return AIMessage(content=message.content, id=message.message_id)
    return HumanMessage(content=message.content, id=message.message_id)


__all__ = [
    "PipefacilConversationResumeError",
    "PipefacilConversationResumeResult",
    "handle_pipefacil_conversation_resume",
]
