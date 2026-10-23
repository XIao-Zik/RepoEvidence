# RepoEvidence

RepoEvidence 是一个本地优先的代码库证据问答与只读排障助手。用户选择一个被授权的本地仓库，系统安全扫描代码、建立带行号的不可变索引快照，并让 Agent 基于该快照回答问题。

它不读取授权目录之外的文件，不执行仓库命令，也不直接改写你的代码。

## 核心能力

- 登记并索引一个本地代码仓库；浏览器只提交“授权根目录 ID + 相对路径”。
- 检索结果携带仓库相对路径与精确行号，可回看原始证据片段。
- 每个会话绑定一个已就绪的索引快照，保证回答可复现。
- 每次提问及工具读文件前都复核文件清单；若代码变更，索引会标记为过期并拒绝混用新旧证据。
- 用户确认刷新后创建新的索引快照；旧会话仍保留旧版本的证据链。

完整功能说明、代码地图和可安全修改的位置见 [docs/PROJECT_GUIDE.md](docs/PROJECT_GUIDE.md)。面试演示步骤见 [docs/DEMO.md](docs/DEMO.md)。

## 快速开始（WSL2 / Linux / macOS）

1. 在 WSL2、Linux 或 macOS 中克隆仓库并进入根目录。
2. 复制环境文件：`cp .env.example .env`。
3. 在 `.env` 中填写：
   - `REPO_EVIDENCE_MODEL_API_KEY`：OpenAI 兼容聊天模型的密钥；
   - `REPO_EVIDENCE_ALLOWED_ROOT_PATH`：允许索引的代码仓库父目录，例如 `/home/you/projects`。
4. 执行 `make up`。首次运行会构建镜像、启动 PostgreSQL 与 Ollama，并下载嵌入模型。
5. 打开 `http://127.0.0.1:5173`，登记该目录下的一个相对路径，例如 `my-service`，创建索引后再创建会话。

服务只将 `REPO_EVIDENCE_ALLOWED_ROOT_PATH` 以只读方式挂载给后端。不要把你的整个 Home 目录或包含密钥的目录设为授权根。

如果仍在运行旧项目，可在 `.env` 设置 `REPO_EVIDENCE_POSTGRES_PORT=55433`、`REPO_EVIDENCE_BACKEND_PORT=18000`、`REPO_EVIDENCE_FRONTEND_PORT=15173`；之后从 `http://127.0.0.1:15173` 打开新项目。这样不需要停止旧容器。

## 常用命令

```bash
make up       # 构建并启动前后端、数据库和嵌入服务
make down     # 停止服务，保留 Docker 数据卷
make logs     # 查看后端与前端日志
make test     # 运行后端测试
make quality  # 后端质量检查、前端格式/测试/构建
make eval     # 运行不调用模型的开发者 RAG 证据评测
make benchmark-index # 运行固定 fixture 的全量/增量索引性能基准，不调用模型
# 仅在服务已启动、存在唯一 ready 仓库时运行；会产生真实模型调用费用
REPO_EVIDENCE_RUN_ONLINE_EVAL=1 make eval-online
make ci       # 运行质量门禁与离线评测（本地与 GitHub Actions 共用）
```

## 索引范围、旧快照与交付

- 想排除仓库中的生成物或测试夹具时，可在**被索引仓库根目录**新增 `.repoevidenceignore`；每行一个 POSIX glob，目录以 `/` 结尾，例如 `coverage/`、`*.log`。它只会减少索引范围，不能取消系统默认的密钥、依赖和二进制排除规则。
- “清理旧快照”会先显示候选项，再要求显式确认；仅删除失败或已替换、且没有被历史会话引用的快照。当前快照与历史会话的证据均不会被清理。证据详情会说明它是当前快照还是因历史会话而保留。
- 离线评测是开发者质量门禁，不出现在用户问答流程中；详细 JSON 写入被忽略的 `artifacts/evaluation/`，可提交的汇总写入 `docs/quality/`。
- `make benchmark-index` 固定运行 3 次全量和 3 次单文件增量扫描/分块基准，使用本地生成的 118 文件 fixture 和确定性哈希写入模拟，不读用户代码仓库，也不调用网络模型。
- `REPO_EVIDENCE_RUN_ONLINE_EVAL=1 make eval-online` 是手动触发的真实 Agent 测评：它运行 30 条带人工证据范围的题目，先确认目标仓库和当前快照均为 ready，再记录严格任务通过数、引用准确率/覆盖率和端到端 P50/P95。原始问题与回答只保存在被忽略的本地工件；`docs/quality/resume-evaluation.md` 仅包含聚合数据和局限性，不能把该结果表述为跨仓库通用准确率。

首次交付到私有 GitHub 仓库时，在已登录 GitHub CLI 的 WSL 终端依次执行：

```bash
make github-init       # 创建私有仓库、添加 repoevidence，并首次推送；不会改动来源 origin
make deliver DRY_RUN=1 # 实际跑 make ci，只预览提交/推送命令
make deliver           # 再次输入 PUSH 后才会提交并推送 repoevidence/main
```

自动交付会拒绝 `.env*`、`artifacts/` 和 `var/` 路径；请在运行前自行检查将要提交的内容。

若你要在宿主机运行后端，请先在 `backend/` 中用 `uv sync` 建立虚拟环境，再执行：

```bash
REPO_EVIDENCE_CONFIG=backend/configs/local.toml \
REPO_EVIDENCE_ALLOWED_ROOTS='[{"id":"projects","path":"/home/you/projects"}]' \
repo-evidence
```

还需要设置 `.env` 中的模型、嵌入模型和 PostgreSQL 连接变量。日常开发更建议使用 `make up`，避免宿主机与容器的服务地址不一致。

## 代码变更时会发生什么

RepoEvidence 不会继续用旧索引冒充最新代码：

```text
工作区代码变更 → 扫描 SHA-256 清单 → 标记当前快照 stale
→ 阻止新问题进入旧快照 → 用户确认刷新 → 构建并发布新快照
```

旧快照不会被覆盖；已经完成的会话仍可审计。详情见 [docs/PROJECT_GUIDE.md](docs/PROJECT_GUIDE.md#代码变更与快照刷新)。
