# OfferPilot 后端 Python 重写方案

> 状态：方案稿（待评审，未开始写代码）
> 日期：2026-09-28
> 目标：将 `backend/` 的 Go 后端（约 1.5 万行 + 0.76 万行测试，94 个文件）重写为 Python 服务，前端 Next.js（`web/`）与 BFF 层保持不变。

---

## 1. 背景与目标

- 维护者无 Go 基础，希望用熟悉的语言长期维护，并后续对项目做优化。
- 前端（React UI + Next.js BFF）与用户可见行为**完全不变**，Python 后端必须逐条复刻 Go 的 HTTP 契约。
- Go 代码已全部提交 git，重写期间可随时 `git show` 参照原文。

### 1.1 已确认的决策

| 决策项 | 结论 |
|---|---|
| 技术栈 | FastAPI + asyncio（httpx / Pydantic v2 / aiosqlite / python-dotenv） |
| 内部保真度 | **务实版**：对外 API/行为契约 100% 对齐；内部省略从未被消费的 ledger 机制（outbox/lease/checkpoint/模型调用账本） |
| Go 后端处置 | **直接替换 `backend/`**（Go 源码由 git 历史保留参照） |
| 中间件/存储 | 保留 SQLite，不引入 Postgres/Redis/向量库（向量检索为后续独立增强项，见 §13） |
| legacy TS 后端 | 退役删除、不移植（被 Go/Python 取代，见 §14） |
| BFF 处置 | BFF 退化为纯代理；PDF/DOCX 解析迁入 Python（新增文档解析模块 + 中文 CID 提取对比门禁，见 §14） |
| config 接口 | 一并迁入 Python（`.env` 由后端进程读写），见 §14.2 |
| 模型选型 | 保留 OpenAI-compatible 抽象层、默认模型名可配；中文场景优先国产模型 A/B（见 §15） |
| Python 环境 | 本地 **3.12 + uv**（`uv run` + `uv.lock`）；Docker 镜像固定 `python:3.12-slim` |

---

## 2. 现状盘点

### 2.1 规模

| 维度 | 数值 |
|---|---|
| Go 代码（非测试） | ~15,000 行 / 94 文件 |
| Go 测试 | ~7,600 行 |
| 第三方依赖 | 仅 2 个：`modernc.org/sqlite`（纯 Go SQLite）、`golang.org/x/net` |
| 知识库 | `knowledge/` 36 个 `.md`、486 条可解析条目 |
| HTTP 路由 | 18 个 |
| 版本 | 0.4.1 |

关键特征：**无 LangChain/LangGraph**，全部为手写 typed Agent Harness + 面试编排 + BM25 检索 + OpenAI 兼容网关。

### 2.2 关键认知修正

仓库 `tests/e2e/` 跑的是 **legacy TypeScript 后端 `src/server.ts`**，**不是 Go 后端**。
Go 后端的验收门禁是 `go test ./...` + `go run ./cmd/offerpilot-eval`。
因此 Python 版需要**自建 pytest 套件 + 移植离线 eval harness**，不能直接复用 `tests/e2e/`。

---

## 3. 工程链路总览

```text
浏览器 (React UI, web/src)
   │  HTTP
   ▼
Next.js BFF (web/src/app/api/*, :3000)      ← 代理 + 本地解析(PDF/DOCX/配置)
   │  fetch BACKEND_URL(:3001) + Authorization: Bearer <key>
   ▼
Python API (backend/, :3001)                ← 重写对象
   │  HTTP (OpenAI-compatible)
   ├──► 文本 LLM（chat + 结构化 JSON + 多模态图片）   [OPENAI_BASE_URL]
   ├──► MiMo ASR/TTS（input_audio / audio 字段）       [MIMO_BASE_URL]
   └──► 外部网页抓取（crawler，含 SSRF 防护）
   │
   ├── SQLite（interview_sessions + 幂等命令 + 事件日志）  [DB_PATH]
   └── 知识库（knowledge/*.md → BM25 内存索引, 486 条）    [KNOWLEDGE_DIR]
```

**BFF 定位**：退化为纯代理（转发 + 鉴权头），不承载业务逻辑；文档解析（PDF/DOCX）迁入 Python 后端（见 §14.2）。后端 URL 由 `BACKEND_URL` 决定，默认 `http://localhost:3001`。

---

## 4. 模块职责与依赖

| 模块（Go） | 职责 | 依赖 | Python 落点 |
|---|---|---|---|
| `cmd/offerpilot-api` | 装配、读 env、优雅退出 | 全部 | `backend/app/main.py` |
| `config` | 加载 `.env`（进程 env 优先） | 无 | `app/config.py` |
| `httpapi` | 18 路由、鉴权、CORS、SSE/NDJSON、错误信封、前端兼容投影 | 全部服务 | `app/api/*` |
| `harness` | Agent 运行时：并发信号量(MaxConcurrent=4)、每 Agent 超时、trace、断连有界执行 | `llm` | `app/harness/` |
| `interview` | 编排：Planner→Interviewer→Assessor→Reporter、确定性策略、幂等、报告、SQLite 持久化 | `harness`/`knowledge`/`profile` | `app/interview/` |
| `profile` | 确定性 JD/简历类型化抽取（含 grounding 校验，非 LLM） | 无 | `app/profile/` |
| `knowledge` | Markdown 解析（每个 `Q：` 标题=1 条）+ BM25 检索 | 无 | `app/knowledge/` |
| `llm` | OpenAI 兼容：结构化 JSON（反射生成 schema）、多模态、重试 | 无 | `app/llm/` |
| `speech` | MiMo ASR（重试 3 次）/ TTS（不重试） | 无 | `app/speech/` |
| `chat` | 流式对话（SSE） | 无 | `app/chat.py` |
| `session` | 内存 session 注册表 + 40 条记忆 | 无 | `app/session.py` |
| `jobmatch` | 简历-JD 语义匹配（四维评分） | `harness` | `app/jobmatch.py` |
| `resumediagnosis` | 多模态简历诊断（文本 + ≤3 图） | `harness` | `app/resumediagnosis.py` |
| `webcrawler` | 快路径 + 有界 Function Tool fallback，SSRF 防护 | `harness` | `app/webcrawler.py` |
| `evals` | 离线评测（30 案例/90 题，隐私零命中门禁） | 无 | `backend/evals/`（Python 版） |

---

## 5. API 契约（必须逐条复刻）

### 5.1 路由清单

| # | Method | Path | Auth | 说明 |
|---|---|---|---|---|
| 1 | GET | `/health` | 否 | 200 |
| 2 | GET | `/health/live` | 否 | 200 |
| 3 | GET | `/health/ready` | 否 | 模型未配置 → 503 |
| 4 | POST | `/api/session` | 是 | 返回 `{sessionId}` |
| 5 | POST | `/api/chat` | 是 | SSE 流 |
| 6 | POST | `/api/interview` | 是 | JSON（action 分派） |
| 7 | POST | `/api/interview/stream` | 是 | NDJSON 流 |
| 8 | POST | `/api/v1/interview` | 是 | 同 6 |
| 9 | GET | `/api/v1/interviews/{id}` | 是 | 快照 |
| 10 | GET | `/api/v1/interviews/{id}/review` | 是 | 复盘 |
| 11 | GET | `/api/v1/interviews/{id}/events` | 是 | 事件日志 |
| 12 | POST | `/api/transcribe` | 是 | 原始音频 body |
| 13 | POST | `/api/tts` | 是 | 返回音频二进制 |
| 14 | POST | `/api/crawl` | 是 | URL 抓取 |
| 15 | POST | `/api/v1/crawl` | 是 | 同 14 |
| 16 | POST | `/api/match` | 是 | JD 匹配 |
| 17 | POST | `/api/v1/match` | 是 | 同 16 |
| 18 | POST | `/api/resume/diagnose` | 是 | 多模态诊断 |
| 19 | POST | `/api/v1/resume/diagnose` | 是 | 同 18 |

> 注：BFF 的 `/api/interview` GET 用 query `interviewId`，转成后端的 path `{interviewId}`；Go 同时接受 `?interviewId=` fallback。

### 5.2 两套错误信封并存（重点）

| 风格 | 形状 | 适用路由 |
|---|---|---|
| API 风格 | `{"error":{"code","message","retryable","field"?}}` | interview/match/resume/crawl/auth/body-too-large |
| Legacy 风格 | `{"error":"string"}` | chat/session/tts |
| Transcribe 特例 | `{"error":"string","retryable":bool}` | transcribe |

### 5.3 流式格式

- **chat = 真 SSE**：`Content-Type: text/event-stream; charset=utf-8`，事件 `session` / `thinking_delta` / `text_delta` / `error` / `done`（`usage:{inputTokens,outputTokens}`），行格式 `data: <json>\n\n`，结尾 `data: [DONE]\n\n`。
- **interview/stream = NDJSON**：`Content-Type: application/x-ndjson; charset=utf-8` + `X-Accel-Buffering: no`，每行一个信封 `{"type":"trace","trace":{...}}` 或末尾恰好一条 `{"type":"result","status":N,"data":{...}}`。BFF 靠请求的 `Accept` 头决定走流式还是 JSON。

### 5.4 鉴权与 CORS

- 鉴权开启条件：`NODE_ENV=production`（忽略大小写）**或** `OFFERPILOT_REQUIRE_AUTH=true`。
- `OFFERPILOT_API_KEY` 为空时不强制（dev 开放）；非空时要求 `Authorization: Bearer <key>` 常量时间比较，失败 401 `unauthorized`。
- CORS：`OFFERPILOT_ALLOWED_ORIGINS`（CSV，默认 `http://localhost:3000,http://127.0.0.1:3000`）精确匹配；`Origin` 存在且不在白名单 → 403 `origin_not_allowed`（先于鉴权）。OPTIONS → 204。
- 响应头统一带 `X-Request-ID`（回显或 12 字节随机 hex）、`X-Content-Type-Options: nosniff`、`Referrer-Policy: no-referrer`。

### 5.5 面试 action 分派（`/api/interview`）

| action | 请求体要点 | 响应要点 |
|---|---|---|
| `start` | `config{focus,difficulty,questionCount,language,feedbackMode}` + `materials{jd?,resume?}`；`clientSessionId` 可空（自动生成） | `{interviewId,state,profile,question,progress}` |
| `answer` | `interviewId,questionId,clientAnswerId,answer{text,inputMode,durationMs?}` | `{interviewId,state,feedback,nextQuestion,progress,reportReady}` |
| `report` | `interviewId` | `webReport`（含 6 维评分、jdCoverage、projectCoverage、turns、readiness） |

`materials.jd/resume` 支持**双形态**：纯字符串 或 `{name,text}`。

---

## 6. 数据契约

### 6.1 务实版 SQLite schema（保留 3 张表）

| 表 | 用途 | 关键约束 |
|---|---|---|
| `interview_sessions` | 会话快照 | `id PK`、`version`（乐观锁 CAS）、`snapshot_json`（= `{session, sourceContents}`，`SourceDocument.Content` 因 `json:"-"` 单独存） |
| `interview_commands` | 幂等命令 | `UNIQUE(principal_id, session_id, action, idempotency_key)`；`status ∈ pending/running/succeeded/failed`；subject 唯一部分索引 |
| `interview_session_events` | 事件日志（供 `/events` + 前端恢复） | `(session_id, sequence) PK`，`event_id` 唯一，序列单调 |

**省略**（Go 中有但无 handler 消费）：`interview_session_cursors`（用简单计数器替代）、`interview_model_invocations`、`interview_checkpoints`、`interview_run_leases`、`interview_outbox`。

打开参数：单连接串行写、`busy_timeout=5000`、`journal_mode=WAL`、`synchronous=NORMAL`、`foreign_keys=ON`。

### 6.2 知识库格式

- 每个 `## Q：<问题>`（H2–H6、`Q[：:]`）标题 = 1 条知识条目；正文到下一个标题为止。
- 条目：`ID = "kb_" + sha256(source+"\0"+question+"\0"+ordinal)[:20]`；`Source` = 相对路径；`Title` = 文档标题；`Question`；`Excerpt` = 520 字纯文本预览；`body` 不序列化不外传。
- 检索器给面试的 `Content` 模板：`知识主题：{Title}\n问题：{Question}\n参考内容：{Excerpt}\n来源：{Source}`。

---

## 7. 关键行为契约（易错点，务必逐条保真）

### 7.1 Harness 并发 / 超时

1. Agent 超时**只在拿到信号量之后**开始计时；排队等待只受调用方 context 约束。
2. 信号量**全局共享**（默认 4），非 per-agent。
3. trace 环形缓冲满时**左移丢弃最旧**（不是循环覆盖）。
4. `callJSON` 出错时仍返回 traceID；排队被取消时返回裸 `ctx.Err()`，调用失败才包 `harness: agent ...`。
5. trace 错误文本双份：全局缓冲存完整错误；请求流只看到固定串 `"agent call failed"` / `"function tool call failed"`。

### 7.2 结构化输出（llm）

6. strict `json_schema` 中**每个导出字段都 required**，可选性只靠指针字段 `anyOf [T, null]` 表达；解码 `DisallowUnknownFields` + 拒绝 `null` + 拒绝尾随值。
7. **恰好一次** `json_object` 兼容降级 + **恰好一次** decode 失败 repair（二者共享同一恢复预算，不叠加）。
8. 超时按 attempt（90s）；`DeadlineExceeded` 可重试、`Canceled` 立即中止；响应读上限 4 MiB。

### 7.3 面试编排

9. 策略（追问/晋级/结束）是**确定性代码**，Planner LLM 只在 `advance` 时调用，且其选择要对照会话快照二次校验。
10. 幂等：`clientAnswerId` 哈希只算 `{questionId, answer}`；同 id 不同 payload → 409 conflict；同题新 id → 409。
11. 每会话 keyed lock + **锁内重载**（观察前一 in-flight 已提交结果）后再评估。
12. 知识证据每问**上限 5 条**，追加到该覆盖点的 JD/resume refs 之后，整体规范化写回 `session.Profile.Coverage`。
13. 评分：知识题 `/20`、项目题 `/30`，`round`（Go 半值远离零）；follow-up/prerequisite 无 resume 证据时用知识评分式。
14. 追问轴选择避免同一 RootID 内重复使用，最终兜底字面量 `"specificity"`。
15. 覆盖点优先级：base 50 + focus 匹配 +20 + 每条 JD ref +20 / resume +15 / knowledge +10；平局按题数→优先级→最旧 `LastAskedTurn`。

### 7.4 隐私 / 脱敏

16. 知识库**参考内容**只给 Assessor；任何模型生成文本复现 16 字私有片段即判泄露、整体替换/省略。
17. 泄露检测归一化：小写字母 + 数字，其余丢弃；`PublicGeneratedText` 返回空串 =「省略/替换」，调用方用确定性兜底摘要。
18. 思维链/系统提示词/简历与 JD 原文/参考内容**永不**进 trace、事件日志、模型请求的 system 层（JD/resume 只以锚点/引用形式出现）。

### 7.5 其他

19. `MaterialInput` 双形态解码（字符串或对象）。
20. `SourceDocument.Content` 是 `json:"-"`，快照需单独存/复原。
21. `InterviewRunTimeout`（默认 5m）把执行从请求 context 解耦；请求断连只终止流，执行继续。
22. 失败写盘用 `context.WithoutCancel` 语义，保证断连仍落 `answer.failed` 事件。
23. BM25：`k1=1.35, b=0.72`，平滑 IDF `ln(1+(N-df+0.5)/(df+0.5))`；加权文档 = Title×2 + Question×3 + body；短语加分 question +6 / body +2；单字符查询跳过强词预过滤；平局按 ID 升序。
24. 报告 readiness：`<55` 或 0 轮 → `not_ready`；`<4` 轮 → `borderline`；否则需覆盖完整 + `>=75` + 无 critical 风险才 `ready`。
25. ASR 重试 3 次（EOF/超时/连接重置/408/429/5xx），TTS 不重试（避免重复生成）；请求头同时带 `api-key` 与 `Authorization: Bearer`。

---

## 8. Python 技术栈与模块映射

### 8.1 依赖清单

| 依赖 | 用途 |
|---|---|
| `fastapi` | 路由、依赖注入、Pydantic 校验 |
| `uvicorn` | ASGI 服务（`--workers 1`，对齐 Go 单进程模型） |
| `httpx` | 所有出站 HTTP（LLM / speech / crawler），支持 async 流式 |
| `pydantic>=2` | 类型化结构、JSON Schema 生成（对齐 Go `schemaFor`）、请求/响应模型 |
| `aiosqlite` | 单连接串行写，对齐 Go `SetMaxOpenConns(1)` |
| `python-dotenv` | 加载 `.env`（进程 env 优先，对齐 `config.LoadDotEnv`） |

不引入 LangChain/LangGraph、ORM、重型任务队列——保持与 Go 版一致的"轻依赖、手写编排"风格。

### 8.2 关键实现对照

| Go 机制 | Python 实现 |
|---|---|
| `harness` 信号量 | `asyncio.Semaphore(MaxConcurrent)` |
| 每 Agent 超时 | `asyncio.timeout(agent_timeout)`（仅包 invoke，不包排队） |
| 断连有界执行 | `run_task = asyncio.create_task(...)`，用独立超时（5m）而非请求 context |
| 结构化 schema | Pydantic `model_json_schema()` + strict 手写（required 全字段 + 指针→`anyOf[T,null]`） |
| 结构化解码 | `pydantic` 校验 + `forbid_extra`（对齐 `DisallowUnknownFields`） |
| BM25 + 分词器 | 手写 ~40 行（对齐 CJK unigram+bigram、平滑 IDF、短语加分） |
| 确定性 profile 抽取 | 手写正则/启发式（对齐 `extractor.go` + grounding 校验） |
| SQLite | `aiosqlite` 单连接 + WAL pragma |

### 8.3 目录结构（替换 `backend/`）

```text
backend/
  pyproject.toml            # 依赖 + 入口
  app/
    main.py                 # 装配、env、优雅退出（对应 cmd/offerpilot-api）
    config.py               # .env 加载（进程 env 优先）
    api/                    # httpapi 路由 + 投影
      routes.py  auth.py  cors.py  errors.py
      interview.py  chat.py  speech.py  crawler.py  match.py  resume.py
      documents.py          # /api/parse-pdf 代理（转发到文档解析模块）
      projections.py        # web* 前端兼容投影
    harness/                # runtime + trace + 4 个 agent（含中文系统提示词）
    interview/              # service + policy + types + sqlite_store
    profile/                # 确定性抽取 + grounding 校验
    knowledge/              # markdown 解析 + BM25 + retriever
    documents/              # PDF/DOCX 解析（PyMuPDF + python-docx，对应原 BFF parse-pdf）
    llm/                    # OpenAI 兼容 client + 结构化 schema
    speech.py               # MiMo ASR/TTS
    chat.py                 # 流式对话
    session.py              # session 注册表 + 记忆
    jobmatch.py  resumediagnosis.py  webcrawler.py
  evals/                    # 离线评测（Python 移植，corpus 原样复用）
  tests/                    # pytest 套件（对应 go test + 额外契约测试）
```

---

## 9. 分阶段实施计划（每阶段独立验收）

| 阶段 | 内容 | 验收标准 |
|---|---|---|
| **P0 骨架** | ① 录 Go golden 契约快照（18 路由校验/拒绝路径）；② env 加载、FastAPI app、health×3、鉴权/CORS、两套错误信封、NDJSON/SSE 工具 | golden 快照落盘；健康检查 + 鉴权/CORS 单测通过；health 返回字段与 Go 完全一致 |
| **P1 知识库** | MD 解析、分词器、BM25、retriever | `knowledgeEntries == 486`；抽样查询排序与 Go 一致 |
| **P2 LLM 网关** | chat + 结构化 JSON（Pydantic schema）+ 多模态 + 重试/降级/repair | 单测覆盖 schema 生成、降级、repair 边界 |
| **P3 speech/chat/session** | MiMo ASR/TTS、SSE 对话、会话记忆 | ASR 重试策略、SSE 事件序列、记忆 40 条裁剪单测 |
| **P4 harness + interview**（最重） | 运行时、4 agent 提示词、profile 抽取、编排、幂等、SQLite、报告/投影 | 幂等/策略/评分/脱敏单测 + `/api/v1/interviews/*` 恢复链 |
| **P5 文档解析 + 其余 agent** | 文档解析（PDF/DOCX，PyMuPDF/python-docx）+ jobmatch、resumediagnosis、webcrawler | 文档解析单测 + 中文 CID 提取对比；各 agent 校验/repair/限制单测 |
| **P6 验收** | eval harness 移植 + pytest 全量 + BFF 冒烟（含文档解析链路） | eval 门禁全绿；BFF 指向 Python 后端跑通核心 UI 流程（含 PDF/DOCX 上传） |

---

## 10. 验收标准（最终）

1. **契约测试**：18 路由的请求/响应 JSON、错误信封、SSE/NDJSON 帧格式与 Go 逐字段一致（用录制的 Go 响应做快照对比）。
2. **行为单测**：复刻 Go 单测里的核心不变量（幂等冲突、策略分支、评分公式、脱敏、BM25 排序、schema 生成）。
3. **离线 eval**：移植 `backend/evals/`，30 案例/90 题全绿（无重复题、证据全有效、隐私零命中）。
4. **集成冒烟**：`web` 指向 Python 后端，跑通「JD 匹配 → 模拟面试 → 报告 → 简历诊断 → 录音诊断」核心链路。
5. **部署**：`docker-compose.yml`、`Dockerfile`、`package.json` 的 `serve` 脚本、CI 切换到 Python 后端。
6. **文档解析**：PDF/DOCX 提取结果与原 pdfjs-dist/mammoth 输出做 side-by-side 对比（重点中文 CID 字体），差异在可接受范围。

---

## 11. 风险与注意事项

- **行为契约是主风险**：Go 版花了多个版本稳定下来的一批不变量（幂等、脱敏、评分、超时语义），重写最怕"语法翻译对了、边界行为漂了"。对策：**趁 Go 还在，P0 先录 18 路由的 golden 契约快照**（校验/拒绝路径不依赖模型，无需 API key），Python 侧逐字段 diff；每阶段再用契约测试钉死，不靠"看起来差不多"。
- **提示词必须逐字搬运**：4 个 agent 的中文系统提示词（含 grounding/no-leak/no-CoT 约束）与 jobmatch/resumediagnosis/webcrawler 的评分 prompt，是输出质量的关键，不得改写。
- **替换时机**：Python 后端在 `backend/` 内开发期间，Go 源码由 git 历史保留；建议在 P6 验收通过后再删 `backend/*.go`，避免中途失去可对照的运行参照。
- **性能**：整体 IO 密集，Python asyncio 单进程足够（Go 版也是单进程 + 并发 4）；BM25 在启动时内存索引，486 条规模无压力。

---

## 12. 后续优化挂接点（用户后续会做）

- 知识检索：当前 BM25，后续可升级向量/重排（仓库已有 `docs/vector-retrieval-proposal.md`）。
- 报告导出：Markdown/PDF 目前在前端，后续可考虑后端生成。
- 会话/记忆：当前内存态，可演进为持久化 + 跨设备同步（对应 Go 里预留但未启用的 ledger 机制）。
- 能力雷达 / 错题聚类 / 训练计划：依赖版本化 `review` schema（已在前端存在）。
- 流式协议标准化：现为"出站 OpenAI 协议 + 入站自定义 SSE/NDJSON"，后续可评估切 AG-UI 或标准 OpenAI SSE（收益在接第三方面板/多端复用，需动前端解析层，独立立项）。

---

## 13. 中间件与存储决策

### 13.1 SQLite 的职责与结论

SQLite 只干三件事（务实版 3 张表）：会话快照恢复、回答幂等账本、事件日志。**除 SQLite 外没有其他中间件**——无 Redis、无消息队列、无向量库；session/记忆是内存 map，知识检索是内存 BM25。

**结论：保留 SQLite，不引入任何更重的组件。** 理由：

- 数据量极小（单用户、KB~MB 级）、单写者、单实例部署，SQLite + WAL + 单连接串行写完全够；Postgres/MySQL 属过度设计。
- 升级的硬条件（水平扩容、多写者、数据 >~1GB）当前一个都不满足。
- Redis/队列：单进程下用 asyncio 信号量 + SQLite 即可协调，只有未来多 worker 扩容才需要（限流/分布式锁/SSE 扇出），属过早设计。

### 13.2 向量数据库

唯一值得未来投入的是检索增强，但**不是现在**：486 条知识 BM25 足够且**确定性**（离线 eval 门禁卡在它上面）。方向是「BM25 召回 + embedding 重排」的渐进式（仓库已有 `docs/vector-retrieval-proposal.md`），而非替换。486 条规模用 numpy 余弦内存算即可，无需 Qdrant/Chroma。注意：引入 embedding 需新增 embedding 模型依赖与成本。

---

## 14. legacy TS 后端与 BFF 处置

TS 相关后端分**两块，性质不同**：

### 14.1 legacy TS 后端（`src/`，约 6.3k 行）→ 退役

已被 Go 取代的旧实现，仅被 `npm run serve:legacy`、`npm start`（CLI）与 `tests/e2e` 使用。功能（agent loop、子 agent、工具、embedding 检索、多 provider 路由）Go 版均已重做。

**决策：不移植、直接退役删除。** 理由：6.3k 行已被替代的实现翻成 Python 是纯浪费；它唯一 Go 没继承的 embedding 检索是 Go **故意**用 BM25 替代的。其 `tests/e2e` 随之一并换成 Python 测试（见 §10）。

### 14.2 Next.js BFF（`web/src/app/api/*`）→ 退化为纯代理

- **纯代理路由**（chat/session/interview/match/resume/parse-url/transcribe/tts/health）：Go→Python 后**零改动**，继续转发到 `:3001`。
- **本地路由**（parse-pdf / config / diagnosis）：原为 Node 专属逻辑。

**决策（已确认）：文档解析（PDF/DOCX）迁入 Python，BFF 退化为纯代理。** 理由：前端服务不应承载文档解析这类业务；且 PDF 文本最终被 Python 后端的简历诊断/面试材料入口消费，后端统一解析可复用同一份解析结果。

- `parse-pdf` → Python 新增 `documents/` 模块（PyMuPDF 文本+页面渲染、python-docx 读 DOCX），BFF 该路由改为纯转发。
- **保留风险与门禁**：README 明确「中文 CID 字体 PDF 靠 pdfjs-dist 的本地 CMap 才正确提取」。pdfjs-dist 对 CJK CMap 支持极其成熟，PyMuPDF 也强但个别「CID 无 ToUnicode」边角可能不一致。**迁移必须新增"中文简历提取文本 side-by-side 对比"用例，纳入 §10 验收。**
- `config`（models.yml + .env 读写）：**已确认一并迁 Python**（`.env` 本由后端进程读写，Python 拥有它更合理），随文档解析一并处理。
- `diagnosis`（内存仪表盘）：前端本地学习状态，非文档业务；维持前端实现，后续与"能力雷达持久化"优化项合并评估。

### 14.3 Python 实现自身的风险

- 主风险仍是**行为契约漂移**（§7 的 25 条），非语言问题。
- 次风险是 asyncio 并发/取消语义（断连有界执行、SSE、超时）比 Go goroutine 更易踩坑，靠谨慎设计 + 测试兜住。
- 性能：IO 密集、单实例，Python 完全够；uvicorn 单 worker 对齐 Go 单进程。

---

## 15. 模型选型

### 15.1 现阶段实际使用

**Go 后端（现役）：**

| 用途 | 模型 | 接口 |
|---|---|---|
| 文本/对话/结构化/多模态 | `gpt-5.5`（默认，经 `ai.tosky.top` 中转；代码默认 base `api.openai.com/v1`） | OpenAI-compatible |
| ASR | `mimo-v2.5-asr` | OpenAI-compatible `input_audio` |
| TTS | `mimo-v2.5-tts`（`mimo_default` 音色） | OpenAI-compatible `audio` |

**Legacy TS 用过（已退役）**：Claude（sonnet-4）、DeepSeek（deepseek-chat）、GPT-4o/4-turbo/4o-mini、Whisper（whisper-1）、OpenAI TTS（tts-1）、embedding。

### 15.2 代码对模型的硬约束

文本模型需满足：(a) OpenAI-compatible `/chat/completions`；(b) 结构化输出（优先 strict `json_schema`，代码已内置 `json_object` 降级 + 一次 repair 兜底，故**不强制**原生 strict 支持）；(c) 多模态图片输入（resume 诊断 `image_url`）；(d) 中文质量；(e) 可选 `reasoning_content`（chat「思考」展示）。ASR/TTS 需 OpenAI 兼容的音频输入/输出。

### 15.3 选型结论

- 整套是 OpenAI-compatible 抽象层，换模型 = 换 `base_url + model + key`，**可插拔**。重写时保留该抽象，**不要把 `gpt-5.5` 写死**；`gpt-5.5` 非 OpenAI 官方型号（中转站命名、可能变动），默认模型名做成配置项。
- **文本主链路（中文优先）**：DeepSeek（便宜、中文强、`json_object` 稳，多模态/严格 `json_schema` 需确认）、Qwen 通义（结构化输出 + 多模态都强）、GLM/Kimi（长文本）；国际线 GPT-4o/4.1（多模态 + strict `json_schema` 最稳）。
- **多模态（resume 诊断）**：GPT-4o/4.1、Qwen-VL、GLM-4V、Doubao 均可，选定一个验证 `image_url`。
- **ASR/TTS**：MiMo 便宜（约 1 分钟 1 分钱）够用；更高 ASR 质量可换 Whisper（贵）；中文更好 + 全家桶可换 Doubao 语音；自部署 TTS 可考虑开源 CosyVoice。
- **检索（将来）**：加向量需引入 embedding 模型（BGE / text-embedding-3 等），与 §13.2 绑定。
