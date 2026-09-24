from __future__ import annotations

import os
import re
import subprocess
import tomllib
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

EMPTY_GIT_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
HUNK_PATTERN = re.compile(r"@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


class QualityGateError(RuntimeError):
    """Raised when a quality check cannot determine a trustworthy result."""


@dataclass(frozen=True)
class QualityConfig:
    source_root: str
    node_root: str
    complexity_warning: int
    complexity_max: int
    node_complexity_max: int
    node_max_lines: int
    node_max_internal_imports: int
    diff_coverage_minimum: float


@dataclass(frozen=True)
class BaseReference:
    requested: str
    revision: str


@dataclass(frozen=True)
class DiffChanges:
    lines_by_path: dict[str, set[int]]
    paths: set[str]


def repository_root() -> Path:
    return Path(__file__).resolve().parents[2]


def load_quality_config(repo_root: Path) -> QualityConfig:
    config = tomllib.loads((repo_root / "pyproject.toml").read_text())
    values = config["tool"]["sdr_quality"]
    return QualityConfig(
        source_root=str(values["source_root"]),
        node_root=str(values["node_root"]),
        complexity_warning=int(values["complexity_warning"]),
        complexity_max=int(values["complexity_max"]),
        node_complexity_max=int(values["node_complexity_max"]),
        node_max_lines=int(values["node_max_lines"]),
        node_max_internal_imports=int(values["node_max_internal_imports"]),
        diff_coverage_minimum=float(values["diff_coverage_minimum"]),
    )


def git_output(repo_root: Path, *args: str, check: bool = True) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
    )
    if check and result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise QualityGateError(f"git {' '.join(args)} failed: {detail}")
    return result.stdout.strip()


def _is_valid_ref(repo_root: Path, ref: str) -> bool:
    if not ref or set(ref) == {"0"}:
        return False
    result = subprocess.run(
        ["git", "rev-parse", "--verify", f"{ref}^{{commit}}"],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def resolve_base_reference(repo_root: Path, requested: str | None = None) -> BaseReference:
    candidates = [
        requested,
        os.environ.get("QUALITY_BASE_REF"),
        "origin/main",
        "HEAD^",
    ]
    seen: set[str] = set()
    for candidate in candidates:
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        if not _is_valid_ref(repo_root, candidate):
            continue
        merge_base = git_output(repo_root, "merge-base", candidate, "HEAD", check=False)
        revision = merge_base or git_output(repo_root, "rev-parse", candidate)
        return BaseReference(requested=candidate, revision=revision)

    head = git_output(repo_root, "rev-parse", "HEAD", check=False)
    if head:
        return BaseReference(requested="empty-tree", revision=EMPTY_GIT_TREE)
    raise QualityGateError("No Git revision is available for the quality comparison base.")


def parse_unified_diff(diff: str) -> DiffChanges:
    lines_by_path: dict[str, set[int]] = defaultdict(set)
    paths: set[str] = set()
    current_path: str | None = None

    for line in diff.splitlines():
        if line.startswith("+++ "):
            value = line[4:].split("\t", 1)[0]
            if value == "/dev/null":
                current_path = None
                continue
            current_path = value[2:] if value.startswith("b/") else value
            paths.add(current_path)
            continue

        match = HUNK_PATTERN.search(line)
        if not match or current_path is None:
            continue
        start = int(match.group(1))
        count = int(match.group(2) or "1")
        if count:
            lines_by_path[current_path].update(range(start, start + count))

    return DiffChanges(lines_by_path=dict(lines_by_path), paths=paths)


def changed_lines(repo_root: Path, base_revision: str, source_root: str) -> DiffChanges:
    diff = git_output(
        repo_root,
        "-c",
        "core.quotePath=false",
        "diff",
        "--unified=0",
        "--find-renames",
        base_revision,
        "--",
        source_root,
    )
    parsed = parse_unified_diff(diff)
    lines_by_path = defaultdict(set, parsed.lines_by_path)
    paths = set(parsed.paths)

    untracked = git_output(
        repo_root,
        "ls-files",
        "--others",
        "--exclude-standard",
        "--",
        source_root,
    )
    for relative_path in untracked.splitlines():
        path = repo_root / relative_path
        if path.suffix != ".py" or not path.exists():
            continue
        paths.add(relative_path)
        lines_by_path[relative_path].update(range(1, len(path.read_text().splitlines()) + 1))

    return DiffChanges(lines_by_path=dict(lines_by_path), paths=paths)
