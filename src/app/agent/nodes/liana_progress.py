from __future__ import annotations

from contextvars import ContextVar
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime

from app.agent.chains import build_liana_progress_chain, invoke_with_temperature_fallback
from app.agent.context import AgentRunContext, default_agent_run_context
from app.agent.liana_progress import (
    build_liana_progress_state_update,
    build_liana_sync_request,
)
from app.agent.state import AgentState

_RUNTIME_SETTINGS: ContextVar[Any] = ContextVar("liana_progress_runtime_settings", default=None)


def _build_chain(*, use_custom_temperature: bool = True):
    return build_liana_progress_chain(
        _RUNTIME_SETTINGS.get(),
        use_custom_temperature=use_custom_temperature,
    )


def _interpret_message(message: str, config: RunnableConfig, settings: Any):
    token = _RUNTIME_SETTINGS.set(settings)
    try:
        return invoke_with_temperature_fallback(
            _build_chain,
            {"latest_user_message": message},
            config=config,
        )
    finally:
        _RUNTIME_SETTINGS.reset(token)


def interpret_liana_progress(
    state: AgentState,
    config: RunnableConfig = None,
    runtime: Runtime[AgentRunContext] = None,
) -> dict[str, Any]:
    context = runtime.context if runtime and runtime.context else default_agent_run_context()
    return build_liana_progress_state_update(
        state,
        context,
        lambda message: _interpret_message(message, config, context.settings),
    )


def plan_liana_pipefacil_sync(
    state: AgentState,
    runtime: Runtime[AgentRunContext] = None,
) -> dict[str, Any]:
    request = build_liana_sync_request(state)
    context = runtime.context if runtime and runtime.context else default_agent_run_context()
    if request is not None and context.pipefacil_sync_enqueue is not None:
        request = context.pipefacil_sync_enqueue(request)
    return {"pending_pipefacil_sync": request, "pipefacil_sync_result": None}


def sync_liana_pipefacil_deal(
    state: AgentState,
    runtime: Runtime[AgentRunContext] = None,
) -> dict[str, Any]:
    request = state.get("pending_pipefacil_sync")
    context = runtime.context if runtime and runtime.context else default_agent_run_context()
    handler = context.pipefacil_sync_handler
    if request is None or handler is None:
        return {"pipefacil_sync_result": {"status": "pending", "error_code": "handler_unavailable"}}
    result = handler(request)
    if result.get("status") in {"succeeded", "blocked"}:
        return {"pending_pipefacil_sync": None, "pipefacil_sync_result": result}
    return {"pipefacil_sync_result": result}


__all__ = [
    "interpret_liana_progress",
    "plan_liana_pipefacil_sync",
    "sync_liana_pipefacil_deal",
]
