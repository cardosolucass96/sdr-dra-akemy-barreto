#!/usr/bin/env python3
"""Require line and branch coverage for executable Python lines changed by a diff."""

from __future__ import annotations

import argparse
import re
import xml.etree.ElementTree as ElementTree
from dataclasses import dataclass
from pathlib import Path

from scripts.quality.common import (
    BaseReference,
    QualityGateError,
    changed_lines,
    load_quality_config,
    repository_root,
    resolve_base_reference,
)

CONDITION_COVERAGE_PATTERN = re.compile(r"\((\d+)/(\d+)\)")


@dataclass(frozen=True)
class CoveredLine:
    hits: int
    branches_covered: int = 0
    branches_total: int = 0


@dataclass(frozen=True)
class CoverageResult:
    line_covered: int
    line_total: int
    branch_covered: int
    branch_total: int
    uncovered_lines: tuple[str, ...]
    partial_branches: tuple[str, ...]

    @property
    def line_percent(self) -> float:
        return 100.0 if self.line_total == 0 else 100 * self.line_covered / self.line_total

    @property
    def branch_percent(self) -> float:
        return 100.0 if self.branch_total == 0 else 100 * self.branch_covered / self.branch_total


def parse_cobertura_coverage(report_path: Path) -> dict[str, dict[int, CoveredLine]]:
    root = ElementTree.parse(report_path).getroot()
    result: dict[str, dict[int, CoveredLine]] = {}
    for class_element in root.findall("./packages/package/classes/class"):
        path = class_element.attrib["filename"].removeprefix("./")
        covered_lines: dict[int, CoveredLine] = {}
        for line in class_element.findall("./lines/line"):
            branch_covered = branch_total = 0
            condition_coverage = line.attrib.get("condition-coverage")
            if condition_coverage:
                match = CONDITION_COVERAGE_PATTERN.search(condition_coverage)
                if match:
                    branch_covered = int(match.group(1))
                    branch_total = int(match.group(2))
            covered_lines[int(line.attrib["number"])] = CoveredLine(
                hits=int(line.attrib.get("hits", "0")),
                branches_covered=branch_covered,
                branches_total=branch_total,
            )
        result[path] = covered_lines
    return result


def evaluate_diff_coverage(
    changed: dict[str, set[int]],
    coverage: dict[str, dict[int, CoveredLine]],
) -> CoverageResult:
    line_covered = line_total = branch_covered = branch_total = 0
    uncovered_lines: list[str] = []
    partial_branches: list[str] = []

    for path, line_numbers in sorted(changed.items()):
        path_coverage = coverage.get(path, {})
        for line_number in sorted(line_numbers):
            counters = path_coverage.get(line_number)
            if counters is None:
                continue
            line_total += 1
            if counters.hits:
                line_covered += 1
            else:
                uncovered_lines.append(f"{path}:{line_number}")
            branch_covered += counters.branches_covered
            branch_total += counters.branches_total
            if counters.branches_covered < counters.branches_total:
                partial_branches.append(
                    f"{path}:{line_number} ({counters.branches_covered}/{counters.branches_total})"
                )

    return CoverageResult(
        line_covered=line_covered,
        line_total=line_total,
        branch_covered=branch_covered,
        branch_total=branch_total,
        uncovered_lines=tuple(uncovered_lines),
        partial_branches=tuple(partial_branches),
    )


def render_report(
    base: BaseReference,
    result: CoverageResult,
    minimum: float,
) -> tuple[str, bool]:
    passed = result.line_percent >= minimum and result.branch_percent >= minimum
    status = "PASSOU" if passed else "FALHOU"
    lines = [
        "## Cobertura do diff Python",
        "",
        "- Regra: `TEST-COV-001`",
        f"- Base: `{base.requested}` (comparacao `{base.revision}`)",
        f"- Status: **{status}**",
        f"- Linhas executaveis alteradas: **{result.line_covered}/{result.line_total} "
        f"({result.line_percent:.1f}%)**",
        f"- Ramos nas linhas alteradas: **{result.branch_covered}/{result.branch_total} "
        f"({result.branch_percent:.1f}%)**",
        f"- Minimo: **{minimum:.0f}%**",
    ]
    if result.line_total == 0:
        lines.append("- Nenhuma linha Python executavel foi alterada; gate nao aplicavel.")
    if result.uncovered_lines:
        lines.extend(["", "### Linhas sem cobertura", ""])
        lines.extend(f"- `{item}`" for item in result.uncovered_lines[:100])
    if result.partial_branches:
        lines.extend(["", "### Ramos parcialmente cobertos", ""])
        lines.extend(f"- `{item}`" for item in result.partial_branches[:100])
    return "\n".join(lines) + "\n", passed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base")
    parser.add_argument("--coverage", default="build/quality/coverage.xml")
    parser.add_argument("--minimum", type=float)
    parser.add_argument("--report", default="build/quality/diff-coverage.md")
    args = parser.parse_args()

    repo_root = repository_root()
    report_path = repo_root / args.report
    report_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        config = load_quality_config(repo_root)
        minimum = args.minimum if args.minimum is not None else config.diff_coverage_minimum
        coverage_path = repo_root / args.coverage
        if not coverage_path.exists():
            raise QualityGateError(
                f"Coverage XML not found at {coverage_path}; "
                "run pytest with --cov-report=xml first."
            )
        base = resolve_base_reference(repo_root, args.base)
        changes = changed_lines(repo_root, base.revision, config.source_root)
        coverage = parse_cobertura_coverage(coverage_path)
        result = evaluate_diff_coverage(changes.lines_by_path, coverage)
        report, passed = render_report(base, result, minimum)
    except (ElementTree.ParseError, OSError, QualityGateError, KeyError, ValueError) as exc:
        report = f"## Cobertura do diff Python\n\n- Status: **ERRO**\n- {exc}\n"
        exit_code = 2
    else:
        exit_code = 0 if passed else 1

    report_path.write_text(report)
    print(report, end="")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
