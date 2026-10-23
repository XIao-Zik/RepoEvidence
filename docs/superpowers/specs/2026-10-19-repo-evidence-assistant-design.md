# RepoEvidence 设计规格

## 1. 定位与目标

将当前 FlowPay Agentic RAG 教学仓库重构为 **RepoEvidence**：一个可由面试官在本地 Docker 环境启动、选择已授权本地代码仓库并获得可核查行号证据的代码问答与只读排障助手。

产品演示的主路径为：选择仓库、建立索引、提问、查看 `path:start_line-end_line` 证据、修改目标仓库文件、检测索引过期并刷新到新的证据版本。它不是教程，不显示 s1--s4，也不使用 FlowPay 数据包、支付模拟器、支付 SQL 或 Webhook 数据源。

## 2. 范围

### 2.1 本版本必须实现

- 单机、单用户、Docker Compose 部署；前端、FastAPI、PostgreSQL + pgvector 和 Ollama 均可本地运行。
- 管理员通过环境变量显式配置一个或多个只读允许根目录；容器中只挂载这些根目录。
- 用户从允许根目录选择一个仓库，创建仓库记录并触发索引。
- 安全扫描代码、配置、Markdown/文本技术文档和可选文本日志，按规则拒绝敏感、二进制、超大和依赖目录文件。
- 每个可检索块保存相对路径、文件 SHA-256、起止行、语言、内容类型和仓库快照身份；集合一旦 ready 即不可修改。
- 统一 Evidence Agent 可使用语义搜索、有限行文件读取、文档搜索、日志搜索和有限深度目录列表等只读工具。
- 回答要求区分已验证事实、合理假设、缺失信息和下一步；每个代码/文档结论都包含实际的文件行号引用。
- 会话保存仓库、collection、服务、环境、错误码、时间范围、已读证据等上下文；仓库或 collection 变更时使旧证据失效。
- 前端展示当前仓库、collection 版本、索引时间、索引状态、工具轨迹、引用和对应代码片段。
- 后端、前端及端到端测试验证路径隔离、引用准确性、索引版本和主要演示路径。

### 2.2 明确不做

- 多租户、登录、权限平台、远程 GitHub 同步、多仓库聚合。
- 自动编辑、提交代码、执行 Shell/Python/Git 任意命令。
- PDF OCR、图片日志、二进制文件解析或自动修复。
- 文件系统监听后自动索引；本版由“提问前变更检测 + 用户确认刷新”驱动。

### 2.3 修复建议与补丁边界

Agent 可以基于当前 collection 的证据生成**补丁建议预览**：它必须带有引用、统一 diff 和适用前提，供用户审阅或导出为 `.patch` 文件。该能力不具有写入、应用补丁、执行命令、提交 Git 或创建分支的权限。用户自行应用补丁后，下一次扫描会将工作树标记为 `stale`，并在确认刷新后创建新的证据快照。

## 3. 产品交互

1. 用户进入仓库页，从允许根目录选择仓库。
2. 页面显示目录、当前快照、文件数、collection ID、上次索引时间与状态；点击“创建索引”。
3. 后端扫描、过滤、分块、嵌入并发布不可变 collection；构建失败不影响最近一个 ready collection。
4. 用户在会话中提问。系统先检测工作树是否相对当前 collection 改变：未改变则运行 Agent；改变则标记当前索引过期并提示刷新。
5. 用户点击“刷新索引”后，系统创建一个新 collection。未改变文件可复用已存块/向量，新增、修改和删除文件形成新快照。
6. Agent 在工具预算内调查并返回结论、证据、排除项、下一步和不确定性。点击证据可查看该 collection 中的冻结片段，并可对照当前只读文件。
7. 历史回答保留其 collection ID；若该 collection 已过期，页面标明“历史证据，当前代码可能不同”，且不能当作当前事实复用。
8. 当用户要求修复方案时，Agent 只返回可审阅的统一 diff 预览和适用条件；用户自行应用后，系统按第 4--5 步检测并发布新快照。

## 4. 架构与模块边界

```text
React 前端
  RepositoryScreen / Chat / EvidenceViewer / Trace
                 |
FastAPI
  RepositoryService / IndexService / EvidenceWorkflow / SessionManager
                 |
  RepositoryScanner / Chunker / ReadOnlyRepositoryTools / KnowledgeClient
                 |
PostgreSQL + pgvector        Ollama Embedding       OpenAI-compatible Chat LLM
```

### 4.1 保留并改名的通用底座

- `core/agent/`：模型消息、工具协议、预算、追踪与回答接收机制。
- `core/chat/`、`core/event_log/`、`core/execution/`、`core/http/`：会话、后台运行、取消、SSE 和回放。
- `core/llm/`：OpenAI 兼容聊天模型、嵌入模型和向量空间身份校验。
- `core/knowledge/`：PostgreSQL + pgvector collection、document、passage 和检索。

Python 包从 `flowpay` 重命名为 `repo_evidence`，项目、Compose 服务、环境变量、标题和文案均移除 FlowPay/教学/s1--s4 术语。

### 4.2 新增领域模块

- `repositories/registry.py`：允许根目录、仓库注册、当前 collection、快照与过期状态。
- `repositories/scanner.py`：规范化路径、遍历、二进制与大小检测、敏感文件/目录规则、SHA-256 清单。
- `repositories/chunking.py`：源码结构优先且可回退到行窗口；Markdown 标题段落；日志时间/上下文窗口。
- `repositories/snapshots.py`：清单哈希、collection ID、变更集（新增/修改/删除）和状态转换。
- `repositories/tools.py`：受限 `search_repository`、`read_file`、`search_docs`、`search_logs`、`list_directory`。
- `repositories/patches.py`：将带行号证据的修复建议渲染为不可执行的统一 diff 预览，并验证它只涉及已授权仓库的相对路径。
- `workflows/evidence.py`：统一 Agent Loop 与面向代码排障的系统提示词，不依赖历史 s1--s4。

`core` 不依赖上述产品层；扫描、读取、分块和工具 schema 都由产品层实现，工作流只编排并通过明确接口使用它们。

## 5. 数据与索引设计

每次成功索引生成不可变 collection：

- `collection_id`：仓库稳定 ID、快照清单哈希前缀、索引策略版本和嵌入模型身份的组合。
- `repository_snapshot`：仓库相对路径、根目录标识、完整文件清单哈希、创建时间、父 collection ID。
- `document`：每个可索引文件一条记录；`source_path` 为 POSIX 相对路径，`source_sha256` 固定该版本内容。
- `passage.source.locator`：`path`、`start_line`、`end_line`、语言、内容类型、切块策略和可选结构名。

文件内容、路径或切块策略改变时绝不覆盖 ready collection。构建过程使用 `building` collection；只有嵌入、来源重验和引用回查全部通过才发布 `ready`。失败状态保留诊断信息，旧 ready collection 继续可用。

首次索引构建全部向量；刷新时扫描新清单，并把未改变文件的文档/块和已验证向量复制或复用到新 collection，只嵌入新增或内容哈希变化的块。删除文件不进入新 collection。

## 6. 安全模型

- 根目录由 `REPO_EVIDENCE_ALLOWED_ROOTS` 明确提供；每个路径 resolve 后必须是目录且工具输入只能解析为该根目录内的相对路径。
- 默认排除 `.git`、`.venv`、`venv`、`node_modules`、`dist`、`build`、`coverage`、缓存目录和二进制文件。
- 拒绝 `.env`、私钥/证书、凭据文件以及命中密钥文件名或内容特征的文件；拒绝原因可展示，正文不可落库或发送给模型。
- 限制单文件字节数、单文件行数、单次读取最大行数、目录列表深度与每轮工具调用数。
- 所有工具只读；没有 Shell、Python、写文件、Git 命令或未授权路径访问能力。
- 工具调用与拒绝均进入已有事件日志，便于前端轨迹与审计查看。

## 7. Agent 和会话行为

统一 Agent Loop 仍为 `think -> validate -> perform -> observe -> accept`。模型只能在声明的 JSON Schema 中选择工具；程序执行预算、去重、路径校验和结果裁剪。

系统提示词要求：

- 只把当前 collection 的代码、文档和日志工具结果称为“已验证事实”。
- 运行时无法由静态资料证明的结论标为“假设”或“不确定”。
- 无关键条件时结构化追问仓库、服务、环境、错误码、Trace ID、日志目录或时间范围，并说明用途。
- 输出固定栏目：结论、证据、排除项、下一步、不确定性。

会话上下文包含 `repository_id`、`collection_id`、当前服务/环境/异常类型/请求或 Trace ID/时间范围和已读证据键。切换仓库或 collection 时，旧 collection 的读取结果和结论不再被当作当前事实；历史消息保留版本标签但不注入当前 Agent 上下文。

## 8. 变更检测与索引刷新

每次提问前计算允许成本内的工作树清单指纹，并与当前 ready collection 的快照比较。

- 指纹相同：继续使用当前 collection。
- 指纹不同：将仓库标记 `stale`，显示新增/修改/删除统计；默认阻止以“当前代码”名义作答。
- 用户确认刷新：创建新 collection；成功后原子切换仓库的 current collection，并把旧 collection 标记 `superseded`。
- 用户选择查看历史：允许明确以旧 collection 进行只读回顾，但回答和界面必须带旧版本标签。
- 用户要求修复建议：仅可生成引用当前 collection 的补丁预览或 `.patch` 下载内容；该过程不写入仓库。用户应用补丁造成的任意文件变化，按同一 stale/refresh 流程处理。

不做后台 watcher，以避免编辑中间态、无预期的嵌入费用和难以解释的证据版本。

## 9. HTTP 与前端

新增 REST 资源：

- `GET /api/repositories/roots`：允许根目录及可选仓库候选项。
- `GET/POST /api/repositories`：列表、注册并选择仓库。
- `GET /api/repositories/{id}`：状态、快照和 collection 信息。
- `POST /api/repositories/{id}/scan`：检测变更而不写索引。
- `POST /api/repositories/{id}/indexes`：创建或刷新索引，返回后台运行标识。
- `GET /api/repositories/{id}/evidence/{passage}`：返回冻结证据片段与行号。
- `POST /api/repositories/{id}/patch-preview`：校验引用和相对路径后返回不可执行的统一 diff 预览；不应用补丁。

现有会话 API 增加 repository/collection 绑定；`/api/meta` 改为产品信息、模型和示例，而非阶段卡片。

前端保留会话导航、SSE、取消和轨迹。新增仓库选择/状态区、索引操作、过期提醒和 EvidenceViewer。轨迹默认展示工具调用、简短理由、结果摘要和最终结论，不展示模型内部思维链。

## 10. 验收与测试

### 后端单元/集成

- 路径规范化、符号链接逃逸、敏感文件、二进制文件、目录/文件大小限制。
- Python/TypeScript/Markdown/日志的切块及精确 `start_line/end_line` 回查。
- collection 发布不可变性、失败不切换当前版本、变更检测和增量刷新。
- 工具 schema、超出根目录和超预算/重复工具调用的拒绝。
- Agent 输出引用必须指向当前 collection 中真实的 passage；无证据结论被拒绝。
- HTTP 仓库 API、SSE 索引事件与会话 collection 失效。

### 前端

- 仓库状态、过期警告、刷新按钮、引用显示和代码片段展开。
- 旧 collection 回答显示历史标签；工具轨迹不暴露内部思维链。

### 演示端到端

使用测试夹具仓库：索引后询问函数/配置/异常位置，返回正确路径和行范围；修改一个文件，下一次询问检测过期；刷新后新回答引用新 collection 与新行范围。

## 11. 交付文档

- README：产品定位、前置条件、WSL2/Linux 快速启动、配置允许根目录、演示脚本与安全边界。
- `docs/PROJECT_GUIDE.md`：面向项目维护者解释模块职责、端到端数据流、功能入口、哪些位置适合继续改造、常见改造示例和验证命令。
- `docs/DEMO.md`：面试演示步骤、推荐提问和讲解要点。
