from __future__ import annotations

import logging
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime

from app.agent.context import AgentRunContext, execution_settings_from_runtime
from app.agent.specialist_delegation import build_specialist_request, build_specialist_update
from app.agent.specialists import OpenAISpecialistRunner, SpecialistRequest, SpecialistResult
from app.agent.state import AgentState

LOGGER = logging.getLogger(__name__)


def _thread_id_from_config(config: RunnableConfig = None) -> str | None:
    configurable = (config or {}).get("configurable", {})
    thread_id = configurable.get("thread_id")
    return str(thread_id) if thread_id else None


def _build_specialist_request(state: AgentState) -> SpecialistRequest:
    return build_specialist_request(state)


def _run_specialist(
    *,
    specialist_name: str,
    request: SpecialistRequest,
    settings: Any,
) -> SpecialistResult:
    return OpenAISpecialistRunner(settings=settings).run(
        specialist_name=specialist_name,
        request=request,
    )


def delegate_specialist(
    state: AgentState,
    config: RunnableConfig = None,
    runtime: Runtime[AgentRunContext] = None,
) -> dict[str, Any]:
    return build_specialist_update(
        state,
        thread_id=_thread_id_from_config(config),
        settings=execution_settings_from_runtime(runtime),
        request_builder=_build_specialist_request,
        specialist_runner=_run_specialist,
        logger=LOGGER,
    )
