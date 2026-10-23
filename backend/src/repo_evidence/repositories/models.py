"""Typed records owned by the local repository registry."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path


class SnapshotStatus(StrEnum):
    """Lifecycle of an immutable evidence snapshot."""

    BUILDING = "building"
    READY = "ready"
    FAILED = "failed"
    SUPERSEDED = "superseded"


@dataclass(frozen=True)
class RepositoryRoot:
    """A named root that an administrator has mounted read-only for this product."""

    id: str
    path: Path


@dataclass(frozen=True)
class RepositoryRecord:
    """A user-selectable repository below one configured root."""

    id: str
    root_id: str
    relative_path: str
    display_name: str
    status: str
    current_collection_id: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class RepositorySnapshot:
    """A registry pointer to one immutable vector collection."""

    id: str
    repository_id: str
    collection_id: str
    manifest_sha256: str
    status: SnapshotStatus
    parent_snapshot_id: str | None
    created_at: datetime
    ready_at: datetime | None
