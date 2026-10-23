from __future__ import annotations

from pathlib import Path

from repo_evidence.core.json import require_object
from repo_evidence.core.llm.embedding.settings import EmbeddingSettings
from repo_evidence.repositories.indexer import prepare_repository_corpus
from repo_evidence.repositories.models import RepositoryRecord
from repo_evidence.repositories.scanner import RepositoryScanner


def repository(root: Path) -> RepositoryRecord:
    return RepositoryRecord(
        id="repo-123",
        root_id="projects",
        relative_path="sample-api",
        display_name="sample-api",
        status="unindexed",
        current_collection_id=None,
        created_at=__import__("datetime").datetime.now(__import__("datetime").UTC),
        updated_at=__import__("datetime").datetime.now(__import__("datetime").UTC),
    )


def embedding() -> EmbeddingSettings:
    return EmbeddingSettings(
        name="test-embedding",
        base_url="http://localhost:11434/v1",
        dimensions=3,
        api_key_env="TEST_KEY",
        revision="test-revision",
    )


def test_prepared_corpus_binds_chunks_to_one_repository_snapshot(tmp_path: Path) -> None:
    root = tmp_path / "sample-api"
    (root / "src").mkdir(parents=True)
    (root / "src/auth.py").write_text("def login():\n    return True\n", encoding="utf-8")
    scan = RepositoryScanner().scan(root)

    corpus = prepare_repository_corpus(repository(root), scan, embedding())

    assert corpus.collection_id.startswith("repo-repo-123-")
    assert corpus.manifest_sha256 == scan.manifest_sha256
    assert require_object(corpus.build_config["embedding"])["model"] == "test-embedding"
    document, chunks = corpus.documents[0]
    assert document.source_path == "src/auth.py"
    assert document.metadata["repository_id"] == "repo-123"
    assert chunks[0].source_locator["start_line"] == 1
    assert corpus.expected_documents == {document.doc_id: len(chunks)}


def test_changed_source_content_creates_a_new_collection_id(tmp_path: Path) -> None:
    root = tmp_path / "sample-api"
    root.mkdir()
    source = root / "service.py"
    source.write_text("def status():\n    return 'old'\n", encoding="utf-8")
    first = prepare_repository_corpus(repository(root), RepositoryScanner().scan(root), embedding())
    source.write_text("def status():\n    return 'new'\n", encoding="utf-8")

    second = prepare_repository_corpus(
        repository(root), RepositoryScanner().scan(root), embedding()
    )

    assert second.collection_id != first.collection_id
