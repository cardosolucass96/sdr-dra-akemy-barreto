from __future__ import annotations

import logging
from contextvars import ContextVar
from typing import Any

from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime

from app.agent.chains import build_outbound_media_classifier_chain
from app.agent.context import AgentRunContext, default_agent_run_context
from app.agent.response_support import (
    LIANA_INITIAL_GREETING,
    build_outbound_media_prompt_view,
    build_responder_chain,
    build_response_update,
    get_enabled_outbound_media_by_id,
    get_whatsapp_style_prompt_text,
)
from app.agent.state import AgentState

LOGGER = logging.getLogger(__name__)
_RUNTIME_SETTINGS: ContextVar[Any] = ContextVar("response_runtime_settings", default=None)


def _build_responder_chain(*, use_custom_temperature: bool = True):
    return build_responder_chain(
        _RUNTIME_SETTINGS.get(),
        use_custom_temperature=use_custom_temperature,
    )


def _build_outbound_media_classifier_chain(*, use_custom_temperature: bool = True):
    return build_outbound_media_classifier_chain(
        _RUNTIME_SETTINGS.get(),
        use_custom_temperature=use_custom_temperature,
    )


def _get_response_style() -> str:
    settings = _RUNTIME_SETTINGS.get()
    label = getattr(settings, "langfuse_prompt_label", None)
    _, response_style = get_whatsapp_style_prompt_text(label=label)
    return response_style


def _get_available_media_prompt_view() -> str:
    return build_outbound_media_prompt_view()


def _initial_greeting_response(state: AgentState) -> dict[str, Any] | None:
    messages = list(state.get("messages", []))
    if state.get("intent") != "greeting" or len(messages) != 1:
        return None

    return {
        "latest_user_message": state.get("latest_user_message", ""),
        "response_text": LIANA_INITIAL_GREETING,
        "response_media": [],
        "response_audio": None,
        "messages": [AIMessage(content=LIANA_INITIAL_GREETING)],
        "status": "responded",
    }


def respond(
    state: AgentState,
    config: RunnableConfig = None,
    runtime: Runtime[AgentRunContext] = None,
) -> dict[str, Any]:
    initial_greeting = _initial_greeting_response(state)
    if initial_greeting is not None:
        return initial_greeting

    context = runtime.context if runtime and runtime.context else default_agent_run_context()
    token = _RUNTIME_SETTINGS.set(context.settings)
    try:
        return build_response_update(
            state,
            config=config,
            chain_factory=_build_responder_chain,
            media_selection_chain_factory=_build_outbound_media_classifier_chain,
            response_style=_get_response_style(),
            available_media=_get_available_media_prompt_view(),
            media_by_id_loader=get_enabled_outbound_media_by_id,
            logger=LOGGER,
        )
    finally:
        _RUNTIME_SETTINGS.reset(token)
