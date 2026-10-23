"""Convert a safe repository scan into an immutable pgvector collection build plan."""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from typing import Protocol

from repo_evidence.core.event_log.contracts import EventSink
from repo_evidence.core.json import JsonObject
from repo_evidence.core.knowledge.client import KnowledgeClient
from repo_evidence.core.knowledge.contracts import Chunk, Collection, Document, VectorStore
from repo_evidence.core.llm.embedding import EmbeddingClient
from repo_evidence.core.llm.embedding.settings import EmbeddingSettings

from .chunking import chunk_file
from .models import RepositoryRecord
from .progress import IndexProgressStore
from .scanner import RepositoryScanner, ScanResult
from .store import RepositoryStore

INDEX_STRATEGY_VERSION = "repository-line-evidence-v1"


@dataclass(frozen=True)
class RepositoryCorpus:
    """All immutable inputs required to create and verify a repository collection."""

    collection_id: str
    manifest_sha256: str
    build_config: JsonObject
    documents: tuple[tuple[Document, list[Chunk]], ...]
    expected_documents: dict[str, int]


@dataclass(frozen=True)
class ChangeSummary:
    """A safe diff between the active immutable manifest and the current work tree."""

    status: str
    added: tuple[str, ...]
    changed: tuple[str, ...]
    deleted: tuple[str, ...]


class CollectionBuilder(VectorStore, Protocol):
    """The limited mutable collection operations needed during a repository build."""

    def create_collection(
        self,
        collection_id: str,
        dataset_release_id: str,
        dataset_manifest_sha256: str,
        build_config: JsonObject,
        dimensions: int,
    ) -> Collection: ...

    def save_document(
        self, collection_id: str, document: Document, chunks: list[Chunk]
    ) -> None: ...

    def copy_unchanged_documents(
        self,
        source_collection_id: str,
        target_collection_id: str,
        file_sha256_by_path: dict[str, str],
    ) -> tuple[str, ...]: ...

    def mark_ready(self, collection_id: str, expected_documents: dict[str, int]) -> None: ...


class RepositoryIndexer:
    """Publish a complete immutable repository collection or leave the prior snapshot active."""

    def __init__(
        self,
        registry: RepositoryStore,
        collections: CollectionBuilder,
        embeddings: EmbeddingClient,
        tracer: EventSink,
        scanner: RepositoryScanner | None = None,
        progress: IndexProgressStore | None = None,
    ) -> None:
        self.registry = registry
        self.collections = collections
        self.embeddings = embeddings
        self.tracer = tracer
        self.scanner = scanner or RepositoryScanner()
        self.progress = progress or IndexProgressStore()

    async def build(self, repository_id: str):
        """Scan, persist, embed, revalidate and atomically activate one new collection version."""
        repository = self.registry.get_repository(repository_id)
        self.registry.begin_index(repository.id)
        snapshot = None
        try:
            root = self.registry.repository_path(repository)
            self.progress.update(repository.id, "scanning")
            scan = await asyncio.to_thread(self.scanner.scan, root)
            self.progress.update(
                repository.id,
                "preparing",
                scanned_files=len(scan.files),
                ignored_files=len(scan.excluded),
            )
            parent_hashes: dict[str, str] = {}
            if repository.current_collection_id:
                current = self.registry.get_snapshot_for_collection(
                    repository.id, repository.current_collection_id
                )
                if current.manifest_sha256 == scan.manifest_sha256:
                    self.registry.set_current_snapshot_stale(repository.id, False)
                    return current
                parent_hashes = self.registry.snapshot_file_hashes(current.id)
            embedding = self.embeddings
            settings = _embedding_settings(embedding)
            corpus = prepare_repository_corpus(repository, scan, settings)
            self.progress.update(
                repository.id,
                "reusing",
                new_chunks=sum(len(chunks) for _, chunks in corpus.documents),
            )
            snapshot = self.registry.create_snapshot(
                repository.id,
                corpus.collection_id,
                corpus.manifest_sha256,
                {item.path: item.sha256 for item in scan.files},
            )
            await asyncio.to_thread(
                self.collections.create_collection,
                corpus.collection_id,
                f"repository:{repository.id}",
                corpus.manifest_sha256,
                corpus.build_config,
                settings.dimensions,
            )
            current_hashes = {item.path: item.sha256 for item in scan.files}
            reusable = {
                path: digest
                for path, digest in current_hashes.items()
                if parent_hashes.get(path) == digest
            }
            reused_paths: set[str] = set()
            if repository.current_collection_id and reusable:
                reused_paths = set(
                    await asyncio.to_thread(
                        self.collections.copy_unchanged_documents,
                        repository.current_collection_id,
                        corpus.collection_id,
                        reusable,
                    )
                )
            self.progress.update(repository.id, "embedding", reused_files=len(reused_paths))
            for document, chunks in corpus.documents:
                if document.source_path in reused_paths:
                    continue
                await asyncio.to_thread(
                    self.collections.save_document, corpus.collection_id, document, chunks
                )
            await KnowledgeClient(self.collections, self.embeddings, self.tracer).embed_pending(
                corpus.collection_id
            )
            self.progress.update(repository.id, "finalizing")
            final_scan = await asyncio.to_thread(self.scanner.scan, root)
            if final_scan.manifest_sha256 != corpus.manifest_sha256:
                raise RuntimeError("Repository changed while its evidence index was being built")
            await asyncio.to_thread(
                self.collections.mark_ready, corpus.collection_id, corpus.expected_documents
            )
            self.registry.activate_snapshot(repository.id, snapshot.id)
            self.progress.update(repository.id, "ready")
            return self.registry.get_snapshot(snapshot.id)
        except Exception as error:
            if snapshot is not None:
                self.registry.fail_snapshot(snapshot.id)
            self.registry.fail_index(repository.id)
            self.progress.update(repository.id, "failed", error=str(error)[:240])
            raise

    async def detect_changes(self, repository_id: str) -> ChangeSummary:
        """Mark a collection stale when its source manifest no longer matches the work tree."""
        repository = self.registry.get_repository(repository_id)
        if repository.current_collection_id is None:
            raise ValueError("Repository has no ready evidence index")
        root = self.registry.repository_path(repository)
        scan = await asyncio.to_thread(self.scanner.scan, root)
        snapshot = self.registry.get_snapshot_for_collection(
            repository.id, repository.current_collection_id
        )
        before = self.registry.snapshot_file_hashes(snapshot.id)
        after = {item.path: item.sha256 for item in scan.files}
        added = tuple(sorted(after.keys() - before.keys()))
        deleted = tuple(sorted(before.keys() - after.keys()))
        changed = tuple(
            sorted(path for path in before.keys() & after.keys() if before[path] != after[path])
        )
        stale = bool(added or changed or deleted)
        self.registry.set_current_snapshot_stale(repository.id, stale)
        return ChangeSummary(
            status="stale" if stale else "unchanged",
            added=added,
            changed=changed,
            deleted=deleted,
        )


def prepare_repository_corpus(
    repository: RepositoryRecord,
    scan: ScanResult,
    embedding: EmbeddingSettings,
    *,
    max_lines: int = 120,
) -> RepositoryCorpus:
    """Freeze a scan into documents and chunks without touching a database or embedding service."""
    collection_id = _collection_id(repository.id, scan.manifest_sha256, embedding)
    build_config: JsonObject = {
        "kind": "repository",
        "repository_id": repository.id,
        "root_id": repository.root_id,
        "relative_path": repository.relative_path,
        "manifest_sha256": scan.manifest_sha256,
        "index_strategy": INDEX_STRATEGY_VERSION,
        "max_lines": max_lines,
        "embedding": embedding.identity(),
    }
    documents: list[tuple[Document, list[Chunk]]] = []
    expected_documents: dict[str, int] = {}
    for item in scan.files:
        doc_id = "file-" + hashlib.sha256(item.path.encode("utf-8")).hexdigest()[:24]
        document = Document(
            doc_id=doc_id,
            title=item.path,
            source_path=item.path,
            content=item.content,
            metadata={
                "repository_id": repository.id,
                "content_type": item.content_type,
                "language": item.language,
                "source_sha256": item.sha256,
            },
        )
        chunks = chunk_file(item, max_lines=max_lines)
        if not chunks:
            continue
        documents.append((document, chunks))
        expected_documents[doc_id] = len(chunks)
    if not documents:
        raise ValueError("Repository scan contains no indexable text files")
    return RepositoryCorpus(
        collection_id=collection_id,
        manifest_sha256=scan.manifest_sha256,
        build_config=build_config,
        documents=tuple(documents),
        expected_documents=expected_documents,
    )


def _collection_id(repository_id: str, manifest_sha256: str, embedding: EmbeddingSettings) -> str:
    identity = hashlib.sha256(
        f"{INDEX_STRATEGY_VERSION}:{embedding.identity()}".encode()
    ).hexdigest()[:10]
    return f"repo-{repository_id}-{manifest_sha256[:12]}-{identity}"


def _embedding_settings(client: EmbeddingClient) -> EmbeddingSettings:
    """Accept the real embedding client while keeping the indexer testable with a small adapter."""
    identity = getattr(client, "identity", None)
    if not isinstance(identity, dict):
        raise TypeError("Repository indexer requires an embedding client with an identity")
    return EmbeddingSettings(
        name=str(identity["model"]),
        base_url=str(identity["base_url"]),
        dimensions=int(identity["dimensions"]),
        api_key_env="REPO_EVIDENCE_EMBEDDING_API_KEY",
        provider=str(identity["provider"]),
        revision=str(identity["revision"]),
        batch_size=int(getattr(client, "batch_size", 32)),
        request_options=identity["request_options"],
    )
