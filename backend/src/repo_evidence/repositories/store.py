"""A small local registry for repositories and their immutable evidence snapshots."""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from uuid import uuid4

from .models import RepositoryRecord, RepositoryRoot, RepositorySnapshot, SnapshotStatus


class RepositoryPathError(ValueError):
    """The requested repository does not resolve below an explicitly allowed root."""


class RepositoryStore:
    """Persist product-local repository state without granting runtime write access to pgvector.

    Repository metadata belongs to the application state database, while documents, chunks and
    vectors remain protected by the existing PostgreSQL reader/builder roles. Each operation opens
    a short SQLite transaction so failed index attempts cannot change the active snapshot pointer.
    """

    def __init__(self, database_path: Path, roots: list[RepositoryRoot]) -> None:
        if not roots:
            raise ValueError("At least one allowed repository root is required")
        self.database_path = database_path
        self.roots = {root.id: RepositoryRoot(root.id, root.path.resolve()) for root in roots}
        if len(self.roots) != len(roots) or any(not root.id.strip() for root in roots):
            raise ValueError("Repository root IDs must be unique and non-empty")
        if missing := [str(root.path) for root in self.roots.values() if not root.path.is_dir()]:
            raise ValueError(f"Allowed repository root is not a directory: {', '.join(missing)}")
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            self._create_schema(connection)

    def register(self, root_id: str, relative_path: str) -> RepositoryRecord:
        """Register an existing directory only after resolving it below its configured root."""
        root = self._root(root_id)
        normalized = self._normalize_relative_path(relative_path)
        resolved = (root.path / normalized).resolve()
        if not resolved.is_relative_to(root.path) or not resolved.is_dir():
            raise RepositoryPathError(
                "Repository path must identify a directory below its allowed root"
            )
        now = self._now()
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT * FROM repositories WHERE root_id=? AND relative_path=?",
                (root.id, normalized),
            ).fetchone()
            if existing is not None:
                return self._repository(existing)
            identifier = str(uuid4())
            connection.execute(
                """INSERT INTO repositories(
                    id, root_id, relative_path, display_name, status, current_collection_id,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'unindexed', NULL, ?, ?)""",
                (identifier, root.id, normalized, PurePosixPath(normalized).name, now, now),
            )
            row = connection.execute(
                "SELECT * FROM repositories WHERE id=?", (identifier,)
            ).fetchone()
            assert row is not None
            return self._repository(row)

    def get_repository(self, repository_id: str) -> RepositoryRecord:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM repositories WHERE id=?", (repository_id,)
            ).fetchone()
        if row is None:
            raise KeyError(repository_id)
        return self._repository(row)

    def list_repositories(self) -> list[RepositoryRecord]:
        """List registered repository choices without resolving or exposing their host paths."""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM repositories ORDER BY display_name, id"
            ).fetchall()
        return [self._repository(row) for row in rows]

    def repository_path(self, repository: RepositoryRecord) -> Path:
        """Resolve a previously registered directory again before every filesystem operation."""
        root = self._root(repository.root_id)
        path = (root.path / repository.relative_path).resolve()
        if not path.is_relative_to(root.path) or not path.is_dir():
            raise RepositoryPathError("Registered repository is no longer inside its allowed root")
        return path

    def create_snapshot(
        self,
        repository_id: str,
        collection_id: str,
        manifest_sha256: str,
        file_sha256_by_path: Mapping[str, str] | None = None,
    ) -> RepositorySnapshot:
        """Record a building collection; only :meth:`activate_snapshot` can make it current."""
        if len(manifest_sha256) != 64 or set(manifest_sha256) - set("0123456789abcdef"):
            raise ValueError("Manifest SHA-256 must be 64 lowercase hexadecimal characters")
        repository = self.get_repository(repository_id)
        now = self._now()
        identifier = str(uuid4())
        files = file_sha256_by_path or {}
        for path, digest in files.items():
            self._normalize_relative_path(path)
            if len(digest) != 64 or set(digest) - set("0123456789abcdef"):
                raise ValueError("File SHA-256 must be 64 lowercase hexadecimal characters")
        with self._connect() as connection:
            parent = connection.execute(
                "SELECT id FROM repository_snapshots WHERE repository_id=? AND collection_id=?",
                (repository.id, repository.current_collection_id),
            ).fetchone()
            existing = connection.execute(
                "SELECT * FROM repository_snapshots WHERE collection_id=?", (collection_id,)
            ).fetchone()
            if existing is not None:
                if (
                    existing["repository_id"] != repository.id
                    or existing["manifest_sha256"] != manifest_sha256
                    or existing["status"] != SnapshotStatus.FAILED.value
                ):
                    raise ValueError("Collection ID already belongs to a non-retryable snapshot")
                identifier = existing["id"]
                connection.execute(
                    """UPDATE repository_snapshots SET status='building', parent_snapshot_id=?,
                    created_at=?, ready_at=NULL WHERE id=?""",
                    (parent[0] if parent else None, now, identifier),
                )
                connection.execute(
                    "DELETE FROM repository_snapshot_files WHERE snapshot_id=?", (identifier,)
                )
            else:
                connection.execute(
                    """INSERT INTO repository_snapshots(
                        id, repository_id, collection_id, manifest_sha256, status,
                        parent_snapshot_id, created_at, ready_at
                    ) VALUES (?, ?, ?, ?, 'building', ?, ?, NULL)""",
                    (
                        identifier,
                        repository.id,
                        collection_id,
                        manifest_sha256,
                        parent[0] if parent else None,
                        now,
                    ),
                )
            connection.executemany(
                """INSERT INTO repository_snapshot_files(snapshot_id, path, sha256)
                VALUES (?, ?, ?)""",
                [(identifier, path, digest) for path, digest in sorted(files.items())],
            )
            row = connection.execute(
                "SELECT * FROM repository_snapshots WHERE id=?", (identifier,)
            ).fetchone()
            assert row is not None
            return self._snapshot(row)

    def activate_snapshot(self, repository_id: str, snapshot_id: str) -> RepositoryRecord:
        """Publish one finished snapshot and supersede the prior current version atomically."""
        now = self._now()
        with self._connect() as connection:
            repository = connection.execute(
                "SELECT * FROM repositories WHERE id=?", (repository_id,)
            ).fetchone()
            snapshot = connection.execute(
                "SELECT * FROM repository_snapshots WHERE id=? AND repository_id=?",
                (snapshot_id, repository_id),
            ).fetchone()
            if repository is None or snapshot is None:
                raise KeyError(snapshot_id)
            if snapshot["status"] not in {
                SnapshotStatus.BUILDING.value,
                SnapshotStatus.READY.value,
            }:
                raise ValueError("Only a building or ready snapshot can be activated")
            previous = repository["current_collection_id"]
            if previous is not None and previous != snapshot["collection_id"]:
                connection.execute(
                    """UPDATE repository_snapshots SET status=?
                    WHERE repository_id=? AND collection_id=? AND status=?""",
                    (
                        SnapshotStatus.SUPERSEDED.value,
                        repository_id,
                        previous,
                        SnapshotStatus.READY.value,
                    ),
                )
            connection.execute(
                "UPDATE repository_snapshots SET status=?, ready_at=? WHERE id=?",
                (SnapshotStatus.READY.value, now, snapshot_id),
            )
            connection.execute(
                """UPDATE repositories SET status='ready', current_collection_id=?, updated_at=?
                WHERE id=?""",
                (snapshot["collection_id"], now, repository_id),
            )
            row = connection.execute(
                "SELECT * FROM repositories WHERE id=?", (repository_id,)
            ).fetchone()
            assert row is not None
            return self._repository(row)

    def get_snapshot(self, snapshot_id: str) -> RepositorySnapshot:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM repository_snapshots WHERE id=?", (snapshot_id,)
            ).fetchone()
        if row is None:
            raise KeyError(snapshot_id)
        return self._snapshot(row)

    def get_snapshot_for_collection(
        self, repository_id: str, collection_id: str
    ) -> RepositorySnapshot:
        """Look up the immutable manifest associated with a repository collection."""
        with self._connect() as connection:
            row = connection.execute(
                """SELECT * FROM repository_snapshots
                WHERE repository_id=? AND collection_id=?""",
                (repository_id, collection_id),
            ).fetchone()
        if row is None:
            raise KeyError(collection_id)
        return self._snapshot(row)

    def fail_snapshot(self, snapshot_id: str) -> RepositorySnapshot:
        """Keep a failed build visible without changing the active evidence pointer."""
        with self._connect() as connection:
            connection.execute(
                "UPDATE repository_snapshots SET status=? WHERE id=? AND status=?",
                (SnapshotStatus.FAILED.value, snapshot_id, SnapshotStatus.BUILDING.value),
            )
        return self.get_snapshot(snapshot_id)

    def snapshot_file_hashes(self, snapshot_id: str) -> dict[str, str]:
        """Return the safe-file manifest frozen with one evidence snapshot."""
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT path, sha256 FROM repository_snapshot_files
                WHERE snapshot_id=? ORDER BY path""",
                (snapshot_id,),
            ).fetchall()
        return {str(row["path"]): str(row["sha256"]) for row in rows}

    def prune_candidates(
        self, repository_id: str, *, protected_collection_ids: set[str]
    ) -> list[RepositorySnapshot]:
        """Return only old failed/superseded evidence that no active history needs."""
        repository = self.get_repository(repository_id)
        protected = {*protected_collection_ids}
        if repository.current_collection_id:
            protected.add(repository.current_collection_id)
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT * FROM repository_snapshots WHERE repository_id=?
                AND status IN ('failed', 'superseded') ORDER BY created_at""",
                (repository_id,),
            ).fetchall()
        return [self._snapshot(row) for row in rows if row["collection_id"] not in protected]

    def delete_pruned_snapshot(
        self, repository_id: str, collection_id: str, *, protected_collection_ids: set[str]
    ) -> None:
        """Delete registry metadata only after the vector collection was deleted successfully."""
        candidates = {
            item.collection_id: item
            for item in self.prune_candidates(
                repository_id, protected_collection_ids=protected_collection_ids
            )
        }
        snapshot = candidates.get(collection_id)
        if snapshot is None:
            raise ValueError("Snapshot is no longer eligible for cleanup")
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM repository_snapshot_files WHERE snapshot_id=?", (snapshot.id,)
            )
            connection.execute("DELETE FROM repository_snapshots WHERE id=?", (snapshot.id,))

    def set_current_snapshot_stale(self, repository_id: str, stale: bool) -> RepositoryRecord:
        """Expose freshness without changing the immutable collection currently serving history."""
        status = "stale" if stale else "ready"
        now = self._now()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM repositories WHERE id=?", (repository_id,)
            ).fetchone()
            if row is None:
                raise KeyError(repository_id)
            if row["current_collection_id"] is None:
                raise ValueError("Cannot set freshness without a ready evidence snapshot")
            connection.execute(
                "UPDATE repositories SET status=?, updated_at=? WHERE id=?",
                (status, now, repository_id),
            )
            updated = connection.execute(
                "SELECT * FROM repositories WHERE id=?", (repository_id,)
            ).fetchone()
            assert updated is not None
            return self._repository(updated)

    def begin_index(self, repository_id: str) -> RepositoryRecord:
        """Record a build in progress without replacing the collection used by existing sessions."""
        return self._set_repository_status(repository_id, "indexing")

    def fail_index(self, repository_id: str) -> RepositoryRecord:
        """Keep a usable predecessor but never claim a failed first build is ready."""
        repository = self.get_repository(repository_id)
        return self._set_repository_status(
            repository_id, "stale" if repository.current_collection_id else "failed"
        )

    def _set_repository_status(self, repository_id: str, status: str) -> RepositoryRecord:
        now = self._now()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM repositories WHERE id=?", (repository_id,)
            ).fetchone()
            if row is None:
                raise KeyError(repository_id)
            connection.execute(
                "UPDATE repositories SET status=?, updated_at=? WHERE id=?",
                (status, now, repository_id),
            )
            updated = connection.execute(
                "SELECT * FROM repositories WHERE id=?", (repository_id,)
            ).fetchone()
            assert updated is not None
            return self._repository(updated)

    def _root(self, root_id: str) -> RepositoryRoot:
        try:
            return self.roots[root_id]
        except KeyError as error:
            raise RepositoryPathError("Unknown allowed repository root") from error

    @staticmethod
    def _normalize_relative_path(value: str) -> str:
        if not value or "\\" in value:
            raise RepositoryPathError("Repository path must be a non-empty POSIX relative path")
        path = PurePosixPath(value)
        if path.is_absolute() or ".." in path.parts or path == PurePosixPath("."):
            raise RepositoryPathError("Repository path must be relative to its allowed root")
        return path.as_posix()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        return connection

    @staticmethod
    def _create_schema(connection: sqlite3.Connection) -> None:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS repositories (
                id TEXT PRIMARY KEY,
                root_id TEXT NOT NULL,
                relative_path TEXT NOT NULL,
                display_name TEXT NOT NULL,
                status TEXT NOT NULL CHECK(
                    status IN ('unindexed', 'indexing', 'ready', 'stale', 'failed')
                ),
                current_collection_id TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(root_id, relative_path)
            );
            CREATE TABLE IF NOT EXISTS repository_snapshots (
                id TEXT PRIMARY KEY,
                repository_id TEXT NOT NULL REFERENCES repositories(id),
                collection_id TEXT NOT NULL UNIQUE,
                manifest_sha256 TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('building', 'ready', 'failed', 'superseded')),
                parent_snapshot_id TEXT REFERENCES repository_snapshots(id),
                created_at TEXT NOT NULL,
                ready_at TEXT
            );
            CREATE TABLE IF NOT EXISTS repository_snapshot_files (
                snapshot_id TEXT NOT NULL REFERENCES repository_snapshots(id),
                path TEXT NOT NULL,
                sha256 TEXT NOT NULL,
                PRIMARY KEY(snapshot_id, path)
            );
            """
        )

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat()

    @staticmethod
    def _repository(row: sqlite3.Row) -> RepositoryRecord:
        return RepositoryRecord(
            id=row["id"],
            root_id=row["root_id"],
            relative_path=row["relative_path"],
            display_name=row["display_name"],
            status=row["status"],
            current_collection_id=row["current_collection_id"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    @staticmethod
    def _snapshot(row: sqlite3.Row) -> RepositorySnapshot:
        return RepositorySnapshot(
            id=row["id"],
            repository_id=row["repository_id"],
            collection_id=row["collection_id"],
            manifest_sha256=row["manifest_sha256"],
            status=SnapshotStatus(row["status"]),
            parent_snapshot_id=row["parent_snapshot_id"],
            created_at=datetime.fromisoformat(row["created_at"]),
            ready_at=datetime.fromisoformat(row["ready_at"]) if row["ready_at"] else None,
        )
