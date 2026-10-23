# RepoEvidence

RepoEvidence 是一个本地优先的代码库证据问答与只读排障助手。它在用户授权的目录内扫描代码、构建带行号的不可变索引快照，并让 Agent 仅基于该快照回答问题。

项目不执行被索引仓库的命令，不修改用户代码，也不会读取授权目录以外的文件。

## 项目结构

```text
backend/
└── src/repo_evidence/    # 索引、快照、检索、Agent 工作流与 HTTP API
frontend/                 # React + Vite 用户界面
infra/                    # 后端、前端与数据库镜像
scripts/delivery/         # 发布前检查与交付脚本
docs/                     # 项目指南、演示与质量报告
evaluation/               # 离线与在线评测题集
compose.yaml              # 本地完整服务编排
Makefile                  # 开发、测试、评测与运维入口
```

## 项目功能

- 登记授权根目录下的本地仓库，只接受“授权根目录 ID + 相对路径”，避免任意路径读取。
- 扫描代码并建立带文件路径、行号和内容摘要的不可变索引快照。
- 使用证据片段回答代码问题；每条结论可回看对应的仓库相对路径与精确行号。
- 在提问和读文件前核对文件清单：仓库变更会使快照过期，阻止新旧证据混用。
- 用户确认后创建新快照；历史会话继续保留其原有证据链，便于审计。
- 提供离线评测、索引性能基准、GitHub Actions 质量门禁和前端证据查看界面。

## 安装与运行

### 环境要求

- WSL2、Linux 或 macOS
- Docker Desktop（WSL2 使用时需启用对应发行版集成）
- 一个 OpenAI 兼容聊天模型的 API 密钥
- 可被只读授权的本地代码仓库父目录

```bash
git clone https://github.com/XIao-Zik/RepoEvidence.git
cd RepoEvidence
cp .env.example .env
```

编辑 `.env`，至少填写以下配置：

```dotenv
REPO_EVIDENCE_MODEL_API_KEY=your_api_key
REPO_EVIDENCE_ALLOWED_ROOT_PATH=/home/you/projects
```

`REPO_EVIDENCE_ALLOWED_ROOT_PATH` 应仅指向需要分析项目的父目录；不要授权整个 Home 目录或包含密钥的目录。

启动完整服务：

```bash
make up
```

首次启动会构建镜像、启动 PostgreSQL 与 Ollama，并下载嵌入模型。完成后访问：

- 前端：<http://127.0.0.1:5173>
- 后端 API：<http://127.0.0.1:8000>

常用命令：

```bash
make down             # 停止服务，保留数据卷
make logs             # 查看服务日志
make test             # 运行后端测试
make quality          # 运行质量检查、测试与前端构建
make eval             # 运行离线证据评测
make benchmark-index  # 运行索引性能基准
make ci               # 运行质量检查与离线评测
```

## 下一步方向

- 扩展更多语言、构建系统和超大仓库的增量索引能力。
- 增加更丰富的代码审查问题集与跨仓库证据评测。
- 在保持只读与证据可追溯边界的前提下，支持 PR/Diff 专项审查。
- 增加团队协作、CI 集成与可配置的仓库治理策略。

更多实现细节见 [项目指南](docs/PROJECT_GUIDE.md)，演示流程见 [演示文档](docs/DEMO.md)，质量数据见 [docs/quality/](docs/quality/)。