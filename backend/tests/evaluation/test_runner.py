import json
from pathlib import Path

from repo_evidence.evaluation.runner import run_offline_evaluation


def test_runner_writes_safe_summary_and_local_detail(tmp_path: Path) -> None:
    cases = tmp_path / "cases.json"
    cases.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "id": "citation-is-grounded",
                        "question": "Where is the scanner?",
                        "answer": "RepositoryScanner scans source files.",
                        "citations": [{"path": "scanner.py", "start_line": 1, "end_line": 3}],
                        "expected_citations": [
                            {"path": "scanner.py", "start_line": 1, "end_line": 3}
                        ],
                        "required_terms": ["RepositoryScanner"],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    report = run_offline_evaluation(cases, tmp_path / "artifacts", tmp_path / "summary.md")

    assert (report.total, report.passed) == (1, 1)
    assert report.detail_path.name == "offline-evaluation.json"
    assert report.summary_path.read_text(encoding="utf-8").startswith("# RepoEvidence 离线评测")
    assert "RepositoryScanner" not in report.summary_path.read_text(encoding="utf-8")
    assert json.loads(report.detail_path.read_text(encoding="utf-8"))["passed"] == 1
