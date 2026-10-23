from repo_evidence.repositories.evaluation import Citation, RepositoryCase, score_case


def auth_case() -> RepositoryCase:
    return RepositoryCase(
        question="认证入口在哪里？",
        expected_citations=(Citation("src/auth.py", 1, 4),),
        required_terms=("authenticate",),
        forbidden_paths=("README.md",),
    )


def test_score_rejects_a_correct_sounding_answer_with_wrong_file_evidence() -> None:
    score = score_case(
        "authenticate 在认证入口中调用。", [Citation("README.md", 1, 2)], auth_case()
    )

    assert score.citation_accuracy == 0
    assert not score.passed


def test_score_accepts_an_answer_with_overlapping_expected_line_evidence() -> None:
    score = score_case("authenticate 处理令牌。", [Citation("src/auth.py", 2, 3)], auth_case())

    assert score.citation_accuracy == 1
    assert score.citation_coverage == 1
    assert score.passed
