"""Run an explicit, real-Agent evidence suite through the public session API."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from .online import OnlineCase, OnlineCaseResult, load_online_cases, percentile, score_online_answer


class OnlineEvaluationPreflightError(RuntimeError):
    """The selected live target is not safe or ready for a paid evaluation run."""


@dataclass(frozen=True)
class OnlineEvaluationReport:
    """Aggregated online suite metrics plus safe/local output locations."""

    total: int
    completed: int
    strict_passed: int
    citation_accuracy: float | None
    citation_coverage: float | None
    latency_p50_ms: int | None
    latency_p95_ms: int | None
    failed_case_ids: tuple[str, ...]
    model: str
    suite_sha256: str
    detail_path: Path
    summary_path: Path


def run_online_evaluation(
    client: httpx.Client,
    base_url: str,
    repository_id: str,
    collection_id: str,
    cases: tuple[OnlineCase, ...],
    artifact_dir: Path,
    summary_path: Path,
    *,
    poll_seconds: float,
    timeout_seconds: float,
) -> OnlineEvaluationReport:
    """Run cases serially, preserving failures and keeping raw answers out of Markdown."""
    if not cases:
        raise ValueError("Online evaluation requires at least one case")
    if poll_seconds < 0 or timeout_seconds <= 0:
        raise ValueError("poll_seconds must be non-negative and timeout_seconds must be positive")
    root = base_url.rstrip("/")
    _preflight(client, root, repository_id, collection_id)
    model = _model(client, root)
    details: list[dict[str, Any]] = []
    scored: list[OnlineCaseResult] = []
    failed_ids: list[str] = []
    for case in cases:
        started = time.perf_counter()
        session_id: str | None = None
        try:
            created = _request(
                client,
                "POST",
                f"{root}/api/sessions",
                json={"repository_id": repository_id, "collection_id": collection_id},
            )
            session_id = _text(created, "id")
            _request(
                client,
                "POST",
                f"{root}/api/sessions/{session_id}/messages",
                json={"request_id": str(uuid.uuid4()), "content": case.question},
            )
            view = _wait_for_terminal(
                client, root, session_id, poll_seconds=poll_seconds, timeout_seconds=timeout_seconds
            )
            status = _text(view, "status")
            elapsed_ms = round((time.perf_counter() - started) * 1000)
            if status != "completed":
                raise RuntimeError(f"session finished with status {status}")
            answer = _assistant_answer(view)
            result = score_online_answer(case, answer, elapsed_ms)
            scored.append(result)
            details.append({"status": "completed", **_result_detail(result)})
        except (httpx.HTTPError, RuntimeError, ValueError) as error:
            failed_ids.append(case.id)
            details.append(
                {
                    "case_id": case.id,
                    "category": case.category,
                    "status": "failed",
                    "reason": str(error),
                    "elapsed_ms": round((time.perf_counter() - started) * 1000),
                }
            )
        finally:
            if session_id is not None:
                try:
                    _request(client, "DELETE", f"{root}/api/sessions/{session_id}")
                except httpx.HTTPError:
                    pass
    report = _report(cases, scored, failed_ids, model, artifact_dir, summary_path)
    artifact_dir.mkdir(parents=True, exist_ok=True)
    report.summary_path.parent.mkdir(parents=True, exist_ok=True)
    report.detail_path.write_text(
        json.dumps(
            {
                "suite_sha256": report.suite_sha256,
                "model": report.model,
                "total": report.total,
                "cases": details,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    report.summary_path.write_text(_markdown(report), encoding="utf-8")
    return report


def select_target(
    repositories: list[object], *, repository_id: str | None, collection_id: str | None
) -> tuple[str, str]:
    """Select an explicit target or the sole ready repository without guesswork."""
    records = [item for item in repositories if isinstance(item, dict)]
    if repository_id is None:
        ready = [item for item in records if item.get("status") == "ready"]
        if len(ready) != 1:
            raise OnlineEvaluationPreflightError(
                "Set repository ID when there is not exactly one ready repository"
            )
        selected = ready[0]
    else:
        selected = next((item for item in records if item.get("id") == repository_id), None)
        if selected is None:
            raise OnlineEvaluationPreflightError("Selected repository was not found")
    selected_id = selected.get("id")
    current = selected.get("current_collection_id")
    if not isinstance(selected_id, str) or not isinstance(current, str) or not current:
        raise OnlineEvaluationPreflightError("Selected repository has no current collection")
    if collection_id is not None and collection_id != current:
        raise OnlineEvaluationPreflightError("Selected collection is not the current collection")
    return selected_id, current


def list_repositories(client: httpx.Client, base_url: str) -> list[object]:
    """Read the list-shaped repository API response without coercing it into an object."""
    response = client.get(base_url.rstrip("/") + "/api/repositories")
    response.raise_for_status()
    value = response.json()
    if not isinstance(value, list):
        raise OnlineEvaluationPreflightError("Repository list response must be a list")
    return value


def _preflight(client: httpx.Client, root: str, repository_id: str, collection_id: str) -> None:
    health = _request(client, "GET", f"{root}/api/health")
    if health.get("status") != "ok":
        raise OnlineEvaluationPreflightError("Live service health is not ok")
    repository = _request(client, "GET", f"{root}/api/repositories/{repository_id}")
    if repository.get("status") != "ready":
        raise OnlineEvaluationPreflightError(
            "Selected repository must be ready before online evaluation"
        )
    if repository.get("current_collection_id") != collection_id:
        raise OnlineEvaluationPreflightError(
            "Selected collection must equal the repository current collection"
        )


def _model(client: httpx.Client, root: str) -> str:
    try:
        value = _request(client, "GET", f"{root}/api/meta").get("model")
    except httpx.HTTPError:
        return "unknown"
    return value if isinstance(value, str) and value else "unknown"


def _wait_for_terminal(
    client: httpx.Client,
    root: str,
    session_id: str,
    *,
    poll_seconds: float,
    timeout_seconds: float,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    while True:
        view = _request(client, "GET", f"{root}/api/sessions/{session_id}")
        status = view.get("status")
        if status in {"completed", "waiting_user", "failed", "canceled", "interrupted"}:
            return view
        if time.monotonic() >= deadline:
            raise RuntimeError("session timed out")
        if poll_seconds:
            time.sleep(poll_seconds)


def _assistant_answer(view: dict[str, Any]) -> str:
    messages = view.get("messages")
    if not isinstance(messages, list):
        raise RuntimeError("session response has no messages")
    for message in reversed(messages):
        if isinstance(message, dict) and message.get("role") == "assistant":
            content = message.get("content")
            if isinstance(content, str) and content.strip():
                return content
    raise RuntimeError("completed session has no assistant answer")


def _request(client: httpx.Client, method: str, url: str, **kwargs: object) -> dict[str, Any]:
    response = client.request(method, url, **kwargs)
    response.raise_for_status()
    value = response.json()
    if not isinstance(value, dict):
        raise RuntimeError(f"{method} {url} returned a non-object response")
    return value


def _text(value: dict[str, Any], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item:
        raise RuntimeError(f"response field {key} must be a non-empty string")
    return item


def _result_detail(result: OnlineCaseResult) -> dict[str, Any]:
    value = asdict(result)
    value["citations"] = [asdict(citation) for citation in result.citations]
    return value


def _report(
    cases: tuple[OnlineCase, ...],
    scored: list[OnlineCaseResult],
    failed_ids: list[str],
    model: str,
    artifact_dir: Path,
    summary_path: Path,
) -> OnlineEvaluationReport:
    total = len(cases)
    completed = len(scored)
    accuracy = sum(item.citation_accuracy for item in scored) / completed if completed else None
    coverage = sum(item.citation_coverage for item in scored) / completed if completed else None
    timings = [item.elapsed_ms for item in scored]
    suite_data = json.dumps(
        [asdict(case) for case in cases],
        default=asdict,
        ensure_ascii=False,
        sort_keys=True,
    )
    suite_sha256 = hashlib.sha256(suite_data.encode()).hexdigest()
    return OnlineEvaluationReport(
        total=total,
        completed=completed,
        strict_passed=sum(item.strict_passed for item in scored),
        citation_accuracy=accuracy,
        citation_coverage=coverage,
        latency_p50_ms=percentile(timings, 0.5) if timings else None,
        latency_p95_ms=percentile(timings, 0.95) if timings else None,
        failed_case_ids=tuple(failed_ids),
        model=model,
        suite_sha256=suite_sha256,
        detail_path=artifact_dir / "online-evaluation.json",
        summary_path=summary_path,
    )


def _markdown(report: OnlineEvaluationReport) -> str:
    failed = ", ".join(report.failed_case_ids) if report.failed_case_ids else "无"
    accuracy = f"{report.citation_accuracy:.1%}" if report.citation_accuracy is not None else "N/A"
    coverage = f"{report.citation_coverage:.1%}" if report.citation_coverage is not None else "N/A"
    p50 = f"{report.latency_p50_ms} ms" if report.latency_p50_ms is not None else "N/A"
    p95 = f"{report.latency_p95_ms} ms" if report.latency_p95_ms is not None else "N/A"
    return (
        "# RepoEvidence 真实 Agent 评测\n\n"
        "此报告是指定题集、模型与本地环境的一次测量，不代表跨仓库通用准确率。"
        "原始问题、回答和会话事件仅保存在本地忽略工件。\n\n"
        f"- 运行时间（UTC）：{datetime.now(UTC).isoformat()}\n"
        f"- 模型：{report.model}\n"
        f"- 题集 SHA-256：{report.suite_sha256}\n"
        "- 评分方法：docs/superpowers/specs/2026-10-20-resume-ready-evaluation-design.md\n\n"
        "| 指标 | 值 |\n| --- | ---: |\n"
        f"| 题目数 | {report.total} |\n"
        f"| 成功完成数 | {report.completed} |\n"
        f"| 严格任务通过数 | {report.strict_passed} |\n"
        f"| 引用准确率 | {accuracy} |\n"
        f"| 引用覆盖率 | {coverage} |\n"
        f"| 端到端延迟 P50 | {p50} |\n"
        f"| 端到端延迟 P95 | {p95} |\n"
        f"| 失败用例 ID | {failed} |\n\n"
        "限制：严格通过率要求全部标准术语及标准行级证据，未衡量开放式自然语言质量；"
        "请同时陈述样本数、模型、日期和固定环境。\n"
    )


def main() -> None:
    """Run the opt-in suite against the locally running service."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-url",
        default=os.environ.get("REPO_EVIDENCE_EVAL_BASE_URL", "http://backend:8000"),
    )
    parser.add_argument(
        "--repository-id", default=os.environ.get("REPO_EVIDENCE_EVAL_REPOSITORY_ID")
    )
    parser.add_argument(
        "--collection-id", default=os.environ.get("REPO_EVIDENCE_EVAL_COLLECTION_ID")
    )
    parser.add_argument("--case-file", type=Path, default=None)
    parser.add_argument("--poll-seconds", type=float, default=0.25)
    parser.add_argument("--timeout-seconds", type=float, default=180.0)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[4]
    cases = load_online_cases(
        args.case_file or root / "evaluation/cases/repository-evidence-online.json"
    )
    with httpx.Client(timeout=args.timeout_seconds) as client:
        repository_id, collection_id = select_target(
            list_repositories(client, args.base_url),
            repository_id=args.repository_id,
            collection_id=args.collection_id,
        )
        report = run_online_evaluation(
            client,
            args.base_url,
            repository_id,
            collection_id,
            cases,
            root / "artifacts/evaluation",
            root / "docs/quality/resume-evaluation.md",
            poll_seconds=args.poll_seconds,
            timeout_seconds=args.timeout_seconds,
        )
    print(
        json.dumps(
            {
                "total": report.total,
                "completed": report.completed,
                "strict_passed": report.strict_passed,
                "citation_accuracy": report.citation_accuracy,
                "citation_coverage": report.citation_coverage,
                "latency_p50_ms": report.latency_p50_ms,
                "latency_p95_ms": report.latency_p95_ms,
                "failed_case_ids": list(report.failed_case_ids),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
