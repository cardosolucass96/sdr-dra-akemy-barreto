from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

DEFAULT_DATASET_PATH = Path("datasets/golden/examples.jsonl")

CASE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{2,80}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
CPF_RE = re.compile(r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b")
CNPJ_RE = re.compile(r"\b\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}\b")
PHONE_RE = re.compile(r"\b(?:\+?55\s*)?(?:\(?\d{2}\)?\s*)?(?:9\s*)?\d{4}[-\s]?\d{4}\b")

STATUSES = {"candidate", "approved", "deprecated"}
SOURCE_KINDS = {"synthetic", "langfuse_trace", "manual_trace_export", "conversation_review"}
CUT_KINDS = {"conversation_start", "mid_conversation", "after_tool", "handoff", "terminal"}
MESSAGE_ROLES = {"system", "user", "assistant", "tool"}
EVALUATION_TYPES = {"rubric", "exact", "manual_review"}


class DatasetValidationError(ValueError):
    pass


def _path(parent: str, child: str) -> str:
    return f"{parent}.{child}" if parent else child


def _require_mapping(value: Any, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise DatasetValidationError(f"{path} must be an object")
    return value


def _require_string(value: Any, path: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise DatasetValidationError(f"{path} must be a string")
    if not allow_empty and not value.strip():
        raise DatasetValidationError(f"{path} must not be empty")
    return value


def _require_int(value: Any, path: str, *, minimum: int | None = None) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise DatasetValidationError(f"{path} must be an integer")
    if minimum is not None and value < minimum:
        raise DatasetValidationError(f"{path} must be >= {minimum}")
    return value


def _require_number(value: Any, path: str, *, minimum: float, maximum: float) -> float:
    if not isinstance(value, int | float) or isinstance(value, bool):
        raise DatasetValidationError(f"{path} must be a number")
    number = float(value)
    if number < minimum or number > maximum:
        raise DatasetValidationError(f"{path} must be between {minimum} and {maximum}")
    return number


def _require_list(value: Any, path: str, *, min_items: int = 0) -> Sequence[Any]:
    if not isinstance(value, list):
        raise DatasetValidationError(f"{path} must be a list")
    if len(value) < min_items:
        raise DatasetValidationError(f"{path} must have at least {min_items} item(s)")
    return value


def _require_string_list(value: Any, path: str, *, min_items: int = 0) -> list[str]:
    items = _require_list(value, path, min_items=min_items)
    strings: list[str] = []
    for index, item in enumerate(items):
        strings.append(_require_string(item, f"{path}[{index}]"))
    return strings


def _require_enum(value: Any, path: str, allowed: set[str]) -> str:
    text = _require_string(value, path)
    if text not in allowed:
        allowed_values = ", ".join(sorted(allowed))
        raise DatasetValidationError(f"{path} must be one of: {allowed_values}")
    return text


def _optional_string_or_null(value: Any, path: str) -> None:
    if value is None:
        return
    _require_string(value, path)


def _validate_no_unknown_keys(value: Mapping[str, Any], path: str, allowed: set[str]) -> None:
    unknown_keys = sorted(set(value) - allowed)
    if unknown_keys:
        raise DatasetValidationError(f"{path} has unknown key(s): {', '.join(unknown_keys)}")


def _iter_strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
        return

    if isinstance(value, Mapping):
        for nested in value.values():
            yield from _iter_strings(nested)
        return

    if isinstance(value, list):
        for nested in value:
            yield from _iter_strings(nested)


def _validate_no_obvious_pii(case: Mapping[str, Any], line_number: int) -> None:
    searchable = "\n".join(_iter_strings(case))
    patterns = {
        "email": EMAIL_RE,
        "cpf": CPF_RE,
        "cnpj": CNPJ_RE,
        "phone": PHONE_RE,
    }
    for label, pattern in patterns.items():
        if pattern.search(searchable):
            raise DatasetValidationError(
                f"line {line_number}: possible {label} detected; anonymize before versioning"
            )


def _validate_source(value: Any, path: str) -> None:
    source = _require_mapping(value, path)
    _validate_no_unknown_keys(
        source,
        path,
        {"kind", "trace_id", "observation_id", "reviewed_by", "reviewed_at"},
    )
    _require_enum(source.get("kind"), _path(path, "kind"), SOURCE_KINDS)

    for key in ("trace_id", "observation_id", "reviewed_by"):
        if key in source:
            _optional_string_or_null(source[key], _path(path, key))

    if "reviewed_at" in source and source["reviewed_at"] is not None:
        reviewed_at = _require_string(source["reviewed_at"], _path(path, "reviewed_at"))
        if not DATE_RE.match(reviewed_at):
            raise DatasetValidationError(f"{_path(path, 'reviewed_at')} must be YYYY-MM-DD")


def _validate_scenario(value: Any, path: str) -> None:
    scenario = _require_mapping(value, path)
    _validate_no_unknown_keys(scenario, path, {"title", "description", "tags"})
    _require_string(scenario.get("title"), _path(path, "title"))
    _require_string(scenario.get("description"), _path(path, "description"))
    _require_string_list(scenario.get("tags"), _path(path, "tags"), min_items=1)


def _validate_cut(value: Any, path: str) -> None:
    cut = _require_mapping(value, path)
    _validate_no_unknown_keys(cut, path, {"kind", "turn_index", "reason"})
    _require_enum(cut.get("kind"), _path(path, "kind"), CUT_KINDS)
    _require_int(cut.get("turn_index"), _path(path, "turn_index"), minimum=0)
    _require_string(cut.get("reason"), _path(path, "reason"))


def _validate_messages(value: Any, path: str) -> None:
    messages = _require_list(value, path, min_items=1)
    for index, item in enumerate(messages):
        message_path = f"{path}[{index}]"
        message = _require_mapping(item, message_path)
        _validate_no_unknown_keys(message, message_path, {"role", "content", "name"})
        _require_enum(message.get("role"), _path(message_path, "role"), MESSAGE_ROLES)
        _require_string(message.get("content"), _path(message_path, "content"))
        if "name" in message:
            _optional_string_or_null(message["name"], _path(message_path, "name"))


def _validate_input(value: Any, path: str) -> None:
    input_value = _require_mapping(value, path)
    _validate_no_unknown_keys(input_value, path, {"messages_so_far", "state_snapshot"})
    _validate_messages(input_value.get("messages_so_far"), _path(path, "messages_so_far"))
    if "state_snapshot" in input_value:
        _require_mapping(input_value["state_snapshot"], _path(path, "state_snapshot"))


def _validate_expected(value: Any, path: str) -> None:
    expected = _require_mapping(value, path)
    _validate_no_unknown_keys(
        expected,
        path,
        {"next_behavior", "ideal_response", "success_criteria", "must_not"},
    )
    _require_string(expected.get("next_behavior"), _path(path, "next_behavior"))
    _require_string(expected.get("ideal_response"), _path(path, "ideal_response"))
    _require_string_list(
        expected.get("success_criteria"),
        _path(path, "success_criteria"),
        min_items=1,
    )
    _require_string_list(expected.get("must_not"), _path(path, "must_not"))


def _validate_rubric(value: Any, path: str) -> None:
    rubric = _require_list(value, path)
    total_weight = 0.0

    for index, item in enumerate(rubric):
        item_path = f"{path}[{index}]"
        row = _require_mapping(item, item_path)
        _validate_no_unknown_keys(row, item_path, {"name", "weight", "description"})
        _require_string(row.get("name"), _path(item_path, "name"))
        total_weight += _require_number(
            row.get("weight"),
            _path(item_path, "weight"),
            minimum=0,
            maximum=1,
        )
        _require_string(row.get("description"), _path(item_path, "description"))

    if rubric and round(total_weight, 6) != 1.0:
        raise DatasetValidationError(f"{path} weights must sum to 1.0")


def _validate_evaluation(value: Any, path: str) -> None:
    evaluation = _require_mapping(value, path)
    _validate_no_unknown_keys(evaluation, path, {"type", "minimum_score", "rubric"})
    _require_enum(evaluation.get("type"), _path(path, "type"), EVALUATION_TYPES)
    _require_number(
        evaluation.get("minimum_score"),
        _path(path, "minimum_score"),
        minimum=0,
        maximum=1,
    )
    _validate_rubric(evaluation.get("rubric"), _path(path, "rubric"))


def _validate_metadata(value: Any, path: str) -> None:
    metadata = _require_mapping(value, path)
    _validate_no_unknown_keys(metadata, path, {"created_at", "owner", "notes"})
    created_at = _require_string(metadata.get("created_at"), _path(path, "created_at"))
    if not DATE_RE.match(created_at):
        raise DatasetValidationError(f"{_path(path, 'created_at')} must be YYYY-MM-DD")
    _require_string(metadata.get("owner"), _path(path, "owner"))
    _require_string(metadata.get("notes"), _path(path, "notes"), allow_empty=True)


def validate_case(case: Any, *, line_number: int) -> str:
    value = _require_mapping(case, f"line {line_number}")
    _validate_no_obvious_pii(value, line_number)
    _validate_no_unknown_keys(
        value,
        f"line {line_number}",
        {
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
        },
    )

    case_id = _require_string(value.get("id"), f"line {line_number}.id")
    if not CASE_ID_RE.match(case_id):
        raise DatasetValidationError(f"line {line_number}.id must match {CASE_ID_RE.pattern}")

    _require_int(value.get("version"), f"line {line_number}.version", minimum=1)
    _require_enum(value.get("status"), f"line {line_number}.status", STATUSES)
    _validate_source(value.get("source"), f"line {line_number}.source")
    _validate_scenario(value.get("scenario"), f"line {line_number}.scenario")
    _validate_cut(value.get("cut"), f"line {line_number}.cut")
    _validate_input(value.get("input"), f"line {line_number}.input")
    _validate_expected(value.get("expected"), f"line {line_number}.expected")
    _validate_evaluation(value.get("evaluation"), f"line {line_number}.evaluation")
    _validate_metadata(value.get("metadata"), f"line {line_number}.metadata")

    return case_id


def validate_file(path: Path) -> list[str]:
    errors: list[str] = []
    seen_ids: set[str] = set()

    if not path.exists():
        return [f"{path} does not exist"]

    for line_number, line in enumerate(path.read_text().splitlines(), start=1):
        if not line.strip():
            continue

        try:
            case = json.loads(line)
            case_id = validate_case(case, line_number=line_number)
        except json.JSONDecodeError as exc:
            errors.append(f"line {line_number}: invalid JSON: {exc.msg}")
            continue
        except DatasetValidationError as exc:
            errors.append(str(exc))
            continue

        if case_id in seen_ids:
            errors.append(f"line {line_number}: duplicated id {case_id}")
        seen_ids.add(case_id)

    if not seen_ids:
        errors.append(f"{path} has no dataset cases")

    return errors


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate the Pipefacil golden dataset JSONL file."
    )
    parser.add_argument(
        "path",
        nargs="?",
        type=Path,
        default=DEFAULT_DATASET_PATH,
        help=f"Dataset JSONL path. Defaults to {DEFAULT_DATASET_PATH}.",
    )
    args = parser.parse_args(argv)

    errors = validate_file(args.path)
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1

    print(f"OK: {args.path} is valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
