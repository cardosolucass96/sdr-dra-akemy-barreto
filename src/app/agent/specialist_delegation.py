"""Specialist delegation work kept outside the LangGraph node boundary."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.agent.messages import latest_user_message, serialize_messages
from app.agent.specialists import (
    DEFAULT_SPECIALIST_REGISTRY,
    SpecialistRequest,
    SpecialistResult,
    failed_specialist_result,
    skipped_specialist_result,
)
from app.agent.state import AgentState


def build_specialist_request(state: AgentState) -> SpecialistRequest:
    latest_message = state.get("latest_user_message") or latest_user_message(state)
    return SpecialistRequest(
        objective=state.get("specialist_reason") or "Run specialist analysis for this turn.",
        latest_user_message=latest_message,
        intent=state.get("intent"),
        intent_reason=state.get("intent_reason"),
        conversation_history=serialize_messages(state.get("messages", [])),
    )


def build_specialist_update(
    agent_state: AgentState,
    *,
    thread_id: str | None,
    settings: Any,
    request_builder: Callable[[AgentState], SpecialistRequest],
    specialist_runner: Callable[..., SpecialistResult],
    logger: Any,
) -> dict[str, Any]:
    requested_name = agent_state.get("specialist_name")
    if not agent_state.get("requires_specialist") or not requested_name:
        return _logged_update(
            skipped_specialist_result("specialist_not_required"),
            thread_id=thread_id,
            logger=logger,
        )
    specialist_name = DEFAULT_SPECIALIST_REGISTRY.resolve_name(requested_name)
    if specialist_name is None:
        return _logged_update(
            failed_specialist_result("specialist_unknown"),
            thread_id=thread_id,
            specialist_name=requested_name,
            logger=logger,
        )
    if not settings.openai_specialists_enabled:
        return _logged_update(
            skipped_specialist_result("openai_specialists_disabled"),
            thread_id=thread_id,
            specialist_name=specialist_name,
            logger=logger,
        )
    try:
        request = request_builder(agent_state)
    except ValueError as exc:
        return _logged_update(
            failed_specialist_result("specialist_request_invalid", summary=str(exc)),
            thread_id=thread_id,
            specialist_name=specialist_name,
            logger=logger,
        )
    logger.info(
        "specialist.run.started",
        extra={
            "thread_id": thread_id,
            "specialist_name": specialist_name,
            "intent": agent_state.get("intent"),
        },
    )
    result = specialist_runner(
        specialist_name=specialist_name,
        request=request,
        settings=settings,
    )
    return _logged_update(
        result,
        thread_id=thread_id,
        specialist_name=specialist_name,
        logger=logger,
    )


def _logged_update(
    result: SpecialistResult,
    *,
    thread_id: str | None,
    logger: Any,
    specialist_name: str | None = None,
) -> dict[str, Any]:
    details = {
        "thread_id": thread_id,
        "specialist_name": specialist_name,
        "specialist_status": result.status,
        "specialist_confidence": result.confidence,
        "specialist_error_code": result.error_code,
    }
    if result.status == "failed":
        logger.warning("specialist.run.failed", extra=details)
    elif result.status == "skipped":
        logger.info("specialist.run.skipped", extra=details)
    else:
        logger.info("specialist.run.completed", extra=details)
    update = {
        "specialist_status": result.status,
        "specialist_result": result.model_dump(mode="json"),
    }
    if specialist_name is not None:
        update["specialist_name"] = specialist_name
    return update
