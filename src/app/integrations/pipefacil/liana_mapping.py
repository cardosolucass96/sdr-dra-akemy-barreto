"""PipeFácil workspace identifiers and field slugs for Liana's SDR."""

from __future__ import annotations

from typing import Literal, cast

LianaStageKey = Literal[
    "entry",
    "qualification",
    "appointment",
    "attended_no_sale",
    "unresponsive",
    "courtesy",
    "won",
    "lost",
]

LIANA_PIPELINE_ID = "4adb0e22-cefe-4da3-9e06-245efa87ee2b"
LIANA_PIPELINE_SEQ = 5

LIANA_STAGE_IDS: dict[LianaStageKey, str] = {
    "entry": "a981eeb4-7cd6-4cfd-a736-5c08c93d8510",
    "qualification": "f738b9b6-e94e-4075-ae9d-6585b34f1f6b",
    "appointment": "003bf3ef-cb11-4236-9c4e-361b05f8f628",
    "attended_no_sale": "a4eaa712-03ba-4212-8ca1-7cc317aafb9a",
    "unresponsive": "8765a8bd-c319-4217-b620-3e735ebda00c",
    "courtesy": "dc23fb16-c27e-4145-8a78-e073a918ccc2",
    "won": "5ad9b3ad-45c6-4307-8505-943a88914f81",
    "lost": "c6bc20ea-92f1-448f-b1de-ea01fa0b9cd4",
}

LIANA_STAGE_ORDER: dict[LianaStageKey, int] = {
    "entry": 1,
    "qualification": 2,
    "appointment": 3,
    "attended_no_sale": 4,
    "unresponsive": 5,
    "courtesy": 6,
    "won": 7,
    "lost": 8,
}

LIANA_CUSTOM_FIELDS = {
    "attendant": "atendente",
    "specialty": "especialidade_procurada",
    "marketing_qualification": "qualificacao_mkt",
    "sales_qualification": "qualificacao_vendas",
    "payment": "pagamento",
    "appointment_at": "consulta_agendada",
    "sale_value": "venda",
    "lead_source": "fonte_do_lead",
    "loss_reason": "motivo_de_perda",
}

LIANA_ATTENDANT_VALUE = "liana"
LIANA_AUTOMATABLE_STAGES = frozenset({"entry", "qualification", "appointment"})
LIANA_QUALIFICATION_FIELDS: dict[str, dict[str, str]] = {
    "qualification": {LIANA_CUSTOM_FIELDS["marketing_qualification"]: "mql"},
    "appointment": {
        LIANA_CUSTOM_FIELDS["marketing_qualification"]: "mql",
        LIANA_CUSTOM_FIELDS["sales_qualification"]: "sql",
    },
}


def resolve_liana_stage_key(stage_id: str | None) -> LianaStageKey | None:
    normalized_stage_id = (stage_id or "").strip()
    return next(
        (
            key
            for key, configured_id in LIANA_STAGE_IDS.items()
            if configured_id == normalized_stage_id
        ),
        None,
    )


def liana_stage_id(stage_key: str) -> str | None:
    return LIANA_STAGE_IDS.get(cast("LianaStageKey", stage_key))


def liana_stage_order(stage_key: str | None) -> int | None:
    if stage_key is None:
        return None
    return LIANA_STAGE_ORDER.get(cast("LianaStageKey", stage_key))


def liana_qualification_fields(stage_key: str) -> dict[str, str]:
    fields = LIANA_QUALIFICATION_FIELDS.get(stage_key, {})
    return {LIANA_CUSTOM_FIELDS["attendant"]: LIANA_ATTENDANT_VALUE, **fields}


__all__ = [
    "LIANA_ATTENDANT_VALUE",
    "LIANA_AUTOMATABLE_STAGES",
    "LIANA_CUSTOM_FIELDS",
    "LIANA_PIPELINE_ID",
    "LIANA_PIPELINE_SEQ",
    "LIANA_QUALIFICATION_FIELDS",
    "LIANA_STAGE_IDS",
    "LIANA_STAGE_ORDER",
    "LianaStageKey",
    "liana_stage_id",
    "liana_qualification_fields",
    "liana_stage_order",
    "resolve_liana_stage_key",
]
