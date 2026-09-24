from __future__ import annotations

from langchain_core.runnables import RunnableLambda

from app.agent.chains.llm import (
    effective_reasoning_effort,
    get_chat_model,
    structured_output_method,
)
from app.agent.chains.schemas import IntentClassification, OpenAIIntentClassification
from app.agent.prompts import get_classifier_prompt_template
from app.core.config import RuntimeSettings


def build_classifier_chain(
    settings: RuntimeSettings | None = None,
    *,
    use_custom_temperature: bool = True,
):
    settings = settings or RuntimeSettings()
    _, prompt_template = get_classifier_prompt_template(label=settings.langfuse_prompt_label)
    model = get_chat_model(settings=settings, temperature=0 if use_custom_temperature else None)
    method = structured_output_method(
        settings.openai_model,
        effective_reasoning_effort(settings.openai_model, settings.openai_reasoning_effort),
    )
    schema = IntentClassification
    if method == "json_schema":
        schema = OpenAIIntentClassification

    structured = model.with_structured_output(schema, method=method)
    if method == "json_schema":
        structured = structured | RunnableLambda(
            lambda value: IntentClassification.model_validate(value.model_dump())
        )
    return prompt_template | structured
