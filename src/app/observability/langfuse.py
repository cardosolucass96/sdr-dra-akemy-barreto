from __future__ import annotations

import re
from collections.abc import Iterator, Sequence
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
from functools import lru_cache, partial
from typing import TYPE_CHECKING, Any, Literal

from langchain_core.prompts import ChatPromptTemplate

from app.core.config import Settings, get_settings

if TYPE_CHECKING:
    from langfuse import Langfuse
    from langfuse.langchain import CallbackHandler


EMAIL_PATTERN = re.compile(r"\b[\w.+-]+@[\w.-]+\.\w+\b")
PHONE_PATTERN = re.compile(r"(?<!\w)\+?\d[\d(). -]{8,}\d(?!\w)")
BEARER_TOKEN_PATTERN = re.compile(r"\bBearer\s+[A-Za-z0-9._-]+\b", re.IGNORECASE)
OPENAI_KEY_PATTERN = re.compile(r"\bsk-[A-Za-z0-9_-]+\b")
LANGFUSE_KEY_PATTERN = re.compile(r"\b(?:pk|sk)-lf-[A-Za-z0-9-]+\b")
LANGFUSE_PROMPT_VARIABLE_PATTERN = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}")
BASE64_JSON_FIELD_PATTERN = re.compile(
    r"""(["'](?:base64|file_data)["']\s*:\s*["'])([^"']+)(["'])"""
)
DATA_URL_BASE64_PATTERN = re.compile(
    r"data:([a-zA-Z0-9.+-]+/[a-zA-Z0-9.+-]+);base64,[A-Za-z0-9+/=_-]+"
)
LANGFUSE_USER_ID_ATTRIBUTE = "user.id"
_ACTIVE_SETTINGS: ContextVar[Settings | None] = ContextVar("langfuse_settings", default=None)

PromptType = Literal["chat", "text"]
ChatPromptMessage = dict[str, str]
LANGCHAIN_ROLE_ALIASES = {
    "user": "human",
    "assistant": "ai",
}


class LocalLangfuseChatPrompt:
    """Small fallback object with the subset of Langfuse prompt behavior we need."""

    def __init__(
        self,
        *,
        name: str,
        prompt: list[ChatPromptMessage],
        labels: list[str] | None = None,
        version: int = 0,
    ) -> None:
        self.name = name
        self.prompt = prompt
        self.labels = labels or []
        self.version = version
        self.is_fallback = True

    def get_langchain_prompt(self, **kwargs: Any) -> list[Any]:
        from langchain_core.prompts.chat import MessagesPlaceholder

        compiled_messages = self.compile(**kwargs)
        langchain_messages: list[Any] = []

        for message in compiled_messages:
            if isinstance(message, dict) and message.get("type") == "placeholder":
                langchain_messages.append(MessagesPlaceholder(variable_name=message["name"]))
                continue

            role = LANGCHAIN_ROLE_ALIASES.get(message["role"], message["role"])
            content = _convert_langfuse_variables_to_langchain(message["content"])
            langchain_messages.append((role, content))

        return langchain_messages

    def compile(self, **kwargs: Any) -> list[ChatPromptMessage]:
        compiled_messages: list[ChatPromptMessage] = []

        for message in self.prompt:
            if message.get("type") == "placeholder":
                placeholder_name = message["name"]
                placeholder_value = kwargs.get(placeholder_name)
                if isinstance(placeholder_value, list):
                    for placeholder_message in placeholder_value:
                        if isinstance(placeholder_message, dict):
                            compiled_messages.append(
                                {
                                    "role": str(placeholder_message.get("role", "user")),
                                    "content": str(placeholder_message.get("content", "")),
                                }
                            )
                            continue

                        role = getattr(placeholder_message, "type", "user")
                        content = getattr(placeholder_message, "content", "")
                        compiled_messages.append(
                            {
                                "role": LANGCHAIN_ROLE_ALIASES.get(role, role),
                                "content": content if isinstance(content, str) else str(content),
                            }
                        )
                    continue

                compiled_messages.append(
                    {
                        "type": "placeholder",
                        "name": placeholder_name,
                    }
                )
                continue

            content = message["content"]
            for key, value in kwargs.items():
                content = re.sub(
                    rf"\{{\{{\s*{re.escape(key)}\s*\}}\}}",
                    str(value),
                    content,
                )
            compiled_messages.append({"role": message["role"], "content": content})

        return compiled_messages


def _normalize_environment(value: str | None, fallback: str) -> str:
    return (value or fallback).strip().lower().replace(" ", "-")


def _convert_langfuse_variables_to_langchain(value: str) -> str:
    return LANGFUSE_PROMPT_VARIABLE_PATTERN.sub(r"{\1}", value)


def is_langfuse_enabled(settings: Settings | None = None) -> bool:
    current_settings = settings or _ACTIVE_SETTINGS.get() or get_settings()
    return bool(
        current_settings.langfuse_enabled
        and current_settings.langfuse_public_key
        and current_settings.langfuse_secret_key
        and current_settings.langfuse_base_url
    )


def _mask_phone_numbers(value: str) -> str:
    def replace(match: re.Match[str]) -> str:
        candidate = match.group(0)
        digits = re.sub(r"\D", "", candidate)
        if 10 <= len(digits) <= 15:
            return "[PHONE_REDACTED]"
        return candidate

    return PHONE_PATTERN.sub(replace, value)


def _mask_string(value: str) -> str:
    masked_value = EMAIL_PATTERN.sub("[EMAIL_REDACTED]", value)
    masked_value = _mask_phone_numbers(masked_value)
    masked_value = BEARER_TOKEN_PATTERN.sub("Bearer [TOKEN_REDACTED]", masked_value)
    masked_value = OPENAI_KEY_PATTERN.sub("[OPENAI_KEY_REDACTED]", masked_value)
    masked_value = LANGFUSE_KEY_PATTERN.sub("[LANGFUSE_KEY_REDACTED]", masked_value)
    masked_value = BASE64_JSON_FIELD_PATTERN.sub(r"\1[BASE64_REDACTED]\3", masked_value)
    masked_value = DATA_URL_BASE64_PATTERN.sub(
        r"data:\1;base64,[BASE64_REDACTED]",
        masked_value,
    )
    return masked_value


def _mask_otel_spans(
    *,
    params: Any,
    preserve_user_id: bool = False,
) -> Any | None:
    from langfuse.types import MaskOtelSpansResult, OtelSpanPatch

    patches: dict[str, Any] = {}

    for identifier, span in params.spans.items():
        replacements: dict[str, Any] = {}

        for key, value in span.attributes.items():
            if preserve_user_id and key == LANGFUSE_USER_ID_ATTRIBUTE:
                continue

            if isinstance(value, str):
                masked_value = _mask_string(value)
                if masked_value != value:
                    replacements[key] = masked_value

        if replacements:
            patches[identifier] = OtelSpanPatch(set_attributes=replacements)

    if not patches:
        return None

    return MaskOtelSpansResult(span_patches=patches)


@lru_cache
def _get_langfuse_client(
    public_key: str | None,
    secret_key: str | None,
    base_url: str | None,
    environment: str,
    debug: bool,
    preserve_user_id: bool,
) -> Langfuse | None:
    if not (public_key and secret_key and base_url):
        return None

    from langfuse import Langfuse

    return Langfuse(
        public_key=public_key,
        secret_key=secret_key,
        base_url=base_url,
        environment=environment,
        debug=debug,
        mask_otel_spans=partial(
            _mask_otel_spans,
            preserve_user_id=preserve_user_id,
        ),
    )


def get_langfuse_client() -> Langfuse | None:
    """Return a client for the active request snapshot, or local defaults.

    The active settings live in a ``ContextVar`` rather than graph state.  That
    keeps provider keys out of checkpoints while letting a saved panel change
    take effect on the next execution.
    """

    settings = _ACTIVE_SETTINGS.get() or get_settings()
    if not is_langfuse_enabled(settings):
        return None
    return _get_langfuse_client(
        settings.langfuse_public_key,
        settings.langfuse_secret_key,
        settings.langfuse_base_url,
        _normalize_environment(settings.langfuse_tracing_environment, settings.app_env),
        settings.langfuse_debug,
        settings.langfuse_pipefacil_user_id_mode == "contact_name_phone",
    )


def get_langchain_handler() -> CallbackHandler | None:
    client = get_langfuse_client()
    if client is None:
        return None

    from langfuse.langchain import CallbackHandler

    return CallbackHandler()


def reset_langfuse_clients() -> None:
    _get_langfuse_client.cache_clear()


def get_langchain_callbacks() -> list[Any]:
    handler = get_langchain_handler()
    return [handler] if handler is not None else []


@contextmanager
def using_langfuse_settings(settings: Settings | None) -> Iterator[None]:
    """Make one immutable execution snapshot available to observability helpers."""

    if settings is None:
        yield
        return

    token = _ACTIVE_SETTINGS.set(settings)
    try:
        yield
    finally:
        _ACTIVE_SETTINGS.reset(token)


def resolve_langfuse_prompt_label(settings: Settings | None = None) -> str:
    current_settings = settings or _ACTIVE_SETTINGS.get() or get_settings()
    override = (current_settings.langfuse_prompt_label or "").strip()
    if override:
        return override

    return "production"


def get_langfuse_prompt(
    name: str,
    *,
    prompt_type: PromptType = "text",
    label: str | None = None,
    fallback: list[ChatPromptMessage] | str | None = None,
    cache_ttl_seconds: int | None = 60,
) -> Any:
    resolved_label = label or resolve_langfuse_prompt_label()
    client = get_langfuse_client()

    if client is None:
        if prompt_type == "chat" and isinstance(fallback, list):
            return LocalLangfuseChatPrompt(
                name=name,
                prompt=fallback,
                labels=[resolved_label],
            )

        if prompt_type == "text" and isinstance(fallback, str):
            return fallback

        raise RuntimeError(
            f"Langfuse prompt '{name}' requires either a configured client or a local fallback."
        )

    return client.get_prompt(
        name,
        type=prompt_type,
        label=resolved_label,
        fallback=fallback,
        cache_ttl_seconds=cache_ttl_seconds,
    )


def build_langchain_chat_prompt(
    name: str,
    *,
    fallback_messages: list[ChatPromptMessage],
    label: str | None = None,
) -> tuple[Any, ChatPromptTemplate]:
    prompt = get_langfuse_prompt(
        name,
        prompt_type="chat",
        label=label,
        fallback=fallback_messages,
    )
    template = ChatPromptTemplate.from_messages(prompt.get_langchain_prompt())
    template.metadata = {"langfuse_prompt": prompt}
    return prompt, template


def warm_up_langfuse(settings: Settings | None = None) -> None:
    with using_langfuse_settings(settings):
        get_langfuse_client()


def flush_langfuse(settings: Settings | None = None) -> None:
    with using_langfuse_settings(settings):
        client = get_langfuse_client()
        if client is not None:
            client.flush()


@contextmanager
def _propagate_context(
    *,
    session_id: str | None = None,
    user_id: str | None = None,
    tags: Sequence[str] | None = None,
) -> Iterator[None]:
    attributes: dict[str, Any] = {}

    if session_id:
        attributes["session_id"] = session_id
    if user_id:
        attributes["user_id"] = user_id
    if tags:
        attributes["tags"] = list(tags)

    if not attributes:
        with nullcontext():
            yield
        return

    from langfuse import propagate_attributes

    with propagate_attributes(**attributes):
        yield


@contextmanager
def observe_span(
    *,
    name: str,
    input: Any | None = None,
    metadata: dict[str, Any] | None = None,
    as_type: str = "span",
) -> Iterator[Any | None]:
    client = get_langfuse_client()
    if client is None:
        yield None
        return

    with client.start_as_current_observation(
        as_type=as_type,
        name=name,
        input=input,
    ) as observation:
        if metadata:
            observation.update(metadata=metadata)
        yield observation


@contextmanager
def observe_agent_run(
    *,
    name: str,
    input: Any,
    session_id: str | None = None,
    user_id: str | None = None,
    tags: Sequence[str] | None = None,
    metadata: dict[str, Any] | None = None,
    as_type: str = "agent",
) -> Iterator[Any | None]:
    with _propagate_context(session_id=session_id, user_id=user_id, tags=tags):
        with observe_span(
            name=name,
            input=input,
            metadata=metadata,
            as_type=as_type,
        ) as observation:
            yield observation
