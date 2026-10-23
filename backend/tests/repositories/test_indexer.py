from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest

from repo_evidence.core.event_log.contracts import EventSink
from repo_evidence.core.knowledge.contracts import Chunk, Document
from repo_evidence.core.llm.embedding import EmbeddingClient
from repo_evidence.core.llm.embedding.settings import EmbeddingSettings
from repo_evidence.repositories.indexer import CollectionBuilder, RepositoryIndexer
from repo_evidence.repositories.models import RepositoryRoot
from repo_evidence.repositories.store import RepositoryStore


class RecordingVectorStore:
    def __init__(self) -> None:
        self.collections: dict[str, dict[str, object]] = {}
        self.documents: dict[str, list[tuple[Document, list[Chunk]]]] = {}
        self.reuse_calls: list[tuple[str, str, dict[str, str]]] = []

    def create_collection(
        self, collection_id, dataset_release_id, manifest_sha256, build_config, dimensions
    ):
        self.collections[collection_id] = {
            "collection_id": collection_id,
            "dataset_release_id": dataset_release_id,
            "dataset_manifest_sha256": manifest_sha256,
            "build_config": build_config,
            "dimensions": dimensions,
            "status": "building",
            "created_at": datetime.now(UTC),
            "ready_at": None,
        }

    def save_document(self, collection_id, document, chunks) -> None:
        self.documents.setdefault(collection_id, []).append((document, chunks))

    def get_collection(self, collection_id):
        return self.collections[collection_id]

    def missing_embeddings(self, collection_id, limit):
        return []

    def mark_ready(self, collection_id, expected_documents) -> None:
        self.collections[collection_id]["status"] = "ready"

    def copy_unchanged_documents(
        self, source_collection_id, target_collection_id, file_sha256_by_path
    ) -> tuple[str, ...]:
        self.reuse_calls.append((source_collection_id, target_collection_id, file_sha256_by_path))
        return tuple(file_sha256_by_path)


class FailingVectorStore(RecordingVectorStore):
    def create_collection(
        self, collection_id, dataset_release_id, manifest_sha256, build_config, dimensions
    ):
        raise RuntimeError("database unavailable")


class NoopTracer:
    async def emit(self, *_args, **_kwargs) -> None:
        return None


class EmptyEmbeddingClient:
    def __init__(self, settings: EmbeddingSettings) -> None:
        self.identity = settings.identity()
        self.batch_size = settings.batch_size


def embedding() -> EmbeddingSettings:
    return EmbeddingSettings(
        name="test-embedding",
        base_url="http://localhost:11434/v1",
        dimensions=3,
        api_key_env="TEST_KEY",
        revision="test-revision",
    )


@pytest.mark.asyncio
async def test_indexer_publishes_a_ready_snapshot_only_after_storing_every_document(
    tmp_path: Path,
) -> None:
    root = tmp_path / "projects"
    repository_path = root / "sample-api"
    (repository_path / "src").mkdir(parents=True)
    (repository_path / "src/auth.py").write_text(
        "def login():\n    return True\n", encoding="utf-8"
    )
    registry = RepositoryStore(tmp_path / "registry.sqlite3", [RepositoryRoot("projects", root)])
    repository = registry.register("projects", "sample-api")
    vectors = RecordingVectorStore()
    indexer = RepositoryIndexer(
        registry,
        cast(CollectionBuilder, cast(object, vectors)),
        cast(EmbeddingClient, cast(object, EmptyEmbeddingClient(embedding()))),
        cast(EventSink, cast(object, NoopTracer())),
    )

    snapshot = await indexer.build(repository.id)

    assert snapshot.status.value == "ready"
    assert registry.get_repository(repository.id).current_collection_id == snapshot.collection_id
    assert len(vectors.documents[snapshot.collection_id]) == 1
    assert vectors.collections[snapshot.collection_id]["status"] == "ready"


@pytest.mark.asyncio
async def test_detect_changes_marks_the_active_snapshot_stale_without_replacing_it(
    tmp_path: Path,
) -> None:
    root = tmp_path / "projects"
    repository_path = root / "sample-api"
    repository_path.mkdir(parents=True)
    source = repository_path / "auth.py"
    removed = repository_path / "README.md"
    source.write_text("def login():\n    return True\n", encoding="utf-8")
    removed.write_text("# Sample API\n", encoding="utf-8")
    registry = RepositoryStore(tmp_path / "registry.sqlite3", [RepositoryRoot("projects", root)])
    repository = registry.register("projects", "sample-api")
    indexer = RepositoryIndexer(
        registry,
        cast(CollectionBuilder, cast(object, RecordingVectorStore())),
        cast(EmbeddingClient, cast(object, EmptyEmbeddingClient(embedding()))),
        cast(EventSink, cast(object, NoopTracer())),
    )
    snapshot = await indexer.build(repository.id)

    source.write_text("def login():\n    return False\n", encoding="utf-8")
    (repository_path / "settings.toml").write_text("debug = false\n", encoding="utf-8")
    removed.unlink()

    summary = await indexer.detect_changes(repository.id)

    assert summary.status == "stale"
    assert summary.added == ("settings.toml",)
    assert summary.changed == ("auth.py",)
    assert summary.deleted == ("README.md",)
    current = registry.get_repository(repository.id)
    assert current.status == "stale"
    assert current.current_collection_id == snapshot.collection_id


@pytest.mark.asyncio
async def test_refresh_reuses_unchanged_files_without_resaving_them(tmp_path: Path) -> None:
    root = tmp_path / "projects"
    repository_path = root / "sample-api"
    repository_path.mkdir(parents=True)
    auth = repository_path / "auth.py"
    config = repository_path / "settings.toml"
    auth.write_text("def login():\n    return True\n", encoding="utf-8")
    config.write_text("debug = false\n", encoding="utf-8")
    registry = RepositoryStore(tmp_path / "registry.sqlite3", [RepositoryRoot("projects", root)])
    repository = registry.register("projects", "sample-api")
    vectors = RecordingVectorStore()
    indexer = RepositoryIndexer(
        registry,
        cast(CollectionBuilder, cast(object, vectors)),
        cast(EmbeddingClient, cast(object, EmptyEmbeddingClient(embedding()))),
        cast(EventSink, cast(object, NoopTracer())),
    )
    first = await indexer.build(repository.id)

    config.write_text("debug = true\n", encoding="utf-8")
    second = await indexer.build(repository.id)

    assert second.collection_id != first.collection_id
    assert len(vectors.reuse_calls) == 1
    source, target, reused = vectors.reuse_calls[0]
    assert (source, target) == (first.collection_id, second.collection_id)
    assert set(reused) == {"auth.py"}
    saved_paths = [
        document.source_path for document, _chunks in vectors.documents[second.collection_id]
    ]
    assert saved_paths == ["settings.toml"]


@pytest.mark.asyncio
async def test_failed_first_index_marks_the_repository_failed(tmp_path: Path) -> None:
    root = tmp_path / "projects"
    repository_path = root / "sample-api"
    repository_path.mkdir(parents=True)
    (repository_path / "auth.py").write_text("def login():\n    return True\n", encoding="utf-8")
    registry = RepositoryStore(tmp_path / "registry.sqlite3", [RepositoryRoot("projects", root)])
    repository = registry.register("projects", "sample-api")
    indexer = RepositoryIndexer(
        registry,
        cast(CollectionBuilder, cast(object, FailingVectorStore())),
        cast(EmbeddingClient, cast(object, EmptyEmbeddingClient(embedding()))),
        cast(EventSink, cast(object, NoopTracer())),
    )

    with pytest.raises(RuntimeError, match="database unavailable"):
        await indexer.build(repository.id)

    assert registry.get_repository(repository.id).status == "failed"
    assert registry.get_repository(repository.id).current_collection_id is None
