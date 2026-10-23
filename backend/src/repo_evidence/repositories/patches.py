"""Validation for review-only patch previews; this module never changes a work tree."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .tools import RepositoryToolError, RepositoryTools

_HEADER = re.compile(r"^(?:--- a/|\+\+\+ b/)(?P<path>[^\r\n]+)$", re.MULTILINE)


@dataclass(frozen=True)
class PatchCitation:
    path: str
    start_line: int
    end_line: int


@dataclass(frozen=True)
class PatchPreview:
    summary: str
    diff: str
    citations: tuple[PatchCitation, ...]


def validate_patch_preview(
    tools: RepositoryTools,
    repository_id: str,
    summary: str,
    diff: str,
    citations: list[PatchCitation],
) -> PatchPreview:
    """Return a review artifact only after every changed path has current, bounded evidence."""
    if not summary.strip():
        raise RepositoryToolError("Patch preview summary must not be blank")
    if not diff.strip() or len(diff) > 100_000:
        raise RepositoryToolError("Patch preview diff must be between 1 and 100000 characters")
    if not citations:
        raise RepositoryToolError("Patch preview requires at least one cited source range")
    cited_paths: set[str] = set()
    for citation in citations:
        # Reading is also the freshness gate: no preview can be issued for stale evidence.
        tools.read_file(repository_id, citation.path, citation.start_line, citation.end_line)
        cited_paths.add(citation.path)
    targets = {match["path"] for match in _HEADER.finditer(diff)}
    if not targets:
        raise RepositoryToolError("Patch preview requires unified diff file headers")
    if targets - cited_paths:
        raise RepositoryToolError("Every changed file must have a cited source range")
    return PatchPreview(summary.strip(), diff, tuple(citations))
