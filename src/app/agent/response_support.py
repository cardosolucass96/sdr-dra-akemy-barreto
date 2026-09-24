"""Response construction helpers kept outside the LangGraph node boundary."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from openai import BadRequestError

from app.agent.chains import (
    build_responder_chain,
    invoke_with_temperature_fallback,
)
from app.agent.chains.schemas import (
    AgentResponsePlan,
    OutboundMediaChoice,
    OutboundMediaClassification,
)
from app.agent.messages import latest_user_message, message_to_text, serialize_messages
from app.agent.prompts import get_whatsapp_style_prompt_text
from app.outbound_media import (
    OUTBOUND_MEDIA_CATALOG_UNAVAILABLE_TEXT,
    build_outbound_media_prompt_view,
    get_enabled_outbound_media_by_id,
)


def infer_catalog_media_choice(
    latest_message: str,
    conversation_history: list[Any],
    available_media: str,
    *,
    config: RunnableConfig | None,
    chain_factory: Callable[..., Any],
    media_by_id_loader: Callable[[], Mapping[str, Any]] = get_enabled_outbound_media_by_id,
    logger: Any,
) -> OutboundMediaChoice | None:
    """Classify an optional catalog send, then constrain it to the local allowlist."""

    if available_media.strip() == OUTBOUND_MEDIA_CATALOG_UNAVAILABLE_TEXT:
        return None

    enabled_media = media_by_id_loader()
    if not enabled_media:
        return None

    try:
        response = invoke_with_temperature_fallback(
            chain_factory,
            {
                "latest_user_message": latest_message,
                "conversation_history": serialize_messages(conversation_history),
                "available_media": available_media,
            },
            config=config,
        )
        classification = _response_to_media_classification(response)
    except Exception:
        logger.warning(
            "agent.outbound_media.classification_failed",
            extra={"pipeline_step": "agent.outbound_media.classification_failed"},
            exc_info=True,
        )
        return None

    if classification.action == "none":
        return None

    media_id = classification.media_id
    if media_id is None:  # Guarded by the schema; retained as a fail-closed boundary.
        return None
    asset = enabled_media.get(media_id.strip())
    if asset is None:
        logger.warning(
            "agent.outbound_media.ignored",
            extra={
                "pipeline_step": "agent.outbound_media.ignored",
                "media_id": media_id,
                "reason": "unknown_or_disabled_media",
            },
        )
        return None

    return OutboundMediaChoice(
        media_id=asset.id,
        reason=classification.reason,
    )


def _response_to_media_classification(response: Any) -> OutboundMediaClassification:
    if isinstance(response, OutboundMediaClassification):
        return response
    if isinstance(response, dict):
        return OutboundMediaClassification.model_validate(response)
    model_dump = getattr(response, "model_dump", None)
    if callable(model_dump):
        return OutboundMediaClassification.model_validate(model_dump())
    raise TypeError("Outbound media classifier returned an unsupported response.")


def _response_to_plan(response: Any) -> AgentResponsePlan:
    if isinstance(response, AgentResponsePlan):
        return response
    if isinstance(response, dict) and "response_text" in response:
        return AgentResponsePlan.model_validate(response)
    response_text = (
        message_to_text(response) if isinstance(response, BaseMessage) else str(response)
    )
    return AgentResponsePlan(response_text=response_text, media_choices=[])


def _conversation_history_with_delivery_context(
    messages: list[Any],
    *,
    available_media: str,
) -> list[Any]:
    safe_media_context = available_media.strip()
    if not safe_media_context or safe_media_context == OUTBOUND_MEDIA_CATALOG_UNAVAILABLE_TEXT:
        return messages
    return [
        SystemMessage(
            content=(
                "Delivery capability for this turn:\n"
                "You can select outbound media from the catalog by returning media_choices "
                "in the structured response. The application will send selected media after "
                "your text reply.\n"
                "If the user asks for an audio, image, video, or document and a catalog item "
                "matches, choose that media_id. Do not say you cannot send media when a "
                "matching catalog item is available.\n"
                "Use only media_id values listed here. Never invent URLs, filenames, or raw "
                f"file content.\nAvailable outbound media:\n{safe_media_context}"
            )
        ),
        *messages,
    ]


def _validate_media_choices(
    choices: list[OutboundMediaChoice],
    *,
    media_by_id_loader: Callable[[], Mapping[str, Any]],
    logger: Any,
) -> list[dict[str, Any]]:
    enabled_media = media_by_id_loader()
    selected_media: list[dict[str, Any]] = []
    selected_ids: set[str] = set()
    for choice in choices:
        media_id = choice.media_id.strip()
        asset = enabled_media.get(media_id)
        if asset is None:
            logger.warning(
                "agent.outbound_media.ignored",
                extra={
                    "pipeline_step": "agent.outbound_media.ignored",
                    "media_id": media_id,
                    "reason": "unknown_or_disabled_media",
                },
            )
            continue
        if media_id in selected_ids:
            logger.warning(
                "agent.outbound_media.ignored",
                extra={
                    "pipeline_step": "agent.outbound_media.ignored",
                    "media_id": media_id,
                    "reason": "duplicate_media",
                },
            )
            continue
        selected_ids.add(media_id)
        selected_media.append(
            {
                "media_id": asset.id,
                "type": asset.type,
                "caption": choice.caption.strip() if choice.caption else None,
                "reason": choice.reason,
                "content_type": asset.content_type,
                "filename": asset.filename,
            }
        )
    return selected_media


def _has_file_content(messages: list[Any]) -> bool:
    for message in messages:
        content = message.get("content", "") if isinstance(message, dict) else message.content
        if isinstance(content, list) and any(
            isinstance(block, dict) and block.get("type") == "file" for block in content
        ):
            return True
    return False


def _invoke_responder_with_file_fallback(
    payload: dict[str, Any],
    *,
    config: RunnableConfig | None,
    chain_factory: Callable[..., Any],
) -> Any:
    try:
        return invoke_with_temperature_fallback(chain_factory, payload, config=config)
    except BadRequestError:
        if not _has_file_content(list(payload.get("conversation_history", []))):
            raise
    retry_payload = dict(payload)
    retry_payload["conversation_history"] = serialize_messages(
        list(payload.get("conversation_history", []))
    )
    return invoke_with_temperature_fallback(chain_factory, retry_payload, config=config)


def build_response_update(
    agent_state: Mapping[str, Any],
    *,
    config: RunnableConfig | None,
    chain_factory: Callable[..., Any],
    media_selection_chain_factory: Callable[..., Any],
    response_style: str,
    available_media: str,
    media_by_id_loader: Callable[[], Mapping[str, Any]],
    logger: Any,
) -> dict[str, Any]:
    latest_message = agent_state.get("latest_user_message") or latest_user_message(agent_state)
    if not latest_message:
        response_text = "Ainda nao recebi nenhuma mensagem do usuario."
        return {
            "latest_user_message": "",
            "response_text": response_text,
            "response_media": [],
            "messages": [AIMessage(content=response_text)],
            "status": "responded",
        }
    conversation_history = _conversation_history_with_delivery_context(
        list(agent_state.get("messages", [])),
        available_media=available_media,
    )
    response = _invoke_responder_with_file_fallback(
        {
            "intent": agent_state.get("intent", "fallback"),
            "latest_user_message": latest_message,
            "conversation_history": conversation_history,
            "specialist_result": agent_state.get("specialist_result"),
            "specialist_context": json.dumps(
                agent_state.get("specialist_result") or {},
                ensure_ascii=False,
                separators=(",", ":"),
            )
            if agent_state.get("specialist_result")
            else "No specialist result.",
            "resume_context": str(agent_state.get("resume_context") or "").strip()
            or "No additional resume context.",
            "response_style": response_style,
            "available_media": available_media,
        },
        config=config,
        chain_factory=chain_factory,
    )
    response_plan = _response_to_plan(response)
    response_media = _validate_media_choices(
        response_plan.media_choices,
        media_by_id_loader=media_by_id_loader,
        logger=logger,
    )
    if not response_media:
        inferred_media = infer_catalog_media_choice(
            latest_message,
            list(agent_state.get("messages", [])),
            available_media,
            config=config,
            chain_factory=media_selection_chain_factory,
            media_by_id_loader=media_by_id_loader,
            logger=logger,
        )
        if inferred_media is not None:
            response_media = _validate_media_choices(
                [inferred_media],
                media_by_id_loader=media_by_id_loader,
                logger=logger,
            )
            logger.info(
                "agent.outbound_media.inferred",
                extra={
                    "pipeline_step": "agent.outbound_media.inferred",
                    "media_id": inferred_media.media_id,
                    "reason": inferred_media.reason,
                },
            )
    response_audio = None
    if not any(media.get("type") == "audio" for media in response_media):
        response_audio = (
            response_plan.generated_audio.model_dump()
            if response_plan.generated_audio is not None
            else None
        )
    response_text = response_plan.response_text
    response_message = (
        response if isinstance(response, BaseMessage) else AIMessage(content=response_text)
    )
    return {
        "latest_user_message": latest_message,
        "response_text": response_text,
        "response_media": response_media,
        "response_audio": response_audio,
        "messages": [response_message],
        "status": "responded",
    }


__all__ = [
    "build_outbound_media_prompt_view",
    "build_responder_chain",
    "build_response_update",
    "get_enabled_outbound_media_by_id",
    "get_whatsapp_style_prompt_text",
    "infer_catalog_media_choice",
]
