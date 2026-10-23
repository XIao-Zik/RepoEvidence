"""Safe, deterministic, text-only scanning of one authorized local repository."""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .store import RepositoryPathError


@dataclass(frozen=True)
class ScannedFile:
    """A text file admitted to one repository evidence snapshot."""

    path: str
    content: str
    sha256: str
    bytes: int
    line_count: int
    language: str
    content_type: str


@dataclass(frozen=True)
class ExcludedFile:
    """A path intentionally withheld from indexing, with no content retained."""

    path: str
    reason: str


@dataclass(frozen=True)
class ScanResult:
    """The complete safe source manifest for one immutable collection build."""

    files: tuple[ScannedFile, ...]
    excluded: tuple[ExcludedFile, ...]
    manifest_sha256: str


class RepositoryScanner:
    """Walk a repository without following links or exposing likely secrets to the model."""

    max_file_bytes = 1_000_000
    excluded_directories = frozenset(
        {".git", ".venv", "venv", "node_modules", "dist", "build", "coverage", ".cache"}
    )
    sensitive_names = frozenset({".env", ".env.local", "credentials", "credentials.json"})
    sensitive_suffixes = frozenset({".pem", ".key", ".p12", ".pfx", ".crt", ".cer"})
    source_suffixes = frozenset({".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".java", ".rs"})
    document_suffixes = frozenset({".md", ".mdx", ".rst", ".txt"})
    config_suffixes = frozenset({".json", ".yaml", ".yml", ".toml", ".ini", ".conf"})
    log_suffixes = frozenset({".log"})
    language_by_suffix = {
        ".py": "python",
        ".ts": "typescript",
        ".tsx": "tsx",
        ".js": "javascript",
        ".jsx": "jsx",
        ".go": "go",
        ".java": "java",
        ".rs": "rust",
        ".md": "markdown",
        ".mdx": "markdown",
        ".rst": "restructuredtext",
        ".json": "json",
        ".yaml": "yaml",
        ".yml": "yaml",
        ".toml": "toml",
        ".ini": "ini",
        ".conf": "config",
        ".log": "log",
        ".txt": "text",
    }
    secret_pattern = re.compile(
        r"(?im)^\s*(?:api[_-]?key|access[_-]?token|secret|password)\s*[:=]\s*\S+"
    )

    def scan(self, root: Path) -> ScanResult:
        """Return sorted admitted files and non-sensitive exclusion metadata for ``root``."""
        root = root.resolve()
        if not root.is_dir():
            raise RepositoryPathError(
                "Repository path must identify an existing allowed root directory"
            )
        ignore_rules = self._ignore_rules(root)
        accepted: list[ScannedFile] = []
        excluded: list[ExcludedFile] = []
        for directory, names, filenames in os.walk(root, topdown=True, followlinks=False):
            current = Path(directory)
            retained_names: list[str] = []
            for name in sorted(names):
                candidate = current / name
                relative = self._relative(root, candidate)
                if candidate.is_symlink():
                    excluded.append(ExcludedFile(relative, "symbolic link"))
                elif self._is_ignored(relative, ignore_rules, directory=True):
                    excluded.append(ExcludedFile(relative, "user ignore rule"))
                elif name in self.excluded_directories:
                    excluded.append(ExcludedFile(relative, "excluded directory"))
                else:
                    retained_names.append(name)
            names[:] = retained_names
            for filename in sorted(filenames):
                candidate = current / filename
                relative = self._relative(root, candidate)
                if candidate.is_symlink():
                    excluded.append(ExcludedFile(relative, "symbolic link"))
                    continue
                if self._is_ignored(relative, ignore_rules, directory=False):
                    excluded.append(ExcludedFile(relative, "user ignore rule"))
                    continue
                decision = self._read_file(candidate, relative)
                if isinstance(decision, ExcludedFile):
                    excluded.append(decision)
                else:
                    accepted.append(decision)
        accepted.sort(key=lambda item: item.path)
        excluded.sort(key=lambda item: item.path)
        manifest = [
            {
                "path": item.path,
                "sha256": item.sha256,
                "bytes": item.bytes,
                "line_count": item.line_count,
                "language": item.language,
                "content_type": item.content_type,
            }
            for item in accepted
        ]
        digest = hashlib.sha256(
            json.dumps(manifest, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode(
                "utf-8"
            )
        ).hexdigest()
        return ScanResult(tuple(accepted), tuple(excluded), digest)

    @staticmethod
    def _ignore_rules(root: Path) -> tuple[str, ...]:
        """Read additive, line-oriented glob exclusions without Git ignore negation semantics."""
        rule_file = root / ".repoevidenceignore"
        if not rule_file.is_file() or rule_file.is_symlink() or rule_file.stat().st_size > 65_536:
            return ()
        try:
            lines = rule_file.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError:
            return ()
        return tuple(
            line.strip() for line in lines if line.strip() and not line.lstrip().startswith("#")
        )

    @staticmethod
    def _is_ignored(relative: str, rules: tuple[str, ...], *, directory: bool) -> bool:
        path = PurePosixPath(relative)
        for rule in rules:
            if rule.startswith("!"):
                continue
            if rule.endswith("/"):
                prefix = rule.rstrip("/")
                if relative == prefix or relative.startswith(prefix + "/"):
                    return True
            elif not directory and path.match(rule):
                return True
        return False

    def resolve_under_root(self, root: Path, relative_path: str) -> Path:
        """Resolve a read request while rejecting traversal and symlink escapes."""
        normalized = self._normalize_relative_path(relative_path)
        root = root.resolve()
        candidate = (root / normalized).resolve()
        if not candidate.is_relative_to(root):
            raise RepositoryPathError("Repository path escapes its allowed root")
        return candidate

    def _read_file(self, path: Path, relative: str) -> ScannedFile | ExcludedFile:
        name = path.name.casefold()
        suffix = path.suffix.casefold()
        if (
            name in self.sensitive_names
            or name.startswith(".env")
            or suffix in self.sensitive_suffixes
        ):
            return ExcludedFile(relative, "sensitive filename")
        size = path.stat().st_size
        if size > self.max_file_bytes:
            return ExcludedFile(relative, "file exceeds size limit")
        raw = path.read_bytes()
        if b"\x00" in raw:
            return ExcludedFile(relative, "binary content")
        try:
            content = raw.decode("utf-8")
        except UnicodeDecodeError:
            return ExcludedFile(relative, "non-UTF-8 content")
        if "-----BEGIN " in content or self.secret_pattern.search(content):
            return ExcludedFile(relative, "sensitive content")
        content_type = self._content_type(suffix)
        if content_type is None:
            return ExcludedFile(relative, "unsupported file type")
        return ScannedFile(
            path=relative,
            content=content,
            sha256=hashlib.sha256(raw).hexdigest(),
            bytes=len(raw),
            line_count=len(content.splitlines()) or (1 if content else 0),
            language=self.language_by_suffix.get(suffix, "text"),
            content_type=content_type,
        )

    def _content_type(self, suffix: str) -> str | None:
        if suffix in self.source_suffixes:
            return "source"
        if suffix in self.document_suffixes:
            return "document"
        if suffix in self.config_suffixes:
            return "config"
        if suffix in self.log_suffixes:
            return "log"
        return None

    @staticmethod
    def _normalize_relative_path(value: str) -> str:
        if not value or "\\" in value:
            raise RepositoryPathError("Repository path must be a non-empty POSIX relative path")
        path = PurePosixPath(value)
        if path.is_absolute() or ".." in path.parts or path == PurePosixPath("."):
            raise RepositoryPathError("Repository path must be relative to its allowed root")
        return path.as_posix()

    @staticmethod
    def _relative(root: Path, candidate: Path) -> str:
        return candidate.relative_to(root).as_posix()
