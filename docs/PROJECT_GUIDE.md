# RepoEvidence 项目功能与维护指南

本文帮助你以“使用者、面试讲解者、维护者”三个角度阅读 RepoEvidence。先从界面完整走一遍，再按下面的代码地图进入实现；不要一开始就从 LLM SDK 或数据库细节读起。

## 1. 它解决什么问题

RepoEvidence 面向“我需要理解或排查一个本地代码库，但不能相信没有出处的 AI 回答”的场景。它将当前仓库固定成一个可引用的索引快照，让 Agent 只能通过搜索与行范围读取取得证据，再围绕这些证据回答。

它的边界同样重要：单用户、本地授权仓库、只读分析。它不是 IDE 插件、代码执行器、Git 托管平台，也不会替用户应用修改。

## 2. 先按这个顺序体验功能

1. 在 `.env` 设置 `REPO_EVIDENCE_ALLOWED_ROOT_PATH`，启动 `make up`。
2. 在页面左侧选择允许根目录，并登记其中一个仓库的相对路径。
3. 点击“创建索引”，等待状态变为“当前证据可用”。
4. 新建会话，输入问题，例如“认证失败的入口在哪里？请列出相关文件和行号。”
5. 在回答中的证据入口查看文件片段、相对路径和行号；在运行轨迹查看工具调用摘要。
6. 修改被索引仓库的一个文件，再点击“扫描变更”或继续提问。
7. 页面将该仓库标记为“索引已过期”。点击“刷新索引”，等待新快照完成后创建新会话验证答案。
8. 若需要回收空间，点击“清理旧快照”，先核对候选列表后再确认删除；当前快照和任何历史会话引用的快照不会成为候选项。

## 3. 代码变更与快照刷新

每个索引快照保存当时文件的 SHA-256 清单。快照准备完成后不可修改。

```text
登记仓库
  → 扫描安全文件并记录清单
  → 分块、嵌入，生成 collection_id
  → 校验后发布 ready 快照
  → 会话绑定 repository_id + collection_id

代码发生变化
  → 比对当前清单与已发布快照
  → 记录 added / changed / deleted，状态变为 stale
  → 拒绝针对旧快照的新问答或文件读取
  → 用户确认刷新，创建新的 collection_id
  → 复用哈希未变文件的文档、分块与向量；仅重建改动或新增文件
```

这能避免典型的 RAG 错误：检索命中的是旧版本向量，模型却读取了新版本代码。历史会话仍可查看旧证据，但刷新完成后不能继续提出新问题；必须创建绑定新 collection 的会话，才会得到当前代码结论。

证据详情会标记“当前证据快照”或“历史会话绑定此快照”。前者服务新调查，后者是为了让旧回答的引用仍可复查；这也是受控清理必须保留它们的原因。

### 自定义排除规则

在被索引仓库根目录创建 `.repoevidenceignore`，每行写一个 POSIX glob。目录规则以 `/` 结尾，例如：

```text
coverage/
generated/
*.log
```

该文件只能增加排除项，不能重新纳入系统默认过滤的密钥、依赖目录、二进制或超大文件。

## 4. 架构地图

| 你关心的功能 | 主要位置 | 阅读重点 |
| --- | --- | --- |
| 授权根、登记仓库、快照状态 | `backend/src/repo_evidence/repositories/store.py` | SQLite 状态转换与根目录校验 |
| 文件排除与哈希扫描 | `repositories/scanner.py` | 不跟随软链接、排除密钥/二进制/依赖目录 |
| 带行号的代码块 | `repositories/chunking.py` | `path`、`start_line`、`end_line` 怎样保持准确 |
| 建索引与变更检测 | `repositories/indexer.py` | 新快照发布、失败回滚、旧集合保留 |
| Agent 的只读工具 | `repositories/tools.py`、`workflows/evidence.py` | 代码搜索、文档/日志分类检索、有限范围读取、有限深度目录列表与提问前二次检测 |
| 补丁预览校验 | `repositories/patches.py` | 只验证有证据的 unified diff，绝不写入工作区 |
| 证据质量评估 | `repositories/evaluation.py` | 拒绝“答案像是对的、但文件或行号引用错误”的结果 |
| 离线质量评测 | `evaluation/cases/`、`evaluation/runner.py` | 开发者门禁：不调用线上模型，检查引用准确率与覆盖率 |
| Agent 决策与回答 | `workflows/evidence.py` | 只能使用证据工具，不展示思维链 |
| HTTP API 和后台索引任务 | `core/http/app.py` | 登记、扫描、建索引、创建绑定会话 |
| 会话 / SSE / 运行轨迹 | `core/execution/`、`core/event_log/` | 一个会话如何保存并回放事件 |
| React 交互 | `frontend/src/components/repository-panel.tsx`、`hooks/use-repositories.ts` | 仓库状态、刷新按钮、会话选择 |
| 冻结证据查看 | `frontend/src/components/evidence-dialog.tsx`、`lib/session-api.ts` | 点击引用后只请求引用所属的索引快照，并显示精确行号；不读取工作树；旧会话可回看旧版本 |

## 5. 哪些地方适合你参与修改

### 低风险：适合先练手

- 调整中文文案、状态标签、空状态、按钮禁用提示：`frontend/src/components/repository-panel.tsx`。
- 新增问答示例或优化展示：`frontend/src/components/chat-panel.tsx`。
- 调整文件类型支持、最大文件大小、需要忽略的目录：扫描策略及对应测试。
- 调整分块大小和 Markdown/日志切分策略：`repositories/chunking.py` 与 `test_chunking.py`。
- 改善 README、演示脚本和本说明文档。

### 中风险：修改前先理解不变量

- 新增一种只读分析工具，例如“列出某目录中与某符号相关的文件”。工具必须带输入上限、结果上限、审计事件，并加入测试。
- 修改 Agent 提示词、最大工具轮数或回答结构：`workflows/evidence.py`。必须保证结论仍能指向引用。
- 优化索引速度或增量复用：`repositories/indexer.py`。必须证明不会改变已发布快照。
- 增加引用查看体验：前端证据组件和后端证据序列化需保持路径与行号一致。

### 高风险：不要直接改，先写设计和测试

- 授权根目录、路径解析、软链接和秘密过滤。
- `ready / stale / indexing / failed` 状态转换。
- collection 发布事务、数据库角色与 pgvector 表结构。
- 会话绑定的 `repository_id` / `collection_id`，以及历史事件回放。
- 任何“执行命令、写文件、应用补丁、访问远程 Git”的能力。

最后一类会直接影响数据隔离和证据可信度。当前后端已经提供补丁预览校验：它要求每个改动文件都有当前快照中的行号证据，并且只返回 unified diff 文本。让用户自行审核并应用；不要让 Agent 写入仓库。

## 6. 修改的标准流程

1. 在 `backend/tests/` 或 `frontend/src/**/*.test.*` 写一个能说明需求的失败测试。
2. 只改满足测试的最小实现。
3. 跑相关测试；涉及前端交互时也跑前端构建。
4. 若影响安全、快照或引用语义，更新本文和 `docs/DEMO.md`。
5. 最后执行 `make quality`。
6. 涉及证据语义时再执行 `make eval`；交付前执行 `make ci`。

## 7. 如何在面试中解释它

一句话版本：

> RepoEvidence 把本地代码库变成版本化、可追溯的证据快照；Agent 只能检索和读取受限片段，代码变更会使索引失效并要求新建快照，避免旧向量和新代码混用。

面试官继续追问时，依次讲“授权根目录 → 安全扫描 → 行级分块 → 不可变集合 → 会话绑定 → 变更失效 → 证据回看”。这条链路比单纯说“我做了一个 RAG”更能体现工程判断。
