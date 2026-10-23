# RepoEvidence 本地开发命令。请在 WSL2、Linux 或 macOS 的仓库根目录执行。

.DEFAULT_GOAL := help

VENV := backend/.venv/bin
CONFIG := backend/configs/local.toml
BACKEND_RUN := docker compose run --rm --no-deps -T -v "$(CURDIR):/app" backend
FRONTEND_RUN := docker compose run --rm --no-deps -T frontend
GITHUB_REPO ?= repo-evidence
GITHUB_REMOTE ?= repoevidence

.PHONY: help up down logs ps test frontend-test frontend-build quality eval benchmark-index eval-online evaluate-resume ci github-init deliver db-up db-setup run

help: ## 显示可用命令

	@awk 'BEGIN { FS = ":.*## " } /^[a-zA-Z0-9_-]+:.*## / { printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2 }' $(MAKEFILE_LIST)

up: ## 构建并后台启动完整本地服务；需先配置 .env

	docker compose up --build -d

down: ## 停止服务，保留 PostgreSQL 与 Ollama 数据卷

	docker compose down

logs: ## 持续查看后端与前端日志

	docker compose logs -f backend frontend

ps: ## 查看服务运行状态

	docker compose ps

test: ## 在容器中运行所有后端测试，不依赖宿主机虚拟环境

	$(BACKEND_RUN) /opt/venv/bin/python -m pytest backend/tests -q

frontend-test: ## 在容器中运行前端 Vitest 测试

	$(FRONTEND_RUN) npm test -- --run

frontend-build: ## 在容器中构建前端生产包

	$(FRONTEND_RUN) npm run build

quality: ## 在容器中运行后端 Ruff、测试，以及前端格式、测试与构建

	$(BACKEND_RUN) /opt/venv/bin/ruff check backend/src backend/tests backend/migrations

	$(BACKEND_RUN) /opt/venv/bin/ruff format --check backend/src backend/tests backend/migrations

	$(BACKEND_RUN) /opt/venv/bin/python -m pytest backend/tests -q

	$(FRONTEND_RUN) npm run format:check

	$(FRONTEND_RUN) npm test -- --run

	$(FRONTEND_RUN) npm run build

eval: ## 运行不调用在线模型的确定性证据评测

	$(BACKEND_RUN) /opt/venv/bin/python -m repo_evidence.evaluation.runner

benchmark-index: ## 运行不调用模型的固定 fixture 索引性能基准

	$(BACKEND_RUN) /opt/venv/bin/python -m repo_evidence.evaluation.index_benchmark

eval-online: ## 运行显式授权的真实 Agent 评测；需 REPO_EVIDENCE_RUN_ONLINE_EVAL=1

	@test "$(REPO_EVIDENCE_RUN_ONLINE_EVAL)" = "1" || (echo "Set REPO_EVIDENCE_RUN_ONLINE_EVAL=1"; exit 2)
	$(BACKEND_RUN) /opt/venv/bin/python -m repo_evidence.evaluation.online_runner

evaluate-resume: eval benchmark-index eval-online ## 手动生成完整简历评测报告

ci: quality eval ## 运行本地与 GitHub 共用的离线质量门禁

github-init: ## 创建名为 repoevidence 的私有远程，不改动来源 origin

	gh auth status && gh repo create $(GITHUB_REPO) --private --source . --remote $(GITHUB_REMOTE) --push

deliver: ## 检查、确认后提交并推送 repoevidence/main；DRY_RUN=1 只预览

	python3 -m scripts.delivery.deliver --remote $(GITHUB_REMOTE) $(if $(filter 1,$(DRY_RUN)),--dry-run,)

db-up: ## 仅启动 PostgreSQL

	docker compose up -d --wait postgres

db-setup: ## 初始化向量库表和最小数据库权限

	docker compose run --rm database-init

run: ## 在宿主机启动后端；需自行设置模型、数据库和允许根目录变量

	REPO_EVIDENCE_CONFIG=$(CONFIG) $(VENV)/repo-evidence
