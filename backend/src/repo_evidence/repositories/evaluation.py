"""Small deterministic checks for whether an answer is supported by repository evidence."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Citation:
    """One inclusive repository-relative line range cited by an answer."""

    path: str
    start_line: int
    end_line: int


@dataclass(frozen=True)
class RepositoryCase:
    """A fixture question with the minimum evidence required for a trustworthy answer."""

    question: str
    expected_citations: tuple[Citation, ...]
    required_terms: tuple[str, ...] = ()
    forbidden_paths: tuple[str, ...] = ()


@dataclass(frozen=True)
class EvidenceScore:
    """Separate wording from evidence quality so plausible answers cannot hide bad citations."""

    citation_accuracy: float
    citation_coverage: float
    required_terms_present: bool
    contains_forbidden_path: bool
    passed: bool


def score_case(answer: str, cited_passages: list[Citation], case: RepositoryCase) -> EvidenceScore:
    """Score one deterministic case without calling a model or accessing a repository."""
    supported = [
        citation for citation in cited_passages if _matches_any(citation, case.expected_citations)
    ]
    citation_accuracy = len(supported) / len(cited_passages) if cited_passages else 0.0
    covered = sum(
        1
        for expected in case.expected_citations
        if any(_ranges_overlap(expected, actual) for actual in cited_passages)
    )
    citation_coverage = covered / len(case.expected_citations) if case.expected_citations else 1.0
    required_terms_present = all(
        term.casefold() in answer.casefold() for term in case.required_terms
    )
    contains_forbidden_path = any(
        citation.path in case.forbidden_paths for citation in cited_passages
    )
    passed = (
        citation_accuracy == 1.0
        and citation_coverage == 1.0
        and required_terms_present
        and not contains_forbidden_path
    )
    return EvidenceScore(
        citation_accuracy,
        citation_coverage,
        required_terms_present,
        contains_forbidden_path,
        passed,
    )


def _matches_any(actual: Citation, expected: tuple[Citation, ...]) -> bool:
    return any(_ranges_overlap(candidate, actual) for candidate in expected)


def _ranges_overlap(left: Citation, right: Citation) -> bool:
    return (
        left.path == right.path
        and left.start_line <= left.end_line
        and right.start_line <= right.end_line
        and left.start_line <= right.end_line
        and right.start_line <= left.end_line
    )
