from pathlib import Path

from scripts.delivery.deliver import blocked_paths, parse_args


def test_delivery_blocks_local_secrets_and_artifacts() -> None:
    assert blocked_paths(
        [
            Path(".env"),
            Path(".env.local"),
            Path("var/live/state.sqlite"),
            Path("artifacts/evaluation/raw.json"),
            Path("README.md"),
        ]
    ) == [
        ".env",
        ".env.local",
        "var/live/state.sqlite",
        "artifacts/evaluation/raw.json",
    ]


def test_delivery_defaults_to_the_separate_private_remote() -> None:
    assert parse_args([]).remote == "repoevidence"
