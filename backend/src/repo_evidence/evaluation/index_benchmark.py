"""Deterministic scanner/chunker performance benchmark with no model or network calls."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from repo_evidence.repositories.chunking import chunk_file
from repo_evidence.repositories.scanner import RepositoryScanner

from .online import percentile


@dataclass(frozen=True)
class BenchmarkSamples:
    samples: int
    median_ms: int
    p95_ms: int
    values_ms: tuple[int, ...]
    reused_files: int
    changed_files: int


@dataclass(frozen=True)
class IndexBenchmarkReport:
    fixture_files: int
    fixture_chunks: int
    full: BenchmarkSamples
    incremental: BenchmarkSamples
    speedup_ratio: float
    detail_path: Path | None
    summary_path: Path | None


def run_index_benchmark(
    work_dir: Path,
    *,
    samples: int = 3,
    artifact_dir: Path | None = None,
    summary_path: Path | None = None,
) -> IndexBenchmarkReport:
    """Time the same safe scanner/chunker path for full and one-file incremental inputs."""
    if samples < 1:
        raise ValueError("samples must be positive")
    fixture = work_dir / "repo-evidence-index-benchmark-fixture"
    fixture.mkdir(parents=True, exist_ok=True)
    scanner = RepositoryScanner()
    full_values: list[int] = []
    incremental_values: list[int] = []
    full_scan = None
    reused_files = 0
    changed_files = 0
    for _ in range(samples):
        _write_fixture(fixture)
        started = time.perf_counter()
        scan = scanner.scan(fixture)
        _simulate_vector_writes(scan.files)
        full_values.append(round((time.perf_counter() - started) * 1000))
        full_scan = scan
    assert full_scan is not None
    for _ in range(samples):
        _write_fixture(fixture)
        parent = scanner.scan(fixture)
        target = fixture / "src/service.py"
        target.write_text(
            target.read_text(encoding="utf-8") + "\n# incremental benchmark change\n",
            encoding="utf-8",
        )
        started = time.perf_counter()
        current = scanner.scan(fixture)
        before = {item.path: item.sha256 for item in parent.files}
        after = {item.path: item.sha256 for item in current.files}
        reusable = {path for path, digest in after.items() if before.get(path) == digest}
        changed = set(after) - reusable
        _simulate_vector_writes([item for item in current.files if item.path in changed])
        incremental_values.append(round((time.perf_counter() - started) * 1000))
        reused_files = len(reusable)
        changed_files = len(changed)
    fixture_chunks = sum(len(chunk_file(item)) for item in full_scan.files)
    full = _samples(full_values, reused_files=0, changed_files=len(full_scan.files))
    incremental = _samples(
        incremental_values, reused_files=reused_files, changed_files=changed_files
    )
    speedup = full.median_ms / incremental.median_ms if incremental.median_ms else 0.0
    report = IndexBenchmarkReport(
        fixture_files=len(full_scan.files),
        fixture_chunks=fixture_chunks,
        full=full,
        incremental=incremental,
        speedup_ratio=round(speedup, 2),
        detail_path=(artifact_dir / "index-benchmark.json") if artifact_dir else None,
        summary_path=summary_path,
    )
    if artifact_dir is not None:
        artifact_dir.mkdir(parents=True, exist_ok=True)
        report.detail_path.write_text(
            json.dumps(
                {
                    "platform": platform.platform(),
                    "python": sys.version.split()[0],
                    **asdict(report),
                    "detail_path": None,
                    "summary_path": None,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    if summary_path is not None:
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary_path.write_text(_markdown(report), encoding="utf-8")
    return report


def _samples(values: list[int], *, reused_files: int, changed_files: int) -> BenchmarkSamples:
    return BenchmarkSamples(
        samples=len(values),
        median_ms=percentile(values, 0.5),
        p95_ms=percentile(values, 0.95),
        values_ms=tuple(values),
        reused_files=reused_files,
        changed_files=changed_files,
    )


def _simulate_vector_writes(files: tuple[object, ...] | list[object]) -> None:
    for file in files:
        chunks = chunk_file(file)  # type: ignore[arg-type]
        for chunk in chunks:
            hashlib.sha256(chunk.content.encode("utf-8")).digest()


def _write_fixture(root: Path) -> None:
    files = {
        "src/service.py": "\n".join(
            f"def operation_{index}():\n    return {index}\n" for index in range(80)
        ),
        "src/worker.py": "\n".join(
            f"class Worker{index}:\n    enabled = True\n" for index in range(40)
        ),
        "web/client.ts": "\n".join(f"export const value{index} = {index};" for index in range(120)),
        "docs/architecture.md": "# Architecture\n\n" + ("Evidence snapshot design.\n" * 100),
        "config/app.toml": '[service]\nname = "benchmark"\nmode = "deterministic"\n',
        "logs/example.log": "INFO benchmark fixture\n" * 80,
    }
    for index in range(60):
        files[f"src/modules/module_{index:03d}.py"] = "\n".join(
            f"def module_{index}_operation_{operation}():\n    return {index + operation}\n"
            for operation in range(24)
        )
    for index in range(40):
        files[f"web/components/component_{index:03d}.ts"] = "\n".join(
            f"export const component{index}Value{value} = {index + value};" for value in range(40)
        )
    for index in range(12):
        files[f"docs/guide_{index:02d}.md"] = f"# Guide {index}\n\n" + (
            "Evidence snapshot benchmark text.\n" * 50
        )
    for relative, content in files.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")


def _markdown(report: IndexBenchmarkReport) -> str:
    return (
        "# RepoEvidence 索引性能基准\n\n"
        "此基准使用程序生成的固定 fixture、真实安全扫描与分块逻辑、确定性本地哈希写入模拟；"
        "不访问用户仓库、不调用模型或网络，因此不能外推为任意仓库的绝对耗时。\n\n"
        "| 指标 | 全量 | 单文件增量 |\n| --- | ---: | ---: |\n"
        f"| 采样次数 | {report.full.samples} | {report.incremental.samples} |\n"
        f"| 中位数 | {report.full.median_ms} ms | {report.incremental.median_ms} ms |\n"
        f"| P95 | {report.full.p95_ms} ms | {report.incremental.p95_ms} ms |\n"
        f"| 复用文件数 | {report.full.reused_files} | {report.incremental.reused_files} |\n"
        f"| 变更文件数 | {report.full.changed_files} | {report.incremental.changed_files} |\n\n"
        f"- Fixture：{report.fixture_files} 个文件，{report.fixture_chunks} 个 chunk\n"
        f"- 增量相对全量中位数加速比：{report.speedup_ratio:.2f}x\n"
    )


def main() -> None:
    """Write local detail and a safe index benchmark summary."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=int, default=3)
    parser.add_argument("--work-dir", type=Path, default=None)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[4]
    report = run_index_benchmark(
        args.work_dir or root / "artifacts/evaluation/work",
        samples=args.samples,
        artifact_dir=root / "artifacts/evaluation",
        summary_path=root / "docs/quality/index-benchmark.md",
    )
    print(
        json.dumps(
            {
                "fixture_files": report.fixture_files,
                "fixture_chunks": report.fixture_chunks,
                "full_median_ms": report.full.median_ms,
                "incremental_median_ms": report.incremental.median_ms,
                "speedup_ratio": report.speedup_ratio,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
