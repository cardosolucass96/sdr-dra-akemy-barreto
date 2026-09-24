from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_sync_module():
    path = Path("scripts/sync_langfuse_golden_dataset.py")
    spec = importlib.util.spec_from_file_location("sync_langfuse_golden_dataset", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_build_langfuse_dataset_item_payload_maps_case_contract() -> None:
    sync = _load_sync_module()
    case = {
        "id": "pf-case-001",
        "version": 1,
        "status": "candidate",
        "source": {
            "kind": "langfuse_trace",
            "trace_id": "trace-123",
            "observation_id": None,
        },
        "scenario": {"title": "Titulo", "description": "Descricao", "tags": ["tag"]},
        "cut": {"kind": "mid_conversation", "turn_index": 3, "reason": "Corte"},
        "input": {
            "messages_so_far": [{"role": "user", "content": "Oi"}],
            "state_snapshot": {"status": "qualifying"},
        },
        "expected": {
            "next_behavior": "Continuar qualificacao",
            "ideal_response": "Qual a cidade de entrega?",
            "success_criteria": ["pede cidade"],
            "must_not": ["inventar preco"],
        },
        "evaluation": {
            "type": "rubric",
            "minimum_score": 0.8,
            "rubric": [{"name": "criterio", "weight": 1, "description": "desc"}],
        },
        "metadata": {"created_at": "2026-07-23", "owner": "pipefacil", "notes": ""},
    }

    payload = sync.build_langfuse_dataset_item_payload(
        case,
        dataset_name="sdr-pipefacil-golden-dataset",
    )

    assert payload["id"] == "sdr-pipefacil-golden-dataset__pf-case-001"
    assert payload["dataset_name"] == "sdr-pipefacil-golden-dataset"
    assert payload["input"] == {
        "messages_so_far": [{"role": "user", "content": "Oi"}],
        "state_snapshot": {"status": "qualifying"},
    }
    assert payload["expected_output"]["ideal_response"] == "Qual a cidade de entrega?"
    assert payload["metadata"]["case_status"] == "candidate"
    assert payload["source_trace_id"] == "trace-123"


def test_build_langfuse_dataset_metadata_includes_evaluation_standard() -> None:
    sync = _load_sync_module()
    manifest = {
        "version": 1,
        "case_statuses": ["candidate", "approved", "deprecated"],
        "pii_policy": {"allow_real_customer_data": False},
        "langfuse_evaluation_standard": {
            "score_configs": [
                {"name": "answer_correct", "data_type": "BOOLEAN"},
                {"name": "failure_note", "data_type": "TEXT"},
            ],
            "correction": {"score_name": "output", "data_type": "CORRECTION"},
        },
    }

    metadata = sync.build_langfuse_dataset_metadata(
        manifest=manifest,
        dataset_path=Path("datasets/golden/examples.jsonl"),
    )

    assert metadata["langfuse_evaluation_standard"] == manifest["langfuse_evaluation_standard"]


def test_filter_cases_skips_deprecated_by_default() -> None:
    sync = _load_sync_module()
    cases = [
        {"id": "candidate", "status": "candidate"},
        {"id": "approved", "status": "approved"},
        {"id": "deprecated", "status": "deprecated"},
    ]

    selected = sync.filter_cases(cases, statuses=set(sync.DEFAULT_SYNC_STATUSES))

    assert [case["id"] for case in selected] == ["candidate", "approved"]


def test_build_langfuse_rest_dataset_item_payload_uses_public_api_shape() -> None:
    sync = _load_sync_module()

    payload = sync.build_langfuse_rest_dataset_item_payload(
        {
            "id": "dataset__case",
            "dataset_name": "dataset",
            "input": {"messages_so_far": []},
            "expected_output": {"ideal_response": "ok"},
            "metadata": {"case_id": "case"},
            "source_trace_id": "trace-123",
        }
    )

    assert payload == {
        "datasetName": "dataset",
        "input": {"messages_so_far": []},
        "expectedOutput": {"ideal_response": "ok"},
        "metadata": {"case_id": "case"},
        "sourceTraceId": "trace-123",
        "sourceObservationId": None,
        "id": "dataset__case",
    }


def test_sync_dry_run_validates_and_reports_cases(capsys) -> None:
    sync = _load_sync_module()

    result = sync.main(["--dry-run"])

    captured = capsys.readouterr()
    assert result == 0
    assert "DRY RUN: would sync 10 case(s)" in captured.out
    assert "sdr-pipefacil-golden-dataset" in captured.out
    assert "sdr-opt-out-encerramento-001" in captured.out
