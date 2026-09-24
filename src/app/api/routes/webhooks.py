from __future__ import annotations

import logging
import time
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Response, status

from app.api.dependencies import (
    get_graph,
    get_pipefacil_message_idempotency_store,
    get_settings,
)
from app.api.schemas.chat import ChatResponse
from app.api.schemas.webhooks import MessageReceivedEventRequest
from app.api.webhook_signature import verify_pipefacil_webhook_signature
from app.application import (
    MessageIdempotencyStore,
    PipefacilInboundMessageError,
    build_pipefacil_message_received_log_context,
    build_pipefacil_message_received_raw_log_payload,
    handle_pipefacil_message_received,
    validate_pipefacil_message_received,
)
from app.core.config import Settings
from app.core.logging import raw_log_value

webhooks_router = APIRouter()
GraphDep = Annotated[Any, Depends(get_graph)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
IdempotencyStoreDep = Annotated[
    MessageIdempotencyStore,
    Depends(get_pipefacil_message_idempotency_store),
]
LOGGER = logging.getLogger(__name__)


@webhooks_router.post(
    "/events/message-received",
    response_model=ChatResponse,
    dependencies=[Depends(verify_pipefacil_webhook_signature)],
)
def message_received(
    payload: MessageReceivedEventRequest,
    background_tasks: BackgroundTasks,
    graph: GraphDep,
    settings: SettingsDep,
    idempotency_store: IdempotencyStoreDep,
    response: Response,
) -> ChatResponse:
    received_extra = {
        **build_pipefacil_message_received_log_context(payload),
        "pipeline_step": "pipefacil.webhook.received",
    }
    if settings.log_inbound_payloads:
        received_extra["raw_payload"] = raw_log_value(
            build_pipefacil_message_received_raw_log_payload(payload)
        )

    LOGGER.info("pipefacil.webhook.received", extra=received_extra)

    try:
        thread_id = validate_pipefacil_message_received(payload)
    except PipefacilInboundMessageError as exc:
        LOGGER.warning(
            "pipefacil.webhook.rejected",
            extra={
                **build_pipefacil_message_received_log_context(payload),
                "pipeline_step": "pipefacil.webhook.rejected",
                "error_code": "pipefacil_inbound_error",
                "error_detail": str(exc),
                "status_code": status.HTTP_422_UNPROCESSABLE_CONTENT,
            },
        )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc

    background_tasks.add_task(
        _process_pipefacil_message_received,
        payload,
        thread_id=thread_id,
        graph=graph,
        settings=settings,
        idempotency_store=idempotency_store,
    )
    response.status_code = status.HTTP_200_OK
    LOGGER.info(
        "pipefacil.webhook.accepted",
        extra={
            **build_pipefacil_message_received_log_context(payload, thread_id=thread_id),
            "pipeline_step": "pipefacil.webhook.accepted",
            "status_code": status.HTTP_200_OK,
        },
    )

    return ChatResponse(
        thread_id=thread_id,
        intent=None,
        intent_reason="Pipefacil message accepted for background processing.",
        response_text="",
        status="accepted",
    )


def _process_pipefacil_message_received(
    payload: MessageReceivedEventRequest,
    *,
    thread_id: str,
    graph: Any,
    settings: Settings,
    idempotency_store: MessageIdempotencyStore,
) -> None:
    start = time.perf_counter()
    try:
        result = handle_pipefacil_message_received(
            payload,
            graph=graph,
            settings=settings,
            idempotency_store=idempotency_store,
        )
    except Exception as exc:
        LOGGER.exception(
            "pipefacil.webhook.processing_failed",
            extra={
                **build_pipefacil_message_received_log_context(payload, thread_id=thread_id),
                "pipeline_step": "pipefacil.webhook.processing_failed",
                "error_code": (
                    "pipefacil_inbound_error"
                    if isinstance(exc, PipefacilInboundMessageError)
                    else "unexpected_background_processing_error"
                ),
                "error_detail": str(exc),
                "duration_ms": round((time.perf_counter() - start) * 1000, 2),
            },
        )
        return

    LOGGER.info(
        "pipefacil.webhook.processing_completed",
        extra={
            **build_pipefacil_message_received_log_context(
                payload,
                thread_id=result.thread_id,
                delivery_status=result.delivery_status,
            ),
            "pipeline_step": "pipefacil.webhook.processing_completed",
            "error_code": result.delivery_error,
            "duration_ms": round((time.perf_counter() - start) * 1000, 2),
        },
    )
