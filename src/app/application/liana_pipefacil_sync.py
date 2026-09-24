from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from app.core.config import Settings
from app.integrations.pipefacil import (
    PipefacilDealLookupError,
    PipefacilDealUpdateError,
    fetch_deal_by_seq,
    update_deal,
)
from app.integrations.pipefacil.liana_mapping import (
    LIANA_AUTOMATABLE_STAGES,
    LIANA_PIPELINE_ID,
    liana_qualification_fields,
    liana_stage_id,
    liana_stage_order,
    resolve_liana_stage_key,
)
from app.integrations.pipefacil.outbox import PipefacilSyncOutboxStore

LOGGER = logging.getLogger(__name__)
QUALIFICATION_TARGETS = frozenset({"qualification", "appointment"})
KNOWN_STAGE_KEYS = frozenset(
    {
        "entry",
        "qualification",
        "appointment",
        "attended_no_sale",
        "unresponsive",
        "courtesy",
        "won",
        "lost",
    }
)


class LianaPipefacilSyncService:
    def __init__(
        self,
        store: PipefacilSyncOutboxStore,
        *,
        settings_provider: Callable[[], Settings],
    ) -> None:
        self._store = store
        self._settings_provider = settings_provider

    def enqueue_and_process(self, request: dict[str, Any]) -> dict[str, Any]:
        operation = self.enqueue_request(request)
        return self.process_operation(operation)

    def enqueue_request(self, request: dict[str, Any]) -> dict[str, Any]:
        operation = _build_pipefacil_operation(request)
        self._store.enqueue(operation)
        return operation

    def process_operation(self, operation: dict[str, Any]) -> dict[str, Any]:
        claimed = self._store.claim(operation["operation_id"])
        if claimed is not None:
            return self._process_claimed(claimed)
        existing = self._store.get(operation["operation_id"])
        return _existing_operation_result(existing)

    def process_next(self) -> bool:
        operation = self._store.claim()
        if operation is None:
            return False
        self._process_claimed(operation)
        return True

    def _process_claimed(self, operation: dict[str, Any] | None) -> dict[str, Any]:
        if operation is None:
            return {"status": "pending", "error_code": "operation_already_claimed"}
        try:
            return self._apply_operation(operation)
        except (PipefacilDealLookupError, PipefacilDealUpdateError) as exc:
            attempt = int(operation.get("attempt_count", 0)) + 1
            self._store.mark_failed(
                operation["operation_id"],
                {
                    "error_code": exc.error_code,
                    "status_code": exc.status_code,
                    "request_id": exc.request_id,
                },
                retry_after_seconds=min(3600, 5 * (2 ** min(attempt, 9))),
            )
            return {"status": "pending", "error_code": exc.error_code}
        except Exception:
            LOGGER.exception(
                "pipefacil.liana_sync.unexpected_failure",
                extra={
                    "deal_seq": operation.get("deal_seq"),
                    "operation_id": operation.get("operation_id"),
                },
            )
            self._store.mark_failed(
                operation["operation_id"],
                {"error_code": "pipefacil_sync_unexpected_error"},
                retry_after_seconds=30,
            )
            return {"status": "pending", "error_code": "pipefacil_sync_unexpected_error"}

    def _apply_operation(self, operation: dict[str, Any]) -> dict[str, Any]:
        deal = fetch_deal_by_seq(seq=operation["deal_seq"], settings=self._settings_provider())
        current_stage_id = _deal_stage_id(deal)
        current_stage = resolve_liana_stage_key(current_stage_id)
        pipeline_id = _deal_pipeline_id(deal)
        if pipeline_id and pipeline_id != LIANA_PIPELINE_ID:
            return self._block(operation, "deal_pipeline_mismatch")
        if current_stage not in KNOWN_STAGE_KEYS:
            return self._block(operation, "deal_stage_unrecognized")

        target = operation["target_stage_key"]
        current_order = liana_stage_order(current_stage)
        target_order = liana_stage_order(target)
        if current_order is None or target_order is None:
            return self._block(operation, "deal_stage_unrecognized")
        if current_order > target_order:
            return self._block(operation, "deal_already_advanced")

        stage_id = liana_stage_id(target) if current_order < target_order else None
        result = update_deal(
            seq=operation["deal_seq"],
            custom_fields=operation["custom_fields"],
            stage_id=stage_id,
            settings=self._settings_provider(),
        )
        receipt = {
            "status_code": result.status_code,
            "request_id": result.request_id,
            "target_stage_key": target,
            "custom_field_slugs": sorted(operation["custom_fields"]),
        }
        self._store.mark_succeeded(operation["operation_id"], receipt)
        return {"status": "succeeded", "receipt": receipt}

    def _block(self, operation: dict[str, Any], error_code: str) -> dict[str, Any]:
        self._store.mark_blocked(operation["operation_id"], {"error_code": error_code})
        return {"status": "blocked", "error_code": error_code}


def _build_pipefacil_operation(request: dict[str, Any]) -> dict[str, Any]:
    target = str(request.get("target_stage_key") or "")
    deal_seq = request.get("deal_seq")
    operation_id = str(request.get("operation_id") or "")
    if target not in QUALIFICATION_TARGETS or target not in LIANA_AUTOMATABLE_STAGES:
        raise ValueError("Liana can sync only qualification or appointment stages.")
    if not isinstance(deal_seq, int) or deal_seq <= 0 or not operation_id:
        raise ValueError("A stable operation id and positive deal seq are required.")
    return {
        "operation_id": operation_id,
        "deal_seq": deal_seq,
        "pipeline_id": LIANA_PIPELINE_ID,
        "target_stage_key": target,
        "stage_id": liana_stage_id(target),
        "custom_fields": liana_qualification_fields(target),
    }


def _existing_operation_result(existing: dict[str, Any] | None) -> dict[str, Any]:
    if existing is None:
        return {"status": "pending", "error_code": "operation_status_unavailable"}
    result = {"status": existing["status"]}
    if existing.get("receipt") is not None:
        result["receipt"] = existing["receipt"]
    if existing.get("last_error") is not None:
        result["error"] = existing["last_error"]
    return result


def _deal_stage_id(deal: dict[str, Any]) -> str | None:
    stage = deal.get("stage")
    if isinstance(stage, dict):
        return _text(stage.get("id"))
    return _text(deal.get("stageId") or deal.get("stage_id"))


def _deal_pipeline_id(deal: dict[str, Any]) -> str | None:
    pipeline = deal.get("pipeline")
    if isinstance(pipeline, dict):
        return _text(pipeline.get("id"))
    return _text(deal.get("pipelineId") or deal.get("pipeline_id"))


def _text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


__all__ = ["LianaPipefacilSyncService"]
