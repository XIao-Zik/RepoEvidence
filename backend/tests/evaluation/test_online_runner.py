from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from repo_evidence.evaluation.online import OnlineCase
from repo_evidence.evaluation.online_runner import (
    OnlineEvaluationPreflightError,
    list_repositories,
    run_online_evaluation,
    select_target,
)
from repo_evidence.repositories.evaluation import Citation

CASES = (
    OnlineCase(
        id="case-1",
        category="architecture",
        question="系统提示有什么要求？",
        expected_citations=(Citation("backend/src/repo_evidence/workflows/evidence.py", 29, 33),),
        required_terms=("path:start-end",),
    ),
)


def test_runner_rejects_not_ready_repository_without_writing_report(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/health":
            return httpx.Response(200, json={"status": "ok"})
        if request.url.path == "/api/repositories/repo-1":
            return httpx.Response(
                200,
                json={"id": "repo-1", "status": "stale", "current_collection_id": "collection-1"},
            )
        raise AssertionError(request.url)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(OnlineEvaluationPreflightError, match="ready"):
        run_online_evaluation(
            client,
            "http://service",
            "repo-1",
            "collection-1",
            CASES,
            tmp_path / "artifacts",
            tmp_path / "summary.md",
            poll_seconds=0,
            timeout_seconds=1,
        )

    assert not (tmp_path / "summary.md").exists()


def test_runner_keeps_raw_answers_out_of_summary(tmp_path: Path) -> None:
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.url.path == "/api/health":
            return httpx.Response(200, json={"status": "ok"})
        if request.url.path == "/api/repositories/repo-1":
            return httpx.Response(
                200,
                json={"id": "repo-1", "status": "ready", "current_collection_id": "collection-1"},
            )
        if request.url.path == "/api/meta":
            return httpx.Response(200, json={"model": "test-model"})
        if request.method == "POST" and request.url.path == "/api/sessions":
            return httpx.Response(201, json={"id": "session-1"})
        if request.method == "POST" and request.url.path == "/api/sessions/session-1/messages":
            return httpx.Response(202, json={"id": "run-1"})
        if request.method == "GET" and request.url.path == "/api/sessions/session-1":
            return httpx.Response(
                200,
                json={
                    "status": "completed",
                    "messages": [
                        {
                            "role": "assistant",
                            "content": (
                                "必须使用 path:start-end。\n"
                                "backend/src/repo_evidence/workflows/evidence.py:29-33"
                            ),
                        }
                    ],
                },
            )
        if request.method == "DELETE" and request.url.path == "/api/sessions/session-1":
            return httpx.Response(200, json={"deleted_ids": ["session-1"]})
        raise AssertionError(request.url)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    report = run_online_evaluation(
        client,
        "http://service",
        "repo-1",
        "collection-1",
        CASES,
        tmp_path / "artifacts",
        tmp_path / "summary.md",
        poll_seconds=0,
        timeout_seconds=1,
    )

    detail = json.loads(report.detail_path.read_text(encoding="utf-8"))
    summary = report.summary_path.read_text(encoding="utf-8")
    assert detail["cases"][0]["answer"].startswith("必须使用")
    assert "必须使用" not in summary
    assert report.total == report.completed == report.strict_passed == 1
    assert ("DELETE", "/api/sessions/session-1") in calls


def test_select_target_uses_the_only_ready_repository() -> None:
    repository_id, collection_id = select_target(
        [
            {"id": "old", "status": "stale", "current_collection_id": "old-collection"},
            {"id": "ready", "status": "ready", "current_collection_id": "current-collection"},
        ],
        repository_id=None,
        collection_id=None,
    )

    assert (repository_id, collection_id) == ("ready", "current-collection")


def test_list_repositories_accepts_the_api_list_response() -> None:
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json=[{"id": "ready", "status": "ready"}])
        )
    )

    assert list_repositories(client, "http://service") == [{"id": "ready", "status": "ready"}]
