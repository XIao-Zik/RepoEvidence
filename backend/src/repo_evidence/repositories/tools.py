"""Bounded read-only access to the exact repository version selected for an investigation."""

from __future__ import annotations

from dataclasses import dataclass

from .scanner import RepositoryScanner, ScannedFile
from .store import RepositoryPathError, RepositoryStore


class RepositoryToolError(ValueError):
    """A user/model tool argument is unsafe, stale, or cannot be served from current evidence."""


@dataclass(frozen=True)
class FileReadResult:
    """A bounded line range plus the content hash used to verify its current source version."""

    path: str
    start_line: int
    end_line: int
    content: str
    source_sha256: str


@dataclass(frozen=True)
class DirectoryResult:
    """A policy-filtered, depth-bounded directory view with no file contents."""

    path: str
    entries: tuple[str, ...]


class RepositoryTools:
    """Use scans as the single policy gate before reading any selected repository file."""

    max_read_lines = 200

    def __init__(self, registry: RepositoryStore, scanner: RepositoryScanner | None = None) -> None:
        self.registry = registry
        self.scanner = scanner or RepositoryScanner()

    def read_file(
        self, repository_id: str, path: str, start_line: int, end_line: int
    ) -> FileReadResult:
        """Read a bounded range only if the worktree still matches the active evidence snapshot."""
        if type(start_line) is not int or type(end_line) is not int or start_line < 1:
            raise RepositoryToolError("Line range must use positive integers")
        if end_line < start_line:
            raise RepositoryToolError("End line must not precede start line")
        repository, root, scan = self._current_scan(repository_id)
        try:
            self.scanner.resolve_under_root(root, path)
        except RepositoryPathError as error:
            raise RepositoryToolError(str(error)) from error
        if repository.status == "stale":
            raise RepositoryToolError(
                "Repository changed; refresh its evidence index before reading"
            )
        selected = next((item for item in scan.files if item.path == path), None)
        if selected is None:
            raise RepositoryToolError("Requested path is not readable under the repository policy")
        return self._line_window(selected, start_line, end_line)

    def is_current(self, repository_id: str) -> bool:
        """Scan once before an answer and retain the active collection while exposing staleness."""
        repository, _root, _scan = self._current_scan(repository_id)
        return repository.status == "ready"

    def is_current_collection(self, repository_id: str, collection_id: str) -> bool:
        """Confirm that a session still points to the repository's active evidence snapshot."""
        repository = self.registry.get_repository(repository_id)
        return repository.status == "ready" and repository.current_collection_id == collection_id

    def list_directory(
        self, repository_id: str, path: str = ".", depth: int = 2
    ) -> DirectoryResult:
        """List only indexed-safe entries below one current repository directory."""
        if type(depth) is not int or not 1 <= depth <= 3:
            raise RepositoryToolError("Directory depth must be an integer from 1 to 3")
        if not isinstance(path, str) or not path:
            raise RepositoryToolError("Directory path must be a non-empty relative path")
        repository, root, scan = self._current_scan(repository_id)
        if repository.status == "stale":
            raise RepositoryToolError(
                "Repository changed; refresh its evidence index before listing files"
            )
        if path == ".":
            normalized, target = ".", root
        else:
            try:
                target = self.scanner.resolve_under_root(root, path)
            except RepositoryPathError as error:
                raise RepositoryToolError(str(error)) from error
            if not target.is_dir():
                raise RepositoryToolError("Requested path is not a readable directory")
            normalized = target.relative_to(root).as_posix()
        prefix = "" if normalized == "." else normalized + "/"
        entries: set[str] = set()
        for item in scan.files:
            if not item.path.startswith(prefix):
                continue
            parts = item.path.removeprefix(prefix).split("/")
            if len(parts) <= depth:
                entries.add("/".join(parts))
            else:
                entries.add("/".join(parts[:depth]) + "/")
        return DirectoryResult(normalized, tuple(sorted(entries)))

    def _current_scan(self, repository_id: str):
        repository = self.registry.get_repository(repository_id)
        if repository.current_collection_id is None:
            raise RepositoryToolError("Repository has no ready evidence index")
        root = self.registry.repository_path(repository)
        scan = self.scanner.scan(root)
        snapshot = self.registry.get_snapshot_for_collection(
            repository.id, repository.current_collection_id
        )
        repository = self.registry.set_current_snapshot_stale(
            repository.id, scan.manifest_sha256 != snapshot.manifest_sha256
        )
        return repository, root, scan

    def _line_window(self, file: ScannedFile, start_line: int, end_line: int) -> FileReadResult:
        lines = file.content.splitlines(keepends=True)
        if start_line > len(lines):
            raise RepositoryToolError("Requested line range is outside the file")
        bounded_end = min(end_line, start_line + self.max_read_lines - 1, len(lines))
        return FileReadResult(
            path=file.path,
            start_line=start_line,
            end_line=bounded_end,
            content="".join(lines[start_line - 1 : bounded_end]),
            source_sha256=file.sha256,
        )
