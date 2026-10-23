"""Assemble the single RepoEvidence workflow from local-only product configuration."""

from __future__ import annotations

import os
from collections.abc import Callable

from fastapi import FastAPI

from repo_evidence.core.config import Settings
from repo_evidence.core.event_log.types import Event
from repo_evidence.core.execution.contracts import Workflow
from repo_evidence.core.http import create_app
from repo_evidence.core.json import JsonObject
from repo_evidence.core.llm.chat.openai_chat import OpenAiChatModel
from repo_evidence.core.llm.embedding import EmbeddingClient
from repo_evidence.core.llm.embedding.ollama import retrieval_input
from repo_evidence.core.llm.embedding.openai_embedding import OpenAiEmbeddingModel
from repo_evidence.repositories.indexer import RepositoryIndexer
from repo_evidence.repositories.models import RepositoryRoot
from repo_evidence.repositories.store import RepositoryStore
from repo_evidence.repositories.tools import RepositoryTools
from repo_evidence.workflows.evidence import EvidenceWorkflow


class _NoopEventSink:
    """Index builds are outside a chat session, but embedding calls still need an event sink."""

    async def emit(
        self,
        kind: str,
        payload: JsonObject,
        call_id: str | None = None,
        parent_call_id: str | None = None,
    ) -> Event:
        return {
            "schema_version": 1,
            "event_id": "index-build",
            "session_id": "index-build",
            "seq": 0,
            "run_id": None,
            "call_id": call_id,
            "parent_call_id": parent_call_id,
            "timestamp": "",
            "type": kind,
            "payload": payload,
        }


def create_repository_store(settings: Settings) -> RepositoryStore:
    """Open the product-local registry for only administrator-approved repository roots."""
    if not settings.repository_roots:
        raise ValueError("RepoEvidence requires at least one allowed repository root")
    return RepositoryStore(
        settings.data_dir / "repositories.sqlite3",
        [RepositoryRoot(root.id, root.path) for root in settings.repository_roots],
    )


def create_workflow(settings: Settings, registry: RepositoryStore | None = None) -> Workflow:
    """Validate model/vector access and return the sole production Evidence Agent."""
    if settings.model.provider != "openai-compatible":
        raise ValueError("RepoEvidence requires an openai-compatible chat model")
    if settings.embedding is None:
        raise ValueError("RepoEvidence requires embedding configuration")
    for key in (
        settings.model.api_key_env,
        settings.embedding.api_key_env,
        settings.knowledge_url_env,
    ):
        if not os.environ.get(key):
            raise ValueError(f"Missing environment variable: {key}")
    return EvidenceWorkflow(
        OpenAiChatModel(settings.model),
        RepositoryTools(registry or create_repository_store(settings)),
        retrieval_input,
    )


def create_repository_indexer(
    settings: Settings, registry: RepositoryStore
) -> tuple[RepositoryIndexer, Callable[[], None]]:
    """Open the builder-only vector connection for explicitly requested immutable index builds."""
    if settings.embedding is None:
        raise ValueError("RepoEvidence requires embedding configuration")
    builder_url = os.environ.get(settings.knowledge_builder_url_env)
    if not builder_url:
        raise ValueError(f"Missing environment variable: {settings.knowledge_builder_url_env}")
    from repo_evidence.core.knowledge import PostgresVectorStore

    vectors = PostgresVectorStore(builder_url)
    sink = _NoopEventSink()
    return (
        RepositoryIndexer(
            registry,
            vectors,
            EmbeddingClient(OpenAiEmbeddingModel(settings.embedding), sink),
            sink,
        ),
        vectors.close,
    )


def create_application(settings: Settings) -> FastAPI:
    """Build the production FastAPI app with one shared registry for chat and indexing."""
    registry = create_repository_store(settings)
    workflow = create_workflow(settings, registry)
    indexer, close_indexer = create_repository_indexer(settings, registry)
    return create_app(
        settings,
        workflow,
        repository_store=registry,
        repository_indexer=indexer,
        repository_cleanup=close_indexer,
    )
