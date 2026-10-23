from __future__ import annotations

from pathlib import Path

from repo_evidence.evaluation.index_benchmark import run_index_benchmark


def test_index_benchmark_reports_full_and_incremental_samples(tmp_path: Path) -> None:
    report = run_index_benchmark(tmp_path, samples=3)

    assert report.full.samples == 3
    assert report.incremental.samples == 3
    assert report.full.median_ms >= 0
    assert report.incremental.median_ms >= 0
    assert report.speedup_ratio >= 0
    assert report.fixture_files >= 100
    assert report.fixture_chunks >= 100
