from __future__ import annotations

from pathlib import Path

import pytest

from repo_evidence.core.config.settings import RepositoryRootSettings, Settings
from repo_evidence.repositories.models import RepositoryRoot, SnapshotStatus
from repo_evidence.repositories.store import RepositoryPathError, RepositoryStore


def test_registers_a_normalized_repository_relative_to_an_allowed_root(tmp_path: Path) -> None:
    root = tmp_path / "projects"
    repository = root / "sample-api"
    repository.mkdir(parents=True)
    store = RepositoryStore(tmp_path / "registry.sqlite3", [RepositoryRoot("projects", root)])

    record = store.register("projects", "sample-api")

    assert record.root_id == "projects"
    assert record.relative_path == "sample-api"
    assert record.display_name == "sample-api"
    assert record.status == "unindexed"
    assert record.current_collection_id is None


def test_rejects_repository_paths_outside_an_allowed_root(tmp_path: Path) -> None:
    root = tmp_path / "projects"
    root.mkdir()
    store = RepositoryStore(tmp_path / "registry.sqlite3", [RepositoryRoot("projects", root)])

    with pytest.raises(RepositoryPathError, match="relative"):
        store.register("projects", "../secret-project")


def test_activating_a_snapshot_supersedes_the_previous_one(tmp_path: Path) -> None:
    root = tmp_path / "projects"
    (root / "sample-api").mkdir(parents=True)
    store = RepositoryStore(tmp_path / "registry.sqlite3", [RepositoryRoot("projects", root)])
    repository = store.register("projects", "sample-api")
    first = store.create_snapshot(repository.id, "repo-a-111", "a" * 64)
    store.activate_snapshot(repository.id, first.id)
    second = store.create_snapshot(repository.id, "repo-a-222", "b" * 64)

    current = store.activate_snapshot(repository.id, second.id)

    assert current.current_collection_id == "repo-a-222"
    assert store.get_snapshot(first.id).status is SnapshotStatus.SUPERSEDED
    assert store.get_snapshot(second.id).status is SnapshotStatus.READY


def test_retries_a_failed_snapshot_with_the_same_deterministic_collection_id(
    tmp_path: Path,
) -> None:
    root = tmp_path / "projects"
    (root / "sample-api").mkdir(parents=True)
    store = RepositoryStore(tmp_path / "registry.sqlite3", [RepositoryRoot("projects", root)])
    repository = store.register("projects", "sample-api")
    failed = store.create_snapshot(repository.id, "repo-a-failed", "a" * 64)
    store.fail_snapshot(failed.id)

    retried = store.create_snapshot(repository.id, "repo-a-failed", "a" * 64)

    assert retried.id == failed.id
    assert retried.status is SnapshotStatus.BUILDING


def test_prune_candidates_keep_current_and_protected_snapshots(tmp_path: Path) -> None:
    root = tmp_path / "projects"
    (root / "sample-api").mkdir(parents=True)
    store = RepositoryStore(tmp_path / "registry.sqlite3", [RepositoryRoot("projects", root)])
    repository = store.register("projects", "sample-api")
    current = store.create_snapshot(repository.id, "current", "0" * 64)
    store.activate_snapshot(repository.id, current.id)
    history = store.create_snapshot(repository.id, "history", "1" * 64)
    store.activate_snapshot(repository.id, history.id)
    failed = store.create_snapshot(repository.id, "failed", "2" * 64)
    store.fail_snapshot(failed.id)

    candidates = store.prune_candidates(repository.id, protected_collection_ids={"current"})

    assert [snapshot.collection_id for snapshot in candidates] == ["failed"]


def test_settings_snapshot_keeps_root_ids_and_paths_without_secret_values(tmp_path: Path) -> None:
    settings = Settings(
        data_dir=tmp_path / "var",
        repository_roots=(RepositoryRootSettings("projects", tmp_path / "projects"),),
    )

    assert settings.snapshot()["repository_roots"] == [
        {"id": "projects", "path": str(tmp_path / "projects")}
    ]


def test_repository_root_settings_reject_duplicate_or_empty_ids(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="non-empty"):
        RepositoryRootSettings("", tmp_path)
    with pytest.raises(ValueError, match="unique"):
        Settings(
            data_dir=tmp_path / "var",
            repository_roots=(
                RepositoryRootSettings("projects", tmp_path / "one"),
                RepositoryRootSettings("projects", tmp_path / "two"),
            ),
        )
