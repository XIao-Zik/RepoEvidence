"""Strict, deterministic scoring primitives for real repository-evidence sessions."""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from repo_evidence.repositories.evaluation import Citation, RepositoryCase, score_case

_CITATION = re.compile(r"(?P<path>[A-Za-z0-9_./-]+):(?P<start>[1-9][0-9]*)-(?P<end>[1-9][0-9]*)")


@dataclass(frozen=True)
class OnlineCase:
    """A human-authored question and the evidence needed to verify one answer."""

    id: str
    category: str
    question: str
    expected_citations: tuple[Citation, ...]
    required_terms: tuple[str, ...] = ()
    forbidden_paths: tuple[str, ...] = ()


@dataclass(frozen=True)
class OnlineCaseResult:
    """Deterministic evidence score for one answer returned by the real session API."""

    case_id: str
    category: str
    answer: str
    citations: tuple[Citation, ...]
    citation_accuracy: float
    citation_coverage: float
    required_terms_present: bool
    contains_forbidden_path: bool
    strict_passed: bool
    elapsed_ms: int


def parse_citations(text: str) -> list[Citation]:
    """Parse only well-formed inclusive repository line ranges from answer text."""
    citations: list[Citation] = []
    for match in _CITATION.finditer(text):
        start_line, end_line = int(match["start"]), int(match["end"])
        if end_line >= start_line:
            citations.append(Citation(match["path"], start_line, end_line))
    return citations


def score_online_answer(case: OnlineCase, answer: str, elapsed_ms: int) -> OnlineCaseResult:
    """Score a real answer without another model call."""
    if elapsed_ms < 0:
        raise ValueError("elapsed_ms must be non-negative")
    citations = tuple(parse_citations(answer))
    score = score_case(
        answer,
        list(citations),
        RepositoryCase(
            question=case.question,
            expected_citations=case.expected_citations,
            required_terms=case.required_terms,
            forbidden_paths=case.forbidden_paths,
        ),
    )
    return OnlineCaseResult(
        case_id=case.id,
        category=case.category,
        answer=answer,
        citations=citations,
        citation_accuracy=score.citation_accuracy,
        citation_coverage=score.citation_coverage,
        required_terms_present=score.required_terms_present,
        contains_forbidden_path=score.contains_forbidden_path,
        strict_passed=score.passed,
        elapsed_ms=elapsed_ms,
    )


def percentile(values: list[int], ratio: float) -> int:
    """Return a nearest-rank percentile for integer durations."""
    if not values:
        raise ValueError("percentile requires non-empty values")
    if not 0 < ratio <= 1:
        raise ValueError("percentile ratio must be in (0, 1]")
    ordered = sorted(values)
    return ordered[math.ceil(len(ordered) * ratio) - 1]


def load_online_cases(path: Path, minimum_cases: int = 30) -> tuple[OnlineCase, ...]:
    """Load a source-controlled online suite and reject ambiguous or incomplete cases."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    raw_cases = payload.get("cases") if isinstance(payload, dict) else None
    if not isinstance(raw_cases, list):
        raise ValueError("Online evaluation cases must contain a cases list")
    cases = tuple(_case(raw) for raw in raw_cases)
    ids = [case.id for case in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("Online evaluation case IDs must be unique")
    if len(cases) < minimum_cases:
        raise ValueError(f"Online evaluation requires at least {minimum_cases} cases")
    return cases


def _case(raw: object) -> OnlineCase:
    if not isinstance(raw, dict):
        raise ValueError("Each online evaluation case must be an object")
    case_id = _text(raw, "id")
    category = _text(raw, "category")
    question = _text(raw, "question")
    expected = tuple(_citation(item) for item in _objects(raw, "expected_citations"))
    if not expected:
        raise ValueError("expected_citations must be non-empty")
    return OnlineCase(
        id=case_id,
        category=category,
        question=question,
        expected_citations=expected,
        required_terms=tuple(_texts(raw, "required_terms")),
        forbidden_paths=tuple(_texts(raw, "forbidden_paths")),
    )


def _objects(raw: dict[str, Any], key: str) -> list[dict[str, Any]]:
    value = raw.get(key)
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise ValueError(f"{key} must be a list of objects")
    return value


def _texts(raw: dict[str, Any], key: str) -> list[str]:
    value = raw.get(key, [])
    valid = isinstance(value, list) and all(
        isinstance(item, str) and item.strip() for item in value
    )
    if not valid:
        raise ValueError(f"{key} must be a list of non-empty strings")
    return value


def _text(raw: dict[str, Any], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a non-empty string")
    return value


def _citation(raw: dict[str, Any]) -> Citation:
    path = raw.get("path")
    start_line, end_line = raw.get("start_line"), raw.get("end_line")
    if (
        not isinstance(path, str)
        or not path.strip()
        or type(start_line) is not int
        or type(end_line) is not int
        or start_line < 1
        or end_line < start_line
    ):
        raise ValueError("expected_citations needs valid path, start_line, and end_line")
    return Citation(path, start_line, end_line)
