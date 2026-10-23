"""In-process, non-sensitive progress summaries for repository index builds."""

from __future__ import annotations

from dataclasses import dataclass, replace
from time import monotonic


@dataclass(frozen=True)
class IndexProgress:
    phase: str = "idle"
    scanned_files: int = 0
    ignored_files: int = 0
    reused_files: int = 0
    new_chunks: int = 0
    elapsed_ms: int = 0
    error: str | None = None


class IndexProgressStore:
    """Keeps progress only for the current process; registry state remains the source of truth."""

    def __init__(self) -> None:
        self._items: dict[str, tuple[float, IndexProgress]] = {}

    def update(self, repository_id: str, phase: str, **changes: int | str | None) -> IndexProgress:
        started, current = self._items.get(repository_id, (monotonic(), IndexProgress()))
        values = {"phase": phase, "elapsed_ms": round((monotonic() - started) * 1000), **changes}
        updated = replace(current, **values)
        self._items[repository_id] = (started, updated)
        return updated

    def get(self, repository_id: str) -> IndexProgress | None:
        item = self._items.get(repository_id)
        if item is None:
            return None
        started, current = item
        return replace(current, elapsed_ms=round((monotonic() - started) * 1000))
