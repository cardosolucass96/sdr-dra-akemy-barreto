from __future__ import annotations

from collections.abc import Callable
from inspect import Parameter, signature
from typing import Any

from openai import BadRequestError


def is_unsupported_temperature_error(error: BadRequestError) -> bool:
    body = getattr(error, "body", None)
    if isinstance(body, dict):
        error_payload = body.get("error", body)
        if isinstance(error_payload, dict):
            param = error_payload.get("param")
            message = str(error_payload.get("message", ""))
            return param == "temperature" and "Unsupported value" in message

    return "temperature" in str(error).lower() and "unsupported value" in str(error).lower()


def build_chain(
    chain_factory: Callable[..., Any],
    *,
    use_custom_temperature: bool,
) -> Any:
    parameters = signature(chain_factory).parameters.values()
    accepts_toggle = any(
        parameter.kind == Parameter.VAR_KEYWORD or parameter.name == "use_custom_temperature"
        for parameter in parameters
    )
    if accepts_toggle:
        return chain_factory(use_custom_temperature=use_custom_temperature)

    return chain_factory()


def invoke_with_temperature_fallback(
    chain_factory: Callable[..., Any],
    payload: dict[str, Any],
    *,
    config=None,
) -> Any:
    try:
        return build_chain(chain_factory, use_custom_temperature=True).invoke(
            payload,
            config=config,
        )
    except BadRequestError as error:
        if not is_unsupported_temperature_error(error):
            raise

    return build_chain(chain_factory, use_custom_temperature=False).invoke(
        payload,
        config=config,
    )
