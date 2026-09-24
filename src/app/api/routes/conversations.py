from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.dependencies import get_graph, get_settings
from app.api.presenters import chat_turn_response_payload
from app.api.routes.webhooks import verify_pipefacil_webhook_signature
from app.api.schemas.conversations import (
    ConversationResumeRequest,
    ConversationResumeResponse,
)
from app.application import (
    PipefacilConversationResumeError,
    handle_pipefacil_conversation_resume,
)
from app.core.config import Settings

conversations_router = APIRouter(prefix="/conversations", tags=["conversations"])
GraphDep = Annotated[Any, Depends(get_graph)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


@conversations_router.post(
    "/resume",
    response_model=ConversationResumeResponse,
    dependencies=[Depends(verify_pipefacil_webhook_signature)],
)
def resume_conversation(
    payload: ConversationResumeRequest,
    graph: GraphDep,
    settings: SettingsDep,
) -> ConversationResumeResponse:
    try:
        result = handle_pipefacil_conversation_resume(
            thread_id=payload.thread_id,
            resume_context=payload.context,
            deal_seq=payload.deal_seq,
            deal_id=payload.deal_id,
            contact_id=payload.contact_id,
            channel_id=payload.channel_id,
            recipient_phone=payload.recipient_phone,
            sender_phone_number_id=payload.sender_phone_number_id,
            profile_name=payload.profile_name,
            send_response=payload.send_response,
            history_limit=payload.history_limit,
            graph=graph,
            settings=settings,
        )
    except PipefacilConversationResumeError as exc:
        error_status = status.HTTP_422_UNPROCESSABLE_CONTENT
        if exc.error_code in {"pipefacil_transport_error", "pipefacil_upstream_error"}:
            error_status = status.HTTP_502_BAD_GATEWAY
        raise HTTPException(
            status_code=error_status,
            detail={
                "message": str(exc),
                "error_code": exc.error_code,
                "upstream_status_code": exc.status_code,
                "request_id": exc.request_id,
            },
        ) from exc
    return ConversationResumeResponse(
        **chat_turn_response_payload(result.response),
        history_message_count=result.history_message_count,
    )
