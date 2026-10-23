from __future__ import annotations

from pathlib import Path

import pytest

from repo_evidence.evaluation.online import (
    OnlineCase,
    load_online_cases,
    percentile,
    score_online_answer,
)
from repo_evidence.repositories.evaluation import Citation


def test_score_requires_terms_and_supported_citations() -> None:
    case = OnlineCase(
        id="workflow-tools",
        category="tool-boundary",
        question="证据工作流提供哪些只读工具？",
        expected_citations=(Citation("backend/src/repo_evidence/workflows/evidence.py", 201, 209),),
        required_terms=("search_repository", "read_file"),
    )

    result = score_online_answer(
        case,
        (
            "使用 search_repository 与 read_file 查询受限证据。\n"
            "backend/src/repo_evidence/workflows/evidence.py:201-209"
        ),
        elapsed_ms=1234,
    )

    assert result.strict_passed is True
    assert result.citation_accuracy == 1.0
    assert result.citation_coverage == 1.0


def test_score_penalizes_unexpected_citation() -> None:
    case = OnlineCase(
        id="workflow-tools",
        category="tool-boundary",
        question="证据工作流提供哪些只读工具？",
        expected_citations=(Citation("backend/src/repo_evidence/workflows/evidence.py", 201, 209),),
        required_terms=("search_repository",),
    )

    result = score_online_answer(
        case,
        (
            "search_repository 是检索工具。\n"
            "backend/src/repo_evidence/workflows/evidence.py:201-209\n"
            "backend/src/repo_evidence/bootstrap.py:1-2"
        ),
        elapsed_ms=1,
    )

    assert result.citation_accuracy == 0.5
    assert result.strict_passed is False


def test_percentile_uses_nearest_rank_and_rejects_empty_values() -> None:
    assert percentile([10, 20, 30], 0.5) == 20
    assert percentile([10, 20, 30], 0.95) == 30
    with pytest.raises(ValueError, match="non-empty"):
        percentile([], 0.5)


def test_online_case_bank_has_thirty_cases_and_required_categories() -> None:
    root = Path(__file__).resolve().parents[3]
    cases = load_online_cases(root / "evaluation/cases/repository-evidence-online.json")

    assert len(cases) >= 30
    assert {case.category for case in cases} >= {
        "architecture",
        "configuration",
        "call-chain",
        "snapshot",
        "error-boundary",
        "tool-boundary",
    }


def test_every_online_case_citation_exists_in_this_checkout() -> None:
    root = Path(__file__).resolve().parents[3]
    cases = load_online_cases(root / "evaluation/cases/repository-evidence-online.json")

    for case in cases:
        for citation in case.expected_citations:
            source = root / citation.path
            assert source.is_file(), case.id
            line_count = len(source.read_text(encoding="utf-8").splitlines())
            assert line_count >= citation.end_line, case.id
