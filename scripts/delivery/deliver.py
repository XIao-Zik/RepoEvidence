"""Run local quality checks, then optionally commit and push safe project files."""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

BLOCKED_PREFIXES = ("artifacts/", "var/")


def blocked_paths(paths: list[Path]) -> list[str]:
    """Return paths that must never be staged by automated delivery."""
    return [
        path.as_posix()
        for path in paths
        if path.as_posix() == ".env"
        or path.as_posix().startswith(".env.")
        or path.as_posix().startswith(BLOCKED_PREFIXES)
    ]


def run(command: list[str], *, root: Path, dry_run: bool = False) -> None:
    print("+", " ".join(command))
    if not dry_run:
        subprocess.run(command, check=True, cwd=root)


def changed_paths(root: Path) -> list[Path]:
    """List tracked and non-ignored untracked files that automated delivery would stage."""
    tracked = subprocess.check_output(
        ["git", "-C", str(root), "diff", "--name-only", "HEAD"], text=True
    ).splitlines()
    untracked = subprocess.check_output(
        ["git", "-C", str(root), "ls-files", "--others", "--exclude-standard"],
        text=True,
    ).splitlines()
    return [Path(path) for path in sorted(set(tracked) | set(untracked))]


def parse_args(arguments: list[str] | None = None) -> argparse.Namespace:
    """Keep the source remote separate from the user's private delivery remote."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--message", default="chore: deliver validated RepoEvidence build"
    )
    parser.add_argument("--remote", default="repoevidence")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(arguments)


def main() -> None:
    args = parse_args()
    root = Path(__file__).resolve().parents[2]
    run(["make", "ci"], root=root)
    staged = changed_paths(root)
    blocked = blocked_paths(staged)
    if blocked:
        raise SystemExit("Refusing to deliver blocked paths: " + ", ".join(blocked))
    if args.dry_run:
        run(["git", "-C", str(root), "add", "-A"], root=root, dry_run=True)
        run(
            ["git", "-C", str(root), "commit", "-m", args.message],
            root=root,
            dry_run=True,
        )
        run(
            ["git", "-C", str(root), "push", args.remote, "HEAD:main"],
            root=root,
            dry_run=True,
        )
        return
    if input(f"Type PUSH to commit and push {args.remote}/main: ").strip() != "PUSH":
        raise SystemExit("Delivery cancelled")
    run(["git", "-C", str(root), "add", "-A"], root=root)
    run(["git", "-C", str(root), "commit", "-m", args.message], root=root)
    run(["git", "-C", str(root), "push", args.remote, "HEAD:main"], root=root)


if __name__ == "__main__":
    main()
