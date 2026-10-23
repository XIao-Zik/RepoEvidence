"""Run versioned offline evidence cases without a model provider or vector database."""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from repo_evidence.repositories.evaluation import Citation, RepositoryCase, score_case


@dataclass(frozen=True)
class EvaluationReport:
    total: int
    passed: int
    citation_accuracy: float
    citation_coverage: float
    elapsed_ms: int
    failed_case_ids: tuple[str, ...]
    detail_path: Path
    summary_path: Path


def run_offline_evaluation(
    case_file: Path, artifact_dir: Path, summary_path: Path
) -> EvaluationReport:
    """Score deterministic cases and separate local detail from safe committed metrics."""
    started = time.perf_counter()
    payload = json.loads(case_file.read_text(encoding="utf-8"))
    raw_cases = payload.get("cases")
    if not isinstance(raw_cases, list) or not raw_cases:
        raise ValueError("Evaluation cases must contain a non-empty cases list")
    results: list[dict[str, Any]] = []
    for raw in raw_cases:
        if not isinstance(raw, dict) or not isinstance(raw.get("id"), str):
            raise ValueError("Each evaluation case must have a string id")
        expected = tuple(_citation(item) for item in _list(raw, "expected_citations"))
        actual = [_citation(item) for item in _list(raw, "citations")]
        case = RepositoryCase(
            question=_string(raw, "question"),
            expected_citations=expected,
            required_terms=tuple(_strings(raw, "required_terms")),
            forbidden_paths=tuple(_strings(raw, "forbidden_paths")),
        )
        score = score_case(_string(raw, "answer"), actual, case)
        results.append({"id": raw["id"], **asdict(score)})
    total = len(results)
    passed = sum(result["passed"] for result in results)
    report = EvaluationReport(
        total=total,
        passed=passed,
        citation_accuracy=sum(result["citation_accuracy"] for result in results) / total,
        citation_coverage=sum(result["citation_coverage"] for result in results) / total,
        elapsed_ms=round((time.perf_counter() - started) * 1000),
        failed_case_ids=tuple(result["id"] for result in results if not result["passed"]),
        detail_path=artifact_dir / "offline-evaluation.json",
        summary_path=summary_path,
    )
    artifact_dir.mkdir(parents=True, exist_ok=True)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    report.detail_path.write_text(
        json.dumps({**_safe_report(report), "cases": results}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    report.summary_path.write_text(_markdown(report), encoding="utf-8")
    return report


def _safe_report(report: EvaluationReport) -> dict[str, Any]:
    return {
        "total": report.total,
        "passed": report.passed,
        "citation_accuracy": report.citation_accuracy,
        "citation_coverage": report.citation_coverage,
        "elapsed_ms": report.elapsed_ms,
        "failed_case_ids": list(report.failed_case_ids),
    }


def _markdown(report: EvaluationReport) -> str:
    failed = ", ".join(report.failed_case_ids) if report.failed_case_ids else "无"
    return (
        "# RepoEvidence 离线评测\n\n"
        "此报告来自确定性测试用例；未调用在线模型，也不包含问题正文、回答或密钥。\n\n"
        "| 指标 | 值 |\n| --- | ---: |\n"
        f"| 用例数 | {report.total} |\n| 通过数 | {report.passed} |\n"
        f"| 引用准确率 | {report.citation_accuracy:.1%} |\n"
        f"| 引用覆盖率 | {report.citation_coverage:.1%} |\n"
        f"| 耗时 | {report.elapsed_ms} ms |\n| 失败用例 ID | {failed} |\n"
    )


def _list(raw: dict[str, Any], key: str) -> list[dict[str, Any]]:
    value = raw.get(key, [])
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise ValueError(f"{key} must be a list of objects")
    return value


def _strings(raw: dict[str, Any], key: str) -> list[str]:
    value = raw.get(key, [])
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{key} must be a list of strings")
    return value


def _string(raw: dict[str, Any], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a string")
    return value


def _citation(raw: dict[str, Any]) -> Citation:
    path = raw.get("path")
    start_line, end_line = raw.get("start_line"), raw.get("end_line")
    if (
        not isinstance(path, str)
        or not isinstance(start_line, int)
        or not isinstance(end_line, int)
    ):
        raise ValueError("Citation needs path, start_line, and end_line")
    return Citation(path, start_line, end_line)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    root = Path(__file__).resolve().parents[4]
    report = run_offline_evaluation(
        root / "evaluation/cases/repository-evidence.json",
        root / "artifacts/evaluation",
        root / "docs/quality/offline-evaluation.md",
    )
    print(json.dumps(_safe_report(report), ensure_ascii=False))


if __name__ == "__main__":
    main()
