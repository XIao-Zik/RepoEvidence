from __future__ import annotations

import json
from pathlib import Path

import pytest

from repo_evidence.core.config import Settings
from repo_evidence.core.event_log import EventStore
from repo_evidence.core.execution.manager import SessionManager
from repo_evidence.core.execution.resources import RuntimeResources
from repo_evidence.core.json import require_list, require_object
from repo_evidence.core.llm.embedding.ollama import retrieval_input
from repo_evidence.repositories.models import RepositoryRoot
from repo_evidence.repositories.scanner import RepositoryScanner
from repo_evidence.repositories.store import RepositoryStore
from repo_evidence.repositories.tools import RepositoryTools
from repo_evidence.workflows.evidence import EvidenceWorkflow
from tests.workflows.fixtures import Model, Search, completion


def file_call(identifier: str) -> dict[str, object]:
    return {
        "id": identifier,
        "type": "function",
        "function": {
            "name": "read_file",
            "arguments": json.dumps({"path": "src/auth.py", "start_line": 1, "end_line": 99}),
        },
    }


def search_call(identifier: str, name: str, query: str) -> dict[str, object]:
    return {
        "id": identifier,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps({"query": query})},
    }


@pytest.mark.asyncio
async def test_evidence_agent_reads_a_bounded_repository_file_before_answering(
    tmp_path: Path,
) -> None:
    root = tmp_path / "projects"
    repository_path = root / "sample-api"
    (repository_path / "src").mkdir(parents=True)
    (repository_path / "src/auth.py").write_text(
        "def authenticate(token: str) -> bool:\n    return bool(token)\n", encoding="utf-8"
    )
    registry = RepositoryStore(tmp_path / "registry.sqlite", [RepositoryRoot("projects", root)])
    repository = registry.register("projects", "sample-api")
    snapshot = registry.create_snapshot(
        repository.id, "fixture", RepositoryScanner().scan(repository_path).manifest_sha256
    )
    registry.activate_snapshot(repository.id, snapshot.id)
    model, search = Model(), Search()
    model.responses = [
        completion(calls=[file_call("read-auth")]),
        completion("结论：认证函数在此处。\n证据：src/auth.py:1-2"),
    ]
    settings = Settings(tmp_path / "var")
    resources = RuntimeResources(settings, retriever=search.for_run)
    manager = SessionManager(
        EventStore(tmp_path / "sessions.sqlite"),
        EvidenceWorkflow(model, RepositoryTools(registry), retrieval_input),
        settings,
        resources,
    )

    session = await manager.create_session(repository_id=repository.id, collection_id="fixture")
    await manager.submit_message(session["id"], "one", "认证实现在哪里？")
    await manager.tasks[session["id"]]

    view = await manager.get_session(session["id"])
    assert view["status"] == "completed"
    assert "src/auth.py:1-2" in view["messages"][-1]["content"]
    offered = [
        require_object(item)["function"] for item in require_list(model.requests[0]["tools"])
    ]
    assert {require_object(item)["name"] for item in offered} == {
        "search_repository",
        "search_docs",
        "search_logs",
        "read_file",
        "list_directory",
        "preview_patch",
    }
    tool_result = [
        require_object(message)
        for message in require_list(model.requests[1]["messages"])
        if require_object(message)["role"] == "tool"
    ][0]
    assert "authenticate" in str(tool_result["content"])


@pytest.mark.asyncio
async def test_evidence_agent_refuses_a_stale_repository_before_calling_the_model(
    tmp_path: Path,
) -> None:
    root = tmp_path / "projects"
    repository_path = root / "sample-api"
    repository_path.mkdir(parents=True)
    source = repository_path / "auth.py"
    source.write_text("def authenticate():\n    return True\n", encoding="utf-8")
    registry = RepositoryStore(tmp_path / "registry.sqlite", [RepositoryRoot("projects", root)])
    repository = registry.register("projects", "sample-api")
    manifest = RepositoryScanner().scan(repository_path).manifest_sha256
    snapshot = registry.create_snapshot(repository.id, "fixture", manifest)
    registry.activate_snapshot(repository.id, snapshot.id)
    source.write_text("def authenticate():\n    return False\n", encoding="utf-8")
    model, search = Model(), Search()
    settings = Settings(tmp_path / "var")
    manager = SessionManager(
        EventStore(tmp_path / "sessions.sqlite"),
        EvidenceWorkflow(model, RepositoryTools(registry), retrieval_input),
        settings,
        RuntimeResources(settings, retriever=search.for_run),
    )

    session = await manager.create_session(repository_id=repository.id, collection_id="fixture")
    await manager.submit_message(session["id"], "one", "认证实现在哪里？")
    await manager.tasks[session["id"]]

    view = await manager.get_session(session["id"])
    assert view["status"] == "completed"
    assert "刷新索引" in view["messages"][-1]["content"]
    assert not model.requests
    assert registry.get_repository(repository.id).status == "stale"


@pytest.mark.asyncio
async def test_evidence_agent_refuses_a_session_bound_to_a_superseded_snapshot(
    tmp_path: Path,
) -> None:
    root = tmp_path / "projects"
    repository_path = root / "sample-api"
    repository_path.mkdir(parents=True)
    (repository_path / "auth.py").write_text(
        "def authenticate():\n    return True\n", encoding="utf-8"
    )
    registry = RepositoryStore(tmp_path / "registry.sqlite", [RepositoryRoot("projects", root)])
    repository = registry.register("projects", "sample-api")
    manifest = RepositoryScanner().scan(repository_path).manifest_sha256
    first = registry.create_snapshot(repository.id, "fixture-v1", manifest)
    registry.activate_snapshot(repository.id, first.id)
    model, search = Model(), Search()
    settings = Settings(tmp_path / "var")
    manager = SessionManager(
        EventStore(tmp_path / "sessions.sqlite"),
        EvidenceWorkflow(model, RepositoryTools(registry), retrieval_input),
        settings,
        RuntimeResources(settings, retriever=search.for_run),
    )
    session = await manager.create_session(repository_id=repository.id, collection_id="fixture-v1")
    refreshed = registry.create_snapshot(repository.id, "fixture-v2", manifest)
    registry.activate_snapshot(repository.id, refreshed.id)

    await manager.submit_message(session["id"], "one", "认证实现在哪里？")
    await manager.tasks[session["id"]]

    view = await manager.get_session(session["id"])
    assert "创建新会话" in view["messages"][-1]["content"]
    assert not model.requests


@pytest.mark.asyncio
async def test_evidence_agent_filters_document_and_log_searches_by_content_type(
    tmp_path: Path,
) -> None:
    root = tmp_path / "projects"
    repository_path = root / "sample-api"
    repository_path.mkdir(parents=True)
    (repository_path / "README.md").write_text("# Configure\n", encoding="utf-8")
    registry = RepositoryStore(tmp_path / "registry.sqlite", [RepositoryRoot("projects", root)])
    repository = registry.register("projects", "sample-api")
    snapshot = registry.create_snapshot(
        repository.id, "fixture", RepositoryScanner().scan(repository_path).manifest_sha256
    )
    registry.activate_snapshot(repository.id, snapshot.id)
    model, search = Model(), Search()
    model.responses = [
        completion(
            calls=[
                search_call("search-docs", "search_docs", "配置文件"),
                search_call("search-logs", "search_logs", "exception"),
            ]
        ),
        completion("无法从静态日志确认运行时错误。"),
    ]
    settings = Settings(tmp_path / "var")
    manager = SessionManager(
        EventStore(tmp_path / "sessions.sqlite"),
        EvidenceWorkflow(model, RepositoryTools(registry), retrieval_input),
        settings,
        RuntimeResources(settings, retriever=search.for_run),
    )

    session = await manager.create_session(repository_id=repository.id, collection_id="fixture")
    await manager.submit_message(session["id"], "one", "配置和异常日志在哪里？")
    await manager.tasks[session["id"]]

    assert [(limit, filters) for _query, limit, filters in search.queries] == [
        (8, {"content_type": "document"}),
        (8, {"content_type": "log"}),
    ]


@pytest.mark.asyncio
async def test_evidence_agent_retries_an_answer_that_cites_an_unread_line_range(
    tmp_path: Path,
) -> None:
    root = tmp_path / "projects"
    repository_path = root / "sample-api"
    (repository_path / "src").mkdir(parents=True)
    (repository_path / "src/auth.py").write_text(
        "def authenticate(token: str) -> bool:\n    return bool(token)\n", encoding="utf-8"
    )
    registry = RepositoryStore(tmp_path / "registry.sqlite", [RepositoryRoot("projects", root)])
    repository = registry.register("projects", "sample-api")
    snapshot = registry.create_snapshot(
        repository.id, "fixture", RepositoryScanner().scan(repository_path).manifest_sha256
    )
    registry.activate_snapshot(repository.id, snapshot.id)
    model, search = Model(), Search()
    model.responses = [
        completion(calls=[file_call("read-auth")]),
        completion("证据：src/auth.py:9-12"),
        completion("证据：src/auth.py:1-2"),
    ]
    settings = Settings(tmp_path / "var")
    manager = SessionManager(
        EventStore(tmp_path / "sessions.sqlite"),
        EvidenceWorkflow(model, RepositoryTools(registry), retrieval_input),
        settings,
        RuntimeResources(settings, retriever=search.for_run),
    )

    session = await manager.create_session(repository_id=repository.id, collection_id="fixture")
    await manager.submit_message(session["id"], "one", "认证实现在哪里？")
    await manager.tasks[session["id"]]

    view = await manager.get_session(session["id"])
    assert view["messages"][-1]["content"] == "证据：src/auth.py:1-2"
    assert len(model.requests) == 3
    assert any(event["type"] == "answer.disputed" for event in manager.store.read(session["id"]))
    final_event = next(
        event for event in manager.store.read(session["id"]) if event["type"] == "message.assistant"
    )
    evidence = require_list(final_event["payload"]["evidence"])
    assert require_object(require_object(evidence[0])["source"])["locator"] == {
        "start_line": 1,
        "end_line": 2,
    }
