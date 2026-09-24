from __future__ import annotations

from typing import Any

from langchain_openai import ChatOpenAI

from app.core.config import RuntimeSettings, get_bootstrap_settings


def model_supports_custom_temperature(model_name: str) -> bool:
    normalized_name = model_name.strip().lower()
    return not normalized_name.startswith("gpt-5")


def model_uses_gpt56_reasoning(model_name: str) -> bool:
    return model_name.strip().lower().startswith("gpt-5.6")


def effective_reasoning_effort(
    model_name: str,
    configured_effort: str | None,
) -> str | None:
    """Use an explicit effort for GPT-5.6 so the API default is never implicit."""

    if not model_uses_gpt56_reasoning(model_name):
        # The template default is intentionally GPT-5.6-specific. Do not send
        # its effort value to legacy GPT-5 or non-reasoning model families.
        return None
    return configured_effort or "none"


def structured_output_method(model_name: str, reasoning_effort: str | None) -> str:
    """Use native JSON Schema when GPT-5.6 reasoning is enabled."""

    if model_uses_gpt56_reasoning(model_name) and reasoning_effort not in (None, "none"):
        return "json_schema"
    return "function_calling"


def get_chat_model(
    settings: RuntimeSettings | None = None,
    *,
    temperature: float | None,
) -> ChatOpenAI:
    settings = settings or RuntimeSettings()
    reasoning_effort = effective_reasoning_effort(
        settings.openai_model,
        settings.openai_reasoning_effort,
    )
    model_kwargs: dict[str, Any] = {"model": settings.openai_model}
    api_key = get_bootstrap_settings().openai_api_key
    if api_key:
        model_kwargs["api_key"] = api_key
    if model_uses_gpt56_reasoning(settings.openai_model):
        # GPT-5.6 accepts the model's default sampling value, exposed as 1.
        model_kwargs["temperature"] = 1
    elif temperature is not None and model_supports_custom_temperature(settings.openai_model):
        model_kwargs["temperature"] = temperature
    if reasoning_effort is not None:
        model_kwargs["reasoning_effort"] = reasoning_effort
    return ChatOpenAI(**model_kwargs)
