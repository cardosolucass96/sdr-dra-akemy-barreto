from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "src"))

DEFAULT_DATASET_PATH = Path("datasets/golden/examples.jsonl")
DEFAULT_MANIFEST_PATH = Path("datasets/golden/manifest.json")
DEFAULT_SYNC_STATUSES = ("candidate", "approved")

INPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["messages_so_far"],
    "properties": {
        "messages_so_far": {"type": "array", "minItems": 1},
        "state_snapshot": {"type": "object"},
    },
}
EXPECTED_OUTPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["next_behavior", "ideal_response", "success_criteria", "must_not"],
    "properties": {
        "next_behavior": {"type": "string"},
        "ideal_response": {"type": "string"},
        "success_criteria": {"type": "array", "items": {"type": "string"}},
        "must_not": {"type": "array", "items": {"type": "string"}},
    },
}


def load_manifest(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def load_cases(path: Path) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for line in path.read_text().splitlines():
        if line.strip():
            cases.append(json.loads(line))
    return cases


def filter_cases(
    cases: Sequence[dict[str, Any]],
    *,
    statuses: set[str],
) -> list[dict[str, Any]]:
    return [case for case in cases if str(case.get("status")) in statuses]


def langfuse_dataset_item_id(*, dataset_name: str, case_id: str) -> str:
    safe_dataset_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", dataset_name).strip("_")
    return f"{safe_dataset_name}__{case_id}"


def build_langfuse_dataset_metadata(
    *,
    manifest: Mapping[str, Any],
    dataset_path: Path,
) -> dict[str, Any]:
    return {
        "source": "local_golden_dataset",
        "source_file": str(dataset_path),
        "manifest_version": manifest.get("version"),
        "case_statuses": manifest.get("case_statuses", []),
        "pii_policy": manifest.get("pii_policy", {}),
        "langfuse_evaluation_standard": manifest.get("langfuse_evaluation_standard", {}),
    }


def build_langfuse_dataset_item_payload(
    case: Mapping[str, Any],
    *,
    dataset_name: str,
) -> dict[str, Any]:
    source = _mapping(case.get("source", {}))
    input_value = _mapping(case["input"])
    expected = _mapping(case["expected"])

    payload: dict[str, Any] = {
        "id": langfuse_dataset_item_id(dataset_name=dataset_name, case_id=str(case["id"])),
        "dataset_name": dataset_name,
        "input": {
            "messages_so_far": input_value["messages_so_far"],
        },
        "expected_output": {
            "next_behavior": expected["next_behavior"],
            "ideal_response": expected["ideal_response"],
            "success_criteria": expected["success_criteria"],
            "must_not": expected["must_not"],
        },
        "metadata": {
            "case_id": case["id"],
            "case_version": case["version"],
            "case_status": case["status"],
            "source": source,
            "scenario": case["scenario"],
            "cut": case["cut"],
            "evaluation": case["evaluation"],
            "local_metadata": case["metadata"],
        },
    }

    state_snapshot = input_value.get("state_snapshot")
    if isinstance(state_snapshot, Mapping):
        payload["input"]["state_snapshot"] = dict(state_snapshot)

    source_trace_id = _safe_langfuse_source_id(source.get("trace_id"))
    source_observation_id = _safe_langfuse_source_id(source.get("observation_id"))
    if source_trace_id:
        payload["source_trace_id"] = source_trace_id
    if source_observation_id:
        payload["source_observation_id"] = source_observation_id

    return payload


def sync_cases_to_langfuse(
    *,
    client: Any,
    dataset_name: str,
    manifest: Mapping[str, Any],
    dataset_path: Path,
    cases: Sequence[dict[str, Any]],
) -> None:
    _create_dataset_if_needed(
        client=client,
        dataset_name=dataset_name,
        description=str(manifest.get("description") or ""),
        metadata=build_langfuse_dataset_metadata(manifest=manifest, dataset_path=dataset_path),
    )

    for case in cases:
        _create_dataset_item(
            client,
            build_langfuse_dataset_item_payload(case, dataset_name=dataset_name),
        )

    flush = getattr(client, "flush", None)
    if callable(flush):
        flush()


def _create_dataset_if_needed(
    *,
    client: Any,
    dataset_name: str,
    description: str,
    metadata: dict[str, Any],
) -> None:
    try:
        client.create_dataset(
            name=dataset_name,
            description=description,
            metadata=metadata,
            input_schema=INPUT_SCHEMA,
            expected_output_schema=EXPECTED_OUTPUT_SCHEMA,
        )
        print(f"Created Langfuse dataset: {dataset_name}")
    except Exception as exc:
        if _looks_like_dataset_already_exists(exc):
            print(f"Langfuse dataset already exists: {dataset_name}")
            return
        raise


def _create_dataset_item(client: Any, payload: dict[str, Any]) -> None:
    try:
        client.create_dataset_item(**payload)
    except Exception as exc:
        if not _looks_like_dataset_item_response_parse_error(exc):
            raise
        _create_dataset_item_with_rest(payload)


def _create_dataset_item_with_rest(payload: Mapping[str, Any]) -> None:
    from app.core.config import get_settings

    settings = get_settings()
    if not settings.langfuse_public_key or not settings.langfuse_secret_key:
        raise RuntimeError("Langfuse credentials are required for REST dataset item sync.")
    if not settings.langfuse_base_url:
        raise RuntimeError("LANGFUSE_BASE_URL is required for REST dataset item sync.")

    response = httpx.post(
        f"{settings.langfuse_base_url.rstrip('/')}/api/public/dataset-items",
        auth=(settings.langfuse_public_key, settings.langfuse_secret_key),
        json=build_langfuse_rest_dataset_item_payload(payload),
        timeout=20,
    )
    if response.is_error:
        raise RuntimeError(
            "Langfuse dataset item REST sync failed "
            f"with status {response.status_code}: {response.text}"
        )


def build_langfuse_rest_dataset_item_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "datasetName": payload["dataset_name"],
        "input": payload.get("input"),
        "expectedOutput": payload.get("expected_output"),
        "metadata": payload.get("metadata"),
        "sourceTraceId": payload.get("source_trace_id"),
        "sourceObservationId": payload.get("source_observation_id"),
        "id": payload.get("id"),
    }


def _mapping(value: Any) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"Expected mapping, got {type(value).__name__}")
    return value


def _safe_langfuse_source_id(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    if not stripped or stripped.startswith("["):
        return None
    return stripped


def _looks_like_dataset_already_exists(exc: Exception) -> bool:
    message = str(exc).lower()
    return "already exists" in message or "conflict" in message or "409" in message


def _looks_like_dataset_item_response_parse_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return "validation" in message and "media_references" in message


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Sync the local golden dataset JSONL into a Langfuse Dataset."
    )
    parser.add_argument(
        "--env-file",
        action="append",
        help="Optional environment file to load before connecting to Langfuse.",
    )
    parser.add_argument(
        "--dataset-path",
        type=Path,
        default=DEFAULT_DATASET_PATH,
        help=f"Golden dataset JSONL path. Defaults to {DEFAULT_DATASET_PATH}.",
    )
    parser.add_argument(
        "--manifest-path",
        type=Path,
        default=DEFAULT_MANIFEST_PATH,
        help=f"Golden dataset manifest path. Defaults to {DEFAULT_MANIFEST_PATH}.",
    )
    parser.add_argument(
        "--dataset-name",
        help="Langfuse dataset name. Defaults to manifest.name.",
    )
    parser.add_argument(
        "--status",
        action="append",
        choices=["candidate", "approved", "deprecated"],
        help="Case status to sync. May be repeated. Defaults to candidate and approved.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate and print what would be synced without calling Langfuse.",
    )
    args = parser.parse_args(argv)

    for env_file in args.env_file or []:
        load_dotenv(env_file, override=True)

    from scripts.validate_golden_dataset import validate_file

    errors = validate_file(args.dataset_path)
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1

    manifest = load_manifest(args.manifest_path)
    dataset_name = args.dataset_name or str(manifest["name"])
    statuses = set(args.status or DEFAULT_SYNC_STATUSES)
    cases = filter_cases(load_cases(args.dataset_path), statuses=statuses)

    if not cases:
        print(f"No cases found for statuses: {', '.join(sorted(statuses))}")
        return 1

    if args.dry_run:
        print(
            f"DRY RUN: would sync {len(cases)} case(s) to Langfuse dataset "
            f"{dataset_name!r}: {', '.join(case['id'] for case in cases)}"
        )
        return 0

    from app.observability.langfuse import get_langfuse_client

    client = get_langfuse_client()
    if client is None:
        print(
            "Langfuse is not configured. Set LANGFUSE_ENABLED, LANGFUSE_PUBLIC_KEY, "
            "LANGFUSE_SECRET_KEY and LANGFUSE_BASE_URL before syncing the dataset.",
            file=sys.stderr,
        )
        return 1

    sync_cases_to_langfuse(
        client=client,
        dataset_name=dataset_name,
        manifest=manifest,
        dataset_path=args.dataset_path,
        cases=cases,
    )
    print(f"Synced {len(cases)} case(s) to Langfuse dataset: {dataset_name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
