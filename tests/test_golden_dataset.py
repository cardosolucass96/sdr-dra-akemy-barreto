from __future__ import annotations

import importlib.util
import json
from pathlib import Path


def _load_validator_module():
    path = Path("scripts/validate_golden_dataset.py")
    spec = importlib.util.spec_from_file_location("validate_golden_dataset", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_golden_dataset_examples_are_valid() -> None:
    validator = _load_validator_module()

    errors = validator.validate_file(Path("datasets/golden/examples.jsonl"))

    assert errors == []


def test_golden_dataset_schema_documents_required_contract() -> None:
    schema = json.loads(Path("datasets/golden/schema.json").read_text())

    assert schema["required"] == [
        "id",
        "version",
        "status",
        "source",
        "scenario",
        "cut",
        "input",
        "expected",
        "evaluation",
        "metadata",
    ]
    assert set(schema["properties"]["status"]["enum"]) == {"candidate", "approved", "deprecated"}
    assert "messages_so_far" in schema["properties"]["input"]["properties"]


def test_golden_dataset_covers_consultative_conversation_scenarios() -> None:
    cases = [
        json.loads(line)
        for line in Path("datasets/golden/examples.jsonl").read_text().splitlines()
        if line.strip()
    ]
    cases_by_id = {case["id"]: case for case in cases}
    expected_case_ids = {
        "sdr-resposta-parcial-pergunta-001",
        "sdr-pergunta-relacionada-retomada-001",
        "sdr-correcao-fato-001",
        "sdr-fora-escopo-retomada-001",
        "sdr-necessidade-vaga-descoberta-001",
        "sdr-beneficio-contextual-001",
        "sdr-conversa-nao-morrer-001",
        "sdr-opt-out-encerramento-001",
    }

    assert expected_case_ids <= cases_by_id.keys()
    assert all(cases_by_id[case_id]["status"] == "candidate" for case_id in expected_case_ids)
    assert all(
        cases_by_id[case_id]["evaluation"]["type"] == "rubric" for case_id in expected_case_ids
    )


def test_golden_dataset_encodes_progression_and_opt_out_boundaries() -> None:
    cases = {
        case["id"]: case
        for case in (
            json.loads(line)
            for line in Path("datasets/golden/examples.jsonl").read_text().splitlines()
            if line.strip()
        )
    }

    progression = cases["sdr-conversa-nao-morrer-001"]
    opt_out = cases["sdr-opt-out-encerramento-001"]
    objection = cases["pf-objecao-mid-001"]

    assert "?" in progression["expected"]["ideal_response"]
    assert "?" not in opt_out["expected"]["ideal_response"]
    assert opt_out["cut"]["kind"] == "terminal"
    assert objection["version"] == 2
    assert "naturalidade" in {row["name"] for row in objection["evaluation"]["rubric"]}
