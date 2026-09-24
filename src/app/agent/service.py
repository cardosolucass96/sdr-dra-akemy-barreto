from __future__ import annotations

from collections.abc import Sequence
from inspect import Parameter, signature
from typing import Any
from uuid import uuid4

from langchain_core.runnables import RunnableConfig
from langgraph.types import Overwrite

from app.agent.context import AgentRunContext
from app.agent.graph import graph as default_graph
from app.agent.messages import (
    has_sensitive_multimodal_content,
    message_content,
    serialize_messages,
)
from app.agent.state import AgentState
from app.core.config import (
    RuntimeSettings,
    Settings,
    build_execution_settings,
    get_bootstrap_settings,
    runtime_settings_from_execution,
)
from app.observability import get_langchain_callbacks, observe_agent_run, using_langfuse_settings


def _resolve_graph(graph: Any | None) -> Any:
    return graph if graph is not None else default_graph


def _invoke_graph(
    graph_runner: Any,
    graph_input: AgentState,
    *,
    config: RunnableConfig | None,
    context: AgentRunContext,
) -> AgentState:
    parameters = signature(graph_runner.invoke).parameters.values()
    accepts_context = any(
        parameter.name == "context" or parameter.kind is Parameter.VAR_KEYWORD
        for parameter in parameters
    )
    invoke_kwargs: dict[str, Any] = {"config": config}
    if accepts_context:
        invoke_kwargs["context"] = context
    return graph_runner.invoke(graph_input, **invoke_kwargs)


def _thread_config(
    thread_id: str | None,
    *,
    config: RunnableConfig | None = None,
) -> RunnableConfig:
    runnable_config: RunnableConfig = dict(config or {})
    configurable = dict(runnable_config.get("configurable", {}))
    configurable.setdefault("thread_id", thread_id or f"agent-run-{uuid4().hex}")
    runnable_config["configurable"] = configurable
    return runnable_config


def _configure_langchain_callbacks(
    runnable_config: RunnableConfig,
    *,
    state: AgentState,
) -> RunnableConfig:
    configured = dict(runnable_config)
    if has_sensitive_multimodal_content(state.get("messages", [])):
        configured.pop("callbacks", None)
        return configured

    callbacks = get_langchain_callbacks()
    if not callbacks:
        return configured

    existing_callbacks = configured.get("callbacks")
    if existing_callbacks is None:
        configured["callbacks"] = callbacks
        return configured

    if isinstance(existing_callbacks, list):
        configured["callbacks"] = [*existing_callbacks, *callbacks]
        return configured

    configured["callbacks"] = [existing_callbacks, *callbacks]
    return configured


def _snapshot_is_missing(snapshot: Any) -> bool:
    return (
        not getattr(snapshot, "values", {})
        and getattr(snapshot, "metadata", None) is None
        and getattr(snapshot, "created_at", None) is None
        and getattr(snapshot, "parent_config", None) is None
    )


def serialize_thread_state(snapshot: Any) -> dict[str, Any]:
    values = dict(getattr(snapshot, "values", {}))
    return {
        "thread_id": snapshot.config["configurable"]["thread_id"],
        "latest_user_message": values.get("latest_user_message"),
        "intent": values.get("intent"),
        "intent_reason": values.get("intent_reason"),
        "response_text": values.get("response_text"),
        "status": values.get("status"),
        "messages": serialize_messages(values.get("messages", [])),
    }


def get_thread_state(
    thread_id: str,
    *,
    graph: Any | None = None,
    config: RunnableConfig | None = None,
) -> Any | None:
    runnable_config = _thread_config(thread_id, config=config)
    snapshot = _resolve_graph(graph).get_state(runnable_config)
    return None if _snapshot_is_missing(snapshot) else snapshot


class ThreadStateResetError(RuntimeError):
    """Raised when the configured graph cannot delete a thread checkpoint."""


def reset_thread_state(thread_id: str, *, graph: Any | None = None) -> None:
    """Delete every persisted checkpoint belonging to one conversation thread."""

    checkpointer = getattr(_resolve_graph(graph), "checkpointer", None)
    delete_thread = getattr(checkpointer, "delete_thread", None)
    if not callable(delete_thread):
        raise ThreadStateResetError("The graph has no thread-capable checkpointer.")

    try:
        delete_thread(thread_id)
    except Exception as exc:
        raise ThreadStateResetError("The thread checkpoint could not be deleted.") from exc


def _build_trace_input(state: AgentState) -> Any:
    messages = state.get("messages", [])

    if messages:
        latest_user_message = message_content(messages[-1]).strip()
        if latest_user_message:
            return latest_user_message

    trace_input: dict[str, Any] = {}
    if messages:
        trace_input["messages"] = serialize_messages(messages)

    if "status" in state:
        trace_input["status"] = state["status"]
    if "intent" in state:
        trace_input["intent"] = state["intent"]

    return trace_input


def _build_trace_output(result: AgentState) -> Any:
    response_text = result.get("response_text")
    if isinstance(response_text, str) and response_text.strip():
        return response_text

    trace_output: dict[str, Any] = {}
    for field in ("status", "intent", "latest_user_message"):
        value = result.get(field)
        if value:
            trace_output[field] = value

    return trace_output


def run_agent(
    state: AgentState,
    *,
    session_id: str | None = None,
    user_id: str | None = None,
    tags: Sequence[str] | None = None,
    metadata: dict[str, Any] | None = None,
    config: RunnableConfig | None = None,
    graph: Any | None = None,
    replace_messages: bool = False,
    settings: Settings | None = None,
) -> AgentState:
    execution_settings = settings or build_execution_settings(
        get_bootstrap_settings(), RuntimeSettings()
    )
    with using_langfuse_settings(execution_settings):
        runnable_config = _configure_langchain_callbacks(
            _thread_config(session_id, config=config),
            state=state,
        )
        graph_runner = _resolve_graph(graph)

        app_slug = execution_settings.app_slug
        trace_tags = ["langgraph", app_slug]
        if tags:
            trace_tags.extend(tags)

        trace_metadata = {"graph": app_slug}
        if metadata:
            trace_metadata.update(metadata)

        with observe_agent_run(
            name=f"run-{app_slug}",
            input=_build_trace_input(state),
            session_id=session_id,
            user_id=user_id,
            tags=trace_tags,
            metadata=trace_metadata,
        ) as observation:
            graph_input = dict(state)
            if replace_messages:
                graph_input["messages"] = Overwrite(list(state.get("messages", [])))
            result = _invoke_graph(
                graph_runner,
                graph_input,
                config=runnable_config or None,
                context=AgentRunContext(
                    settings=runtime_settings_from_execution(execution_settings)
                ),
            )
            if observation is not None:
                observation.update(output=_build_trace_output(result))

    return result
