from __future__ import annotations

from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnableLambda

from app.agent.chains.llm import (
    effective_reasoning_effort,
    get_chat_model,
    structured_output_method,
)
from app.agent.chains.schemas import LianaLeadProgress, OpenAILianaLeadProgress
from app.core.config import RuntimeSettings

LIANA_PROGRESS_SYSTEM_PROMPT = """Você interpreta sinais comerciais explícitos para a SDR Liana.
Considere somente o texto da mensagem atual. Não infira intenção, objetivo, interesse ou período.
Marque explicit_interest somente quando a pessoa demonstrar interesse claro em avançar com
avaliação ou tratamento. Extraia stated_objective apenas se ela disser o que quer avaliar ou
resolver. Marque appointment_requested quando pedir horário/agendamento ou indicar um período
em que gostaria de ser atendida; extraia preferred_period apenas quando estiver escrito.
Perguntas informativas, sintomas ou queixas sem pedido de avanço não contam como interesse nem
como pedido de agendamento. Use null para texto ausente. Confidence deve refletir a clareza dos
sinais identificados, de 0 a 1."""


def build_liana_progress_chain(
    settings: RuntimeSettings | None = None,
    *,
    use_custom_temperature: bool = True,
):
    resolved_settings = settings or RuntimeSettings()
    model = get_chat_model(
        settings=resolved_settings,
        temperature=0 if use_custom_temperature else None,
    )
    method = structured_output_method(
        resolved_settings.openai_model,
        effective_reasoning_effort(
            resolved_settings.openai_model,
            resolved_settings.openai_reasoning_effort,
        ),
    )
    schema = LianaLeadProgress
    if method == "json_schema":
        schema = OpenAILianaLeadProgress
    structured = model.with_structured_output(schema, method=method)
    if method == "json_schema":
        structured = structured | RunnableLambda(
            lambda value: LianaLeadProgress.model_validate(value.model_dump())
        )
    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", LIANA_PROGRESS_SYSTEM_PROMPT),
            ("human", "Mensagem recebida: {latest_user_message}"),
        ]
    )
    return prompt | structured


__all__ = ["LIANA_PROGRESS_SYSTEM_PROMPT", "build_liana_progress_chain"]
