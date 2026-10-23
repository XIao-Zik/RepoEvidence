"""Load RepoEvidence configuration and start the local HTTP service."""

import argparse
import logging
from pathlib import Path

import uvicorn
from fastapi import FastAPI

from repo_evidence.bootstrap import create_application
from repo_evidence.core.config import Settings, load_settings


class Arguments(argparse.Namespace):
    """命令行选择覆盖环境配置。"""

    config: Path | None = None


def configured_runtime(args: Arguments) -> Settings:
    """Load the one product configuration; the application has no stage selector."""
    return load_settings(args.config)


def app_factory() -> FastAPI:
    """给 `uvicorn --factory` 用的入口：配置全部来自环境变量，便于容器里热重载。

    `main` 走参数解析后直接持有 app 对象，没法配合 --reload（它要求导入字符串）。
    两条路径共用 configured_runtime，确保本地目录授权的解析口径一致。
    """
    settings = configured_runtime(Arguments())
    logging.basicConfig(level=settings.log_level)
    return create_application(settings)


def main() -> None:
    """Start the single-process local RepoEvidence service."""
    parser = argparse.ArgumentParser(description="RepoEvidence local code evidence assistant")
    parser.add_argument("--config", type=Path, default=None)
    args = parser.parse_args(namespace=Arguments())
    try:
        settings = configured_runtime(args)
        app = create_application(settings)
    except ValueError as error:
        parser.error(str(error))
    logging.basicConfig(level=settings.log_level)
    uvicorn.run(app, host=settings.host, port=settings.port, workers=1, timeout_graceful_shutdown=5)
