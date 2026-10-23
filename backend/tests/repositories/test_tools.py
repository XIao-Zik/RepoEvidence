from __future__ import annotations

from pathlib import Path

import pytest

from repo_evidence.repositories.models import RepositoryRoot
from repo_evidence.repositories.scanner import RepositoryScanner
from repo_evidence.repositories.store import RepositoryStore
from repo_evidence.repositories.tools import RepositoryToolError, RepositoryTools


def ready_repository(tmp_path: Path) -> tuple[RepositoryStore, str]:
    root = tmp_path / "projects"
    repository_path = root / "sample-api"
    (repository_path / "src").mkdir(parents=True)
    (repository_path / "src/auth.py").write_text(
        "".join(f"line_{number}\n" for number in range(1, 301)), encoding="utf-8"
    )
    (repository_path / ".env").write_text("SECRET=value\n", encoding="utf-8")
    store = RepositoryStore(tmp_path / "registry.sqlite3", [RepositoryRoot("projects", root)])
    repository = store.register("projects", "sample-api")
    manifest = RepositoryScanner().scan(repository_path).manifest_sha256
    snapshot = store.create_snapshot(repository.id, "repo-snapshot", manifest)
    store.activate_snapshot(repository.id, snapshot.id)
    return store, repository.id


def test_read_file_caps_lines_and_returns_current_source_hash(tmp_path: Path) -> None:
    store, repository_id = ready_repository(tmp_path)

    result = RepositoryTools(store).read_file(repository_id, "src/auth.py", 1, 999)

    assert result.path == "src/auth.py"
    assert (result.start_line, result.end_line) == (1, 200)
    assert result.source_sha256
    assert result.content.startswith("line_1\n")


def test_read_file_rejects_sensitive_or_outside_paths(tmp_path: Path) -> None:
    store, repository_id = ready_repository(tmp_path)
    tools = RepositoryTools(store)

    with pytest.raises(RepositoryToolError, match="not readable"):
        tools.read_file(repository_id, ".env", 1, 2)
    with pytest.raises(RepositoryToolError, match="relative"):
        tools.read_file(repository_id, "../outside.py", 1, 2)


def test_list_directory_returns_only_safe_files_below_the_requested_directory(
    tmp_path: Path,
) -> None:
    store, repository_id = ready_repository(tmp_path)
    repository = store.get_repository(repository_id)
    root = store.repository_path(repository)
    (root / "docs").mkdir()
    (root / "docs" / "runbook.md").write_text("# Runbook\n", encoding="utf-8")
    (root / "docs" / ".env").write_text("SECRET=value\n", encoding="utf-8")
    refreshed = store.create_snapshot(
        repository_id,
        "repo-snapshot-with-docs",
        RepositoryScanner().scan(root).manifest_sha256,
    )
    store.activate_snapshot(repository_id, refreshed.id)

    result = RepositoryTools(store).list_directory(repository_id, "docs", depth=1)

    assert result.path == "docs"
    assert result.entries == ("runbook.md",)
