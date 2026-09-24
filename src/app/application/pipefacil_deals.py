from __future__ import annotations

import logging

from app.core.config import Settings
from app.integrations.pipefacil import (
    PipefacilDealUpdateError,
    PipefacilDealUpdateResult,
    update_deal_stage,
)

LOGGER = logging.getLogger(__name__)


def move_pipefacil_deal_stage(
    *,
    deal_seq: int | None,
    target_stage_id: str | None,
    settings: Settings,
) -> PipefacilDealUpdateResult:
    log_context = {
        "deal_seq": deal_seq,
        "pipefacil_stage_id": (target_stage_id or "").strip() or None,
    }
    LOGGER.info(
        "pipefacil.deal.stage_update_started",
        extra={
            **log_context,
            "pipeline_step": "pipefacil.deal.stage_update_started",
        },
    )

    try:
        result = update_deal_stage(
            seq=deal_seq,
            stage_id=target_stage_id,
            settings=settings,
        )
    except PipefacilDealUpdateError as exc:
        LOGGER.warning(
            "pipefacil.deal.stage_update_failed",
            extra={
                **log_context,
                "pipeline_step": "pipefacil.deal.stage_update_failed",
                "error_code": exc.error_code,
                "status_code": exc.status_code,
                "request_id": exc.request_id,
            },
        )
        raise

    LOGGER.info(
        "pipefacil.deal.stage_update_completed",
        extra={
            **log_context,
            "pipeline_step": "pipefacil.deal.stage_update_completed",
            "status_code": result.status_code,
            "request_id": result.request_id,
        },
    )
    return result
