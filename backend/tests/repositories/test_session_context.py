from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest

from repo_evidence.core.config import Settings
from repo_evidence.core.event_log import EventStore
from repo_evidence.core.execution.contracts import TurnInput, TurnResult, Workflow
from repo_evidence.core.execution.manager import SessionManager
from repo_evidence.core.execution.resources import RuntimeResources
from repo_evidence.core.execution.runtime import AgentRuntime
from repo_evidence.core.execution.stage import StageCard


class ContextResources:
    def __init__(self) -> None:
        self.collection_ids: list[str | None] = []

    async def snapshot(self, previous=None, *, collection_id=None):
        self.collection_ids.append(collection_id)
        return {"knowledge": {"collection_id": collection_id}}

    def document_retriever(self, _tracer):
        return None


class CollectionEcho:
    id = "collection-echo"
    version = "1"
    card = StageCard(label="Collection echo", description="test-only workflow")

    async def run(self, turn: TurnInput, runtime: AgentRuntime) -> TurnResult:
        _ = turn
        return TurnResult(f"collection={runtime.collection_id}", {})


@pytest.mark.asyncio
async def test_session_runs_with_the_collection_bound_when_it_was_created(tmp_path: Path) -> None:
    settings = Settings(tmp_path / "var")
    resources = ContextResources()
    manager = SessionManager(
        EventStore(tmp_path / "sessions.sqlite"),
        cast(Workflow, CollectionEcho()),
        settings,
        resources=cast(RuntimeResources, cast(object, resources)),
    )

    session = await manager.create_session(repository_id="repo-1", collection_id="collection-v2")
    await manager.submit_message(session["id"], "request-1", "where is authentication?")
    await manager.tasks[session["id"]]
    finished = await manager.get_session(session["id"])

    assert finished["repository_id"] == "repo-1"
    assert finished["collection_id"] == "collection-v2"
    assert resources.collection_ids == ["collection-v2"]
    assert finished["messages"][-1]["content"] == "collection=collection-v2"
