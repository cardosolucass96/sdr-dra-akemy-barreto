from __future__ import annotations

import pytest

import app.application.pipefacil_deals as pipefacil_deals
from app.core.config import Settings
from app.integrations.pipefacil import PipefacilDealUpdateError, PipefacilDealUpdateResult


class _FakeLogger:
    def __init__(self) -> None:
        self.records: list[tuple[str, str, dict[str, object]]] = []

    def info(self, message: str, *args: object, **kwargs: object) -> None:
        self.records.append(("info", message, dict(kwargs.get("extra") or {})))

    def warning(self, message: str, *args: object, **kwargs: object) -> None:
        self.records.append(("warning", message, dict(kwargs.get("extra") or {})))


def test_move_pipefacil_deal_stage_delegates_and_logs_safe_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    logger = _FakeLogger()
    captured: dict[str, object] = {}
    settings = Settings(_env_file=None, pipefacil_api_key="secret-api-key")
    expected = PipefacilDealUpdateResult(
        status_code=200,
        request_id="req-stage",
        payload={"data": {"seq": 100}},
    )
    monkeypatch.setattr(pipefacil_deals, "LOGGER", logger)
    monkeypatch.setattr(
        pipefacil_deals,
        "update_deal_stage",
        lambda **kwargs: captured.update(kwargs) or expected,
    )

    result = pipefacil_deals.move_pipefacil_deal_stage(
        deal_seq=100,
        target_stage_id="stage-2",
        settings=settings,
    )

    assert result is expected
    assert captured == {
        "seq": 100,
        "stage_id": "stage-2",
        "settings": settings,
    }
    assert [record[1] for record in logger.records] == [
        "pipefacil.deal.stage_update_started",
        "pipefacil.deal.stage_update_completed",
    ]
    assert "secret-api-key" not in repr(logger.records)


def test_move_pipefacil_deal_stage_preserves_stable_error_and_logs_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    logger = _FakeLogger()
    error = PipefacilDealUpdateError(
        "invalid",
        error_code="pipefacil_upstream_error",
        status_code=422,
        request_id="req-invalid",
    )
    monkeypatch.setattr(pipefacil_deals, "LOGGER", logger)
    monkeypatch.setattr(
        pipefacil_deals,
        "update_deal_stage",
        lambda **kwargs: (_ for _ in ()).throw(error),
    )

    with pytest.raises(PipefacilDealUpdateError) as exc_info:
        pipefacil_deals.move_pipefacil_deal_stage(
            deal_seq=100,
            target_stage_id="stage-2",
            settings=Settings(_env_file=None, pipefacil_api_key="secret-api-key"),
        )

    assert exc_info.value is error
    failed_log = logger.records[-1][2]
    assert failed_log["error_code"] == "pipefacil_upstream_error"
    assert failed_log["status_code"] == 422
    assert failed_log["request_id"] == "req-invalid"
    assert "secret-api-key" not in repr(logger.records)
