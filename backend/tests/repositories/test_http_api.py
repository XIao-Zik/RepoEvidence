from __future__ import annotations

from pathlib import Path
from threading import Event

from fastapi.testclient import TestClient

from repo_evidence.core.config import Settings
from repo_evidence.core.http import create_app
from repo_evidence.core.knowledge.contracts import Passage, PassageLookup, PassageSource
from repo_evidence.repositories.models import RepositoryRoot
from repo_evidence.repositories.scanner import RepositoryScanner
from repo_evidence.repositories.store import RepositoryStore
from tests.core.fixtures import FixtureWorkflow


class RecordingIndexer:
    def __init__(self) -> None:
        self.started = Event()
        self.repository_ids: list[str] = []

    async def build(self, repository_id: str) -> None:
        self.repository_ids.append(repository_id)
        self.started.set()


class RecordingCollections:
    def __init__(self) -> None:
        self.deleted: list[str] = []

    def delete_collection(self, collection_id: str) -> None:
        self.deleted.append(collection_id)


class PruningIndexer(RecordingIndexer):
    def __init__(self) -> None:
        super().__init__()
        self.collections = RecordingCollections()


class FrozenEvidence:
    def read_passage(self, collection_id: str, doc_id: str, chunk_no: int) -> PassageLookup:
        assert (collection_id, doc_id, chunk_no) == ("fixture", "file-auth", 1)
        passage = Passage(
            id="doc:file-auth#c001",
            title="src/auth.py",
            text="def authenticate():\n    return True\n",
            score=1.0,
            source=PassageSource(
                collection_id="fixture",
                doc_id="file-auth",
                chunk_no=1,
                path="src/auth.py",
                sha256="frozen-sha",
                locator={"start_line": 1, "end_line": 2},
                metadata={"language": "python"},
            ),
        )
        return PassageLookup(passage, passage.text)


def test_repository_api_lists_safe_roots_and_registers_relative_repository(tmp_path: Path) -> None:
    root = tmp_path / "projects"
    (root / "sample-api").mkdir(parents=True)
    registry = RepositoryStore(tmp_path / "registry.sqlite", [RepositoryRoot("projects", root)])
    settings = Settings(tmp_path / "var", mock_delay_ms=0)

    with TestClient(
        create_app(settings, FixtureWorkflow(settings), repository_store=registry)
    ) as client:
        roots = client.get("/api/repositories/roots")
        created = client.post(
            "/api/repositories",
            json={"root_id": "projects", "relative_path": "sample-api"},
        )
        listed = client.get("/api/repositories")

        assert roots.status_code == 200
        assert roots.json() == [{"id": "projects"}]
        assert created.status_code == 201
        assert created.json()["relative_path"] == "sample-api"
        assert str(root) not in str(created.json())
        assert listed.status_code == 200
        assert [item["relative_path"] for item in listed.json()] == ["sample-api"]


def test_repository_scan_marks_current_evidence_stale(tmp_path: Path) -> None:
    root = tmp_path / "projects"
    repository_path = root / "sample-api"
    repository_path.mkdir(parents=True)
    source = repository_path / "auth.py"
    source.write_text("def authenticate():\n    return True\n", encoding="utf-8")
    registry = RepositoryStore(tmp_path / "registry.sqlite", [RepositoryRoot("projects", root)])
    repository = registry.register("projects", "sample-api")
    snapshot = registry.create_snapshot(
        repository.id,
        "fixture",
        RepositoryScanner().scan(repository_path).manifest_sha256,
    )
    registry.activate_snapshot(repository.id, snapshot.id)
    source.write_text("def authenticate():\n    return False\n", encoding="utf-8")
    settings = Settings(tmp_path / "var", mock_delay_ms=0)

    with TestClient(
        create_app(settings, FixtureWorkflow(settings), repository_store=registry)
    ) as client:
        response = client.post(f"/api/repositories/{repository.id}/scan")

    assert response.status_code == 200, response.json()
    assert response.json()["status"] == "stale"
    assert response.json()["current_collection_id"] == "fixture"


def test_index_endpoint_starts_one_background_build_for_a_registered_repository(
    tmp_path: Path,
) -> None:
    root = tmp_path / "projects"
    (root / "sample-api").mkdir(parents=True)
    registry = RepositoryStore(tmp_path / "registry.sqlite", [RepositoryRoot("projects", root)])
    repository = registry.register("projects", "sample-api")
    indexer = RecordingIndexer()
    settings = Settings(tmp_path / "var", mock_delay_ms=0)

    with TestClient(
        create_app(
            settings,
            FixtureWorkflow(settings),
            repository_store=registry,
            repository_indexer=indexer,
        )
    ) as client:
        response = client.post(f"/api/repositories/{repository.id}/indexes")

        assert response.status_code == 202
        assert response.json()["status"] == "indexing"
        assert indexer.started.wait(timeout=1)
    assert indexer.repository_ids == [repository.id]


def test_session_creation_binds_a_ready_current_repository_snapshot(tmp_path: Path) -> None:
    root = tmp_path / "projects"
    repository_path = root / "sample-api"
    repository_path.mkdir(parents=True)
    (repository_path / "auth.py").write_text(
        "def authenticate():\n    return True\n", encoding="utf-8"
    )
    registry = RepositoryStore(tmp_path / "registry.sqlite", [RepositoryRoot("projects", root)])
    repository = registry.register("projects", "sample-api")
    snapshot = registry.create_snapshot(
        repository.id,
        "fixture",
        RepositoryScanner().scan(repository_path).manifest_sha256,
    )
    registry.activate_snapshot(repository.id, snapshot.id)
    settings = Settings(tmp_path / "var", mock_delay_ms=0)

    with TestClient(
        create_app(settings, FixtureWorkflow(settings), repository_store=registry)
    ) as client:
        response = client.post(
            "/api/sessions",
            json={"repository_id": repository.id, "collection_id": "fixture"},
        )

    assert response.status_code == 201
    assert response.json()["repository_id"] == repository.id
    assert response.json()["collection_id"] == "fixture"


def test_evidence_endpoint_returns_frozen_lines_from_the_selected_snapshot(tmp_path: Path) -> None:
    root = tmp_path / "projects"
    repository_path = root / "sample-api"
    repository_path.mkdir(parents=True)
    registry = RepositoryStore(tmp_path / "registry.sqlite", [RepositoryRoot("projects", root)])
    repository = registry.register("projects", "sample-api")
    snapshot = registry.create_snapshot(repository.id, "fixture", "0" * 64)
    registry.activate_snapshot(repository.id, snapshot.id)
    settings = Settings(tmp_path / "var")

    with TestClient(
        create_app(
            settings,
            FixtureWorkflow(settings),
            repository_store=registry,
            repository_evidence=FrozenEvidence(),
        )
    ) as client:
        response = client.get(f"/api/repositories/{repository.id}/evidence/doc:file-auth@c001")

    assert response.status_code == 200, response.json()
    assert response.json() == {
        "collection_id": "fixture",
        "snapshot_status": "ready",
        "retention_reason": "当前证据快照，供新的调查会话使用",
        "path": "src/auth.py",
        "start_line": 1,
        "end_line": 2,
        "content": "def authenticate():\n    return True\n",
        "source_sha256": "frozen-sha",
    }


def test_evidence_endpoint_can_read_a_superseded_snapshot_for_a_historical_session(
    tmp_path: Path,
) -> None:
    root = tmp_path / "projects"
    repository_path = root / "sample-api"
    repository_path.mkdir(parents=True)
    registry = RepositoryStore(tmp_path / "registry.sqlite", [RepositoryRoot("projects", root)])
    repository = registry.register("projects", "sample-api")
    old = registry.create_snapshot(repository.id, "fixture", "0" * 64)
    registry.activate_snapshot(repository.id, old.id)
    latest = registry.create_snapshot(repository.id, "fixture-current", "1" * 64)
    registry.activate_snapshot(repository.id, latest.id)
    settings = Settings(tmp_path / "var")

    with TestClient(
        create_app(
            settings,
            FixtureWorkflow(settings),
            repository_store=registry,
            repository_evidence=FrozenEvidence(),
        )
    ) as client:
        response = client.get(
            f"/api/repositories/{repository.id}/evidence/doc:file-auth@c001?collection_id=fixture"
        )

    assert response.status_code == 200, response.json()
    assert response.json()["collection_id"] == "fixture"


def test_snapshot_prune_only_deletes_superseded_evidence_not_current_snapshot(
    tmp_path: Path,
) -> None:
    root = tmp_path / "projects"
    repository_path = root / "sample-api"
    repository_path.mkdir(parents=True)
    registry = RepositoryStore(tmp_path / "registry.sqlite", [RepositoryRoot("projects", root)])
    repository = registry.register("projects", "sample-api")
    old = registry.create_snapshot(repository.id, "collection-v1", "0" * 64)
    registry.activate_snapshot(repository.id, old.id)
    latest = registry.create_snapshot(repository.id, "collection-v2", "1" * 64)
    registry.activate_snapshot(repository.id, latest.id)
    indexer = PruningIndexer()
    settings = Settings(tmp_path / "var")

    with TestClient(
        create_app(
            settings,
            FixtureWorkflow(settings),
            repository_store=registry,
            repository_indexer=indexer,
        )
    ) as client:
        preview = client.post(f"/api/repositories/{repository.id}/snapshots/prune-preview")
        response = client.post(
            f"/api/repositories/{repository.id}/snapshots/prune",
            json={"collection_ids": ["collection-v1"]},
        )
        rejected = client.post(
            f"/api/repositories/{repository.id}/snapshots/prune",
            json={"collection_ids": ["collection-v2"]},
        )

    assert preview.status_code == 200, preview.json()
    assert preview.json() == {
        "candidates": [{"collection_id": "collection-v1", "status": "superseded"}]
    }
    assert response.status_code == 200, response.json()
    assert response.json() == {"deleted_collection_ids": ["collection-v1"]}
    assert indexer.collections.deleted == ["collection-v1"]
    assert rejected.status_code == 409
    try:
        registry.get_snapshot_for_collection(repository.id, "collection-v1")
    except KeyError:
        pass
    else:
        raise AssertionError("Pruned snapshot metadata must be removed")


def test_patch_preview_validates_citations_but_never_writes_the_repository(tmp_path: Path) -> None:
    root = tmp_path / "projects"
    repository_path = root / "sample-api"
    repository_path.mkdir(parents=True)
    source = repository_path / "auth.py"
    original = "def authenticate():\n    return True\n"
    source.write_text(original, encoding="utf-8")
    registry = RepositoryStore(tmp_path / "registry.sqlite", [RepositoryRoot("projects", root)])
    repository = registry.register("projects", "sample-api")
    scan = RepositoryScanner().scan(repository_path)
    snapshot = registry.create_snapshot(
        repository.id,
        "fixture",
        scan.manifest_sha256,
        {item.path: item.sha256 for item in scan.files},
    )
    registry.activate_snapshot(repository.id, snapshot.id)
    settings = Settings(tmp_path / "var")
    body = {
        "summary": "Handle missing token explicitly.",
        "diff": (
            "--- a/auth.py\n+++ b/auth.py\n@@ -1,2 +1,2 @@\n"
            " def authenticate():\n-    return True\n+    return False\n"
        ),
        "citations": [{"path": "auth.py", "start_line": 1, "end_line": 2}],
    }

    app = create_app(settings, FixtureWorkflow(settings), repository_store=registry)
    with TestClient(app) as client:
        response = client.post(f"/api/repositories/{repository.id}/patch-preview", json=body)

    assert response.status_code == 200, response.json()
    assert response.json()["diff"] == body["diff"]
    assert response.json()["citations"] == body["citations"]
    assert source.read_text(encoding="utf-8") == original
