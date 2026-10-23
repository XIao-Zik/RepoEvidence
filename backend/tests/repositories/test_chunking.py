from __future__ import annotations

from repo_evidence.repositories.chunking import chunk_file
from repo_evidence.repositories.scanner import ScannedFile


def scanned(path: str, content: str, language: str, content_type: str) -> ScannedFile:
    return ScannedFile(
        path=path,
        content=content,
        sha256="a" * 64,
        bytes=len(content.encode()),
        line_count=len(content.splitlines()),
        language=language,
        content_type=content_type,
    )


def test_python_function_chunks_preserve_exact_inclusive_line_ranges() -> None:
    chunks = chunk_file(
        scanned("src/auth.py", "import os\n\ndef login():\n    return True\n", "python", "source"),
        max_lines=80,
    )

    assert [
        (chunk.source_locator["start_line"], chunk.source_locator["end_line"]) for chunk in chunks
    ] == [
        (1, 2),
        (3, 4),
    ]
    assert chunks[1].source_locator["symbol"] == "login"


def test_python_chunking_omits_whitespace_only_ranges_between_symbols() -> None:
    chunks = chunk_file(
        scanned(
            "src/auth.py",
            "def login():\n    return True\n\n\ndef logout():\n    return True\n",
            "python",
            "source",
        ),
        max_lines=80,
    )

    assert [(chunk.chunk_no, chunk.content) for chunk in chunks] == [
        (1, "def login():\n    return True\n"),
        (2, "def logout():\n    return True\n"),
    ]


def test_markdown_chunks_at_headings_without_dropping_lines() -> None:
    chunks = chunk_file(
        scanned("README.md", "# Install\ntext\n# Run\ntext\n", "markdown", "document"),
        max_lines=80,
    )

    assert [
        (chunk.source_locator["start_line"], chunk.source_locator["end_line"]) for chunk in chunks
    ] == [
        (1, 2),
        (3, 4),
    ]
    assert [chunk.content for chunk in chunks] == ["# Install\ntext\n", "# Run\ntext\n"]
