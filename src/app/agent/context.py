"""Per-invocation dependencies for LangGraph nodes.

Only non-sensitive runtime settings enter this context. They are not part of the graph
state and are therefore never checkpointed with the lead conversation.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from langgraph.runtime import Runtime

from app.core.config import (
    RuntimeSettings,
    Settings,
    build_execution_settings,
    get_bootstrap_settings,
)


@dataclass(frozen=True, slots=True)
class AgentRunContext:
    settings: RuntimeSettings
    pipefacil_deal_seq: int | None = None
    pipefacil_stage_key: str | None = None
    pipefacil_sync_enqueue: Callable[[dict[str, Any]], dict[str, Any]] | None = None
    pipefacil_sync_handler: Callable[[dict[str, Any]], dict[str, Any]] | None = None


def default_agent_run_context() -> AgentRunContext:
    """Fallback for direct LangGraph Studio runs without a FastAPI request."""

    return AgentRunContext(settings=RuntimeSettings())


def execution_settings_from_runtime(
    runtime: Runtime[AgentRunContext] | None,
) -> Settings:
    if runtime is None or runtime.context is None:
        return build_execution_settings(get_bootstrap_settings(), RuntimeSettings())
    context = runtime.context if runtime and runtime.context else default_agent_run_context()
    return build_execution_settings(get_bootstrap_settings(), context.settings)
