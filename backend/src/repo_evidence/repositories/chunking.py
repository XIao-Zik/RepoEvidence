"""Line-preserving chunks for repository code, documents, configuration, and logs."""

from __future__ import annotations

import ast
from collections.abc import Iterable

from repo_evidence.core.json import JsonObject
from repo_evidence.core.knowledge.contracts import Chunk

from .scanner import ScannedFile


def chunk_file(file: ScannedFile, max_lines: int = 120) -> list[Chunk]:
    """Create ordered chunks with inclusive source lines and no dropped source text."""
    if max_lines < 1:
        raise ValueError("max_lines must be positive")
    lines = file.content.splitlines(keepends=True)
    if not lines:
        return []
    if file.language == "python" and file.content_type == "source":
        ranges = _python_ranges(file.content, len(lines))
    elif file.language == "markdown" and file.content_type == "document":
        ranges = _markdown_ranges(lines)
    else:
        ranges = [(1, len(lines), None)]
    expanded: list[tuple[int, int, str | None]] = []
    for start, end, symbol in ranges:
        expanded.extend(_split_range(start, end, symbol, max_lines))
    nonempty = [
        (start, end, symbol)
        for start, end, symbol in expanded
        if "".join(lines[start - 1 : end]).strip()
    ]
    return [
        Chunk(
            chunk_no=index,
            content="".join(lines[start - 1 : end]),
            source_locator=_locator(file, start, end, symbol),
        )
        for index, (start, end, symbol) in enumerate(nonempty, start=1)
    ]


def _python_ranges(content: str, line_count: int) -> list[tuple[int, int, str | None]]:
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return [(1, line_count, None)]
    boundaries = [
        (node.lineno, node.end_lineno or node.lineno, node.name)
        for node in tree.body
        if isinstance(node, (ast.AsyncFunctionDef, ast.ClassDef, ast.FunctionDef))
    ]
    if not boundaries:
        return [(1, line_count, None)]
    ranges: list[tuple[int, int, str | None]] = []
    cursor = 1
    for start, end, symbol in boundaries:
        if cursor < start:
            ranges.append((cursor, start - 1, None))
        ranges.append((start, end, symbol))
        cursor = end + 1
    if cursor <= line_count:
        ranges.append((cursor, line_count, None))
    return ranges


def _markdown_ranges(lines: list[str]) -> list[tuple[int, int, str | None]]:
    headings = [index for index, line in enumerate(lines, start=1) if line.startswith("#")]
    if not headings:
        return [(1, len(lines), None)]
    ranges: list[tuple[int, int, str | None]] = []
    if headings[0] > 1:
        ranges.append((1, headings[0] - 1, None))
    for position, start in enumerate(headings):
        end = headings[position + 1] - 1 if position + 1 < len(headings) else len(lines)
        symbol = lines[start - 1].lstrip("#").strip() or None
        ranges.append((start, end, symbol))
    return ranges


def _split_range(
    start: int, end: int, symbol: str | None, max_lines: int
) -> Iterable[tuple[int, int, str | None]]:
    for part_start in range(start, end + 1, max_lines):
        yield part_start, min(part_start + max_lines - 1, end), symbol


def _locator(file: ScannedFile, start: int, end: int, symbol: str | None) -> JsonObject:
    locator: JsonObject = {
        "path": file.path,
        "start_line": start,
        "end_line": end,
        "language": file.language,
        "content_type": file.content_type,
    }
    if symbol is not None:
        locator["symbol"] = symbol
    return locator
