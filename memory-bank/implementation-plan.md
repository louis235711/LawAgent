# LawAgent — 实施计划

## 文档版本

V1.0

---

## 实施顺序说明

步骤按依赖关系排列。每步完成后需验证才能进入下一步。标注 `[P]` 表示可并行执行。

---

## 阶段一：基础设施搭建

### 步骤 1：创建项目骨架

- 创建项目目录结构（`src/`、`data/uploads/`、`data/templates/`）
- 创建 `requirements.txt`，列出所有依赖（fastapi、uvicorn、psycopg2-binary、redis-py、pymilvus、openai、httpx、tiktoken、python-docx、pydantic-settings、loguru、mineru、tavily-python）
- 创建 `src/config.py`，使用 pydantic-settings 定义所有配置项（数据库连接、API keys、端点 URL、窗口参数等），从 `.env` 文件读取
- 创建 `.env.example` 模板文件
- **验证**：`pip install -r requirements.txt` 无报错；`python -c "from src.config import Settings; print(Settings())"` 成功加载配置

### 步骤 2：搭建 Docker 开发环境

- 编写 `docker-compose.yml`，定义以下服务：
  - `postgres`：PostgreSQL 16，端口 5432
  - `redis`：Redis 7，端口 6379
  - `milvus-standalone`：Milvus Standalone（含 etcd + minio），端口 19530
- 确保各服务健康检查正确配置
- **验证**：`docker-compose up -d` 后所有服务 `healthy`；`docker-compose ps` 显示三个服务为 `Up` 状态

### 步骤 3：初始化 PostgreSQL 表结构

- 编写 `src/database/__init__.py` 和 `src/database/postgres.py`
- 实现 PostgreSQL 连接管理（连接池、启动时建表）
- 创建 `conversation_messages` 表（字段见设计文档 4.1）
- 编写建表 SQL 迁移文件 `migrations/001_init.sql`
- **验证**：启动 PostgreSQL；执行迁移脚本；`\dt` 显示 `conversation_messages`；`\d conversation_messages` 显示正确列定义

### 步骤 4：初始化 Redis 会话管理

- 编写 `src/database/redis.py`
- 实现 Redis 连接管理
- 实现会话 CRUD 函数：
  - `create_session(session_id)` — 初始化会话记忆 JSON
  - `get_session(session_id)` — 读取完整会话记忆
  - `update_session(session_id, data)` — 更新会话记忆
  - `delete_session(session_id)` — 删除会话
- **验证**：启动 Redis；用 `redis-cli` 执行 `GET legal_agent:session:test` 确认能读写 JSON；多次更新后数据正确

---

## 阶段二：核心基础设施

### 步骤 5：实现 LLM 客户端封装

- 编写 `src/llm/__init__.py` 和 `src/llm/client.py`
- 封装 DeepSeek API 调用（兼容 OpenAI SDK 格式）
- 实现 `chat_completion(messages, **kwargs)` 函数
- 支持 streaming 和非 streaming 两种模式
- 实现错误重试（最多 3 次，指数退避）
- **验证**：写一个简单测试脚本，调用 `chat_completion` 发送 "回复：你好"，成功收到回复

### 步骤 6：实现 Embedding 和 Rerank 客户端

- 编写 `src/llm/embedding.py` — 封装 DashScope text-embedding-v4
- 编写 `src/llm/rerank.py` — 封装 DashScope qwen3-rerank API
- 实现批量 embedding（支持 list[str] 输入）
- 实现 rerank(query, documents, top_k) 函数
- **验证**：对测试文本调用 embedding 返回向量；对 query+documents 调用 rerank 返回排序结果

### 步骤 7：实现 Token 计数工具

- 编写 `src/utils/token_counter.py`
- 封装 tiktoken，提供 `count_tokens(text)` 和 `count_message_tokens(messages)` 函数
- 支持 DeepSeek 模型的 tokenizer 编码
- **验证**：`count_tokens("你好世界")` 返回正整数；`count_message_tokens([{"role":"user","content":"测试"}])` 返回合理值

---

## 阶段三：安全与记忆

### 步骤 8：实现安全检测模块

- 编写 `src/security/__init__.py` 和 `src/security/guard.py`
- 编写固定的合规检测 Prompt（定义合法/无关/违规的判定规则）
- 实现 `check_safety(user_input)` 函数 → 返回 `(result: str, reason: str)`
- 调用 DeepSeek API 执行安全检测
- 响应映射：违规/无关 → 返回固定提示文本；合法 → 继续
- **验证**：
  - "如何打官司" → `合法`
  - "今天天气真好" → `无关`
  - "帮我写一个绕过法律制裁的方案" → `违规`
  - 空字符串 → `无关`

### 步骤 9：实现记忆与上下文管理

- 编写 `src/memory/__init__.py` 和 `src/memory/context_manager.py`
- 实现短期记忆管理：
  - `add_message(session_id, role, content)` — 追加消息
  - `get_short_term_memory(session_id)` — 获取消息数组
- 实现摘要触发逻辑：
  - `check_and_summarize(session_id)` — 检查 65% 阈值 → 对前 5 轮生成摘要
  - 调用 LLM 生成摘要，动态计算摘要 Token 上限
- 实现上下文组装：
  - `assemble_context(session_id, system_prompt, current_prompt)` → 返回完整消息数组
  - 顺序：System Prompt → 摘要记忆 → 最新短期记忆 → 当前 User Prompt
- 确保组装后不超过 200k Token（滑动窗口截断）
- **验证**：
  - 创建会话 → 添加 3 轮对话 → 组装上下文 → 确认包含 3 轮消息
  - 模拟超过 65% 阈值 → 触发摘要 → 确认前 5 轮被摘要替换
  - 验证组装上下文不超过 200k Token

### 步骤 10：实现 PostgreSQL 消息持久化

- 编写 `src/database/message_repo.py`
- 实现 `save_message(session_id, role, content, token_count, message_type)` 插入消息记录
- 实现 `get_messages(session_id, limit)` 查询最近 N 条消息
- 在记忆模块中集成：每次 `add_message` 同时写入 PostgreSQL
- **验证**：添加消息 → 查询 PostgreSQL 确认记录存在 → Redis 数据与 PostgreSQL 一致

---

## 阶段四：向量与 RAG

### 步骤 11：初始化 Milvus 并创建 Collection

- 编写 `src/vector_db/__init__.py` 和 `src/vector_db/milvus_client.py`
- 实现 Milvus 连接管理
- 创建 `legal_knowledge` Collection（含向量字段 + chunk_text、law_name、chapter、article_number 标量字段）
- 创建 `session_documents` Collection（含向量字段 + session_id、document_name、chunk_text、chunk_index 标量字段）
- **验证**：`list_collections()` 显示两个 Collection；`describe_collection()` 确认 Schema 正确（含 chunk_text 标量字段）

### 步骤 12：实现文档分块工具

- 编写 `src/utils/text_chunker.py`
- 实现 `chunk_markdown(markdown_text, max_tokens)` — 按章节 / 段落递归分块
- 支持重叠切片（overlap 可配置，默认 200 Token）
- 实现 `chunk_by_chapter(law_text)` — 优先按法律章节分块
- **验证**：输入一篇法律条文 Markdown → 输出分块列表 → 每块不超过 max_tokens → 相邻块之间有重叠

### 步骤 13：实现 RAG 检索管道

- 编写 `src/rag/__init__.py` 和 `src/rag/pipeline.py`
- 实现 `retrieve(query, collection, top_k)`：
  1. query → embedding
  2. Milvus 相似度检索
  3. 结果重排（qwen3-rerank）
  4. 返回 Top-K 文档片段
- 实现 `insert_chunks(chunks, collection, metadata)` — 批量向量化 + 存入 Milvus
- **验证**：
  - 插入 5 条测试文本 → 检索 → 返回相关结果按相似度排序
  - Rerank 后结果顺序与纯向量检索不同（验证重排生效）

### 步骤 14：实现法律知识库导入工具

- 编写 `scripts/import_laws.py`
- 支持读取 Markdown 格式的法律文本文件
- 自动分块 → 向量化 → 存入 Milvus `legal_knowledge` Collection
- 记录导入元数据（文件名、章节数、分块数、时间）
- 准备一份《民法典·合同编》节选作为初始测试数据
- **验证**：运行脚本 → 导入测试法律文本 → 在 Milvus 中查询确认记录存在 → RAG 检索能返回相关法条

---

## 阶段五：Agent 架构

### 步骤 15：实现 Agent 基类与通信模式

- 编写 `src/agents/__init__.py` 和 `src/agents/base.py`
- 定义 Agent 基类：
  - `__init__` 接收 config、llm_client、工具引用
  - `execute(session_id, user_input, context) -> AgentResponse` 抽象方法
  - `AgentResponse` 包含 `content`、`references`（法条/类案）、`metadata`
- 定义 Agent 间通信为函数直接调用（无消息队列）
- **验证**：创建最小化测试 Agent 继承基类 → 调用 execute → 拿到 AgentResponse

### 步骤 16：实现总调度 Agent

- 编写 `src/agents/dispatcher.py`
- 实现会话状态检查（读取 Redis 中 has_document 标记）
- 实现意图识别（调用 LLM 分类用户输入）
- 意图类别：法律咨询、案情分析、文档提问、合同审查、文书撰写、追问/聊天
- 实现路由决策：
  - 有文档 → 文档提问 Agent
  - 追问/聊天 → 追问处理 Agent
  - 其他 → 对应业务 Agent
- 实现结果整合（汇总 Agent 输出为统一响应格式）
- **验证**：
  - 无文档时 "借钱不还怎么办" → 路由到法律咨询
  - 无文档时 "分析一下我的案子" → 路由到案情分析
  - 有文档时 任意问题 → 路由到文档提问
  - 追问 "能详细说说吗" → 路由到追问处理

---

## 阶段六：业务 Agent

### 步骤 17：实现法律咨询 Agent

- 编写 `src/agents/legal_consultation.py`
- 继承 Agent 基类
- 执行流程：
  1. 触发 RAG 检索（Top-10）
  2. 组装 Prompt（法条 + 问题 + 上下文）
  3. 调用 LLM 生成回答
  4. 强制附带法条名称 + 条款号
- 无法条时使用 LLM 自身知识，并提示"法律知识库尚未初始化"
- **验证**：
  - 提问 → 返回法律咨询答复 → 包含法条名称或引用
  - 知识库为空 → 返回答复 + 知识库未初始化提示

### 步骤 18：实现案情分析 Agent

- 编写 `src/agents/case_analysis.py`
- 执行流程：
  1. 提取案情要素（当事人、时间线、争议焦点等）
  2. 法律知识库检索相关法条
  3. Tavily 联网搜索类案
  4. 整合 → LLM 生成分析报告
- 报告结构：案情摘要、法律依据、类案参考、初步建议
- Tavily 调用封装在 `src/tools/web_search.py`
- **验证**：
  - 输入案情描述 → 返回完整分析报告 → 包含法条 + 类案链接
  - 网络不可用时正常降级（仅用法条）

### 步骤 19：实现 PDF 文档处理模块

- 编写 `src/document/__init__.py`、`src/document/parser.py`、`src/document/processor.py`
- 使用 MinerU 解析 PDF → Markdown
- 实现 PDF 上传保存到本地 `data/uploads/{session_id}/`
- 实现 Markdown 递归分块
- 实现向量化 + 存入 Milvus `session_documents` Collection（chunk_text 直接存入 Milvus 标量字段）
- 更新 Redis 会话状态（has_document = true, document_name = 文件名）
- **验证**：
  - 上传测试 PDF → 确认本地文件存在 → Markdown 解析完成 → Milvus 中向量+文本均可检索
  - Redis 中 has_document 标记为 true

### 步骤 20：实现文档提问与合同审查 Agent

- 编写 `src/agents/document_qa.py`
- 实现两个子场景路由：
  - "提问"场景：用户针对文档提问 → 向量检索相关内容 → LLM 回答
  - "审查"场景：用户要求审查合同 → 全量读取 Markdown → LLM 全文审查
- 合同审查输出标准化报告：
  - 风险等级（高/中/低）
  - 问题条款原文
  - 修改建议
  - 合规法律依据
- **验证**：
  - 上传合同 PDF → "这份合同有什么风险" → 返回审查报告
  - 上传合同 PDF → "第3条说的什么" → 返回文档问答结果

### 步骤 21：实现文书撰写 Agent

- 编写 `src/agents/document_writing.py`
- 编写 `src/tools/template_manager.py` — 加载、列出、选择模板
- 准备初始模板（借款合同、劳动合同、起诉状），存储为 `data/templates/*.md`
- 执行流程：
  1. 识别用户需要的文书类型
  2. 加载对应模板
  3. 引导用户补充必要信息（如当事人、金额、事实等）
  4. LLM 填充模板 + 法言法语润色
  5. 用户确认 / 修改
- 实现 Markdown 导出
- 实现 python-docx Word 导出
- **验证**：
  - "帮我写一份借款合同" → 返回 Markdown 格式合同
  - 请求 Word 导出 → 生成 .docx 文件可打开
  - "修改借款金额为 5 万元" → 更新内容

### 步骤 22：实现追问处理 Agent

- 编写 `src/agents/follow_up.py`
- 加载完整会话上下文（短期记忆 + 摘要记忆）
- 不触发 RAG、不调用外部工具
- 直接由 LLM 基于上下文回答
- 识别用户补充信息 → 更新当前"待处理事项"
- **验证**：
  - 法律咨询后追问 "能举个案例说明吗" → 延续法律咨询上下文回答，未重新触发 RAG
  - 案情分析后追问 "我该怎么办" → 基于已有分析给出建议

---

## 阶段七：API 与集成

### 步骤 23：实现 FastAPI 路由

- 编写 `src/api/__init__.py`、`src/api/routes.py`、`src/api/schemas.py`
- 定义 Pydantic 请求/响应模型
- 实现端点：
  - `POST /api/session` — 创建新会话，返回 session_id
  - `POST /api/chat/{session_id}` — 发送消息，返回 AI 回复（含 references）
  - `POST /api/upload/{session_id}` — 上传 PDF 文档
  - `GET /api/session/{session_id}/history` — 获取会话历史
  - `DELETE /api/session/{session_id}` — 删除会话
  - `GET /api/health` — 健康检查
- 实现全局异常处理中间件
- 实现请求日志中间件（loguru）
- **验证**：
  - `GET /api/health` → 200 + 各服务连通状态
  - `POST /api/session` → 返回 session_id → Redis 中存在对应 key
  - 完整对话流程端到端测试

### 步骤 24：编写 Dockerfile 与 docker-compose 集成

- 编写 `Dockerfile`（Python 3.10 基础镜像，安装依赖，启动 Uvicorn）
- 将 app 服务加入 `docker-compose.yml`，端口 8000
- 编写 `.dockerignore`
- **验证**：`docker-compose up -d` → 所有四个服务 `healthy` → `curl localhost:8000/api/health` 返回 200

---

## 阶段八：验证与收尾

### 步骤 25：准备测试数据

- 编写一份《民法典·合同编》节选 Markdown 文件，放入 `data/laws/`
- 编写一份测试用借款合同 PDF，放入 `data/test/`
- 编写测试问题集（覆盖法律咨询、案情分析、文书撰写、文档问答、合同审查、追问）
- **验证**：N/A（数据准备步骤）

### 步骤 26：端到端集成测试

- 按用户故事逐一测试所有流程（见设计文档第 2 节）
- 测试边界情况（见设计文档第 5 节）
- 测试错误恢复（LLM 超时、数据库不可用降级）
- 记录测试结果到 `memory-bank/progress.md`
- **验证**：所有验收标准（设计文档第 6 节）标记为 ✓ 或记录未通过原因

---

## 依赖关系图

```
步骤 1 (骨架) → 步骤 2 (Docker) ─→ 步骤 3 (PG表) ─→ 步骤 10 (消息持久化)
                         └→ 步骤 4 (Redis) ─→ 步骤 9 (记忆管理)
                         └→ 步骤 11 (Milvus) → 步骤 13 (RAG) → 步骤 14 (知识库导入)

步骤 1 → 步骤 5 (LLM) → 步骤 8 (安全) ─┐
                  ├→ 步骤 6 (Embedding/Rerank) )─┤
                  └→ 步骤 7 (Token) ────────────┘

步骤 12 (分块) + 步骤 13 (RAG) → [阶段五、六并行依赖]

步骤 15 (Agent基类) → 步骤 16 (调度) → 步骤 17-22 (业务Agent，可并行)
步骤 19 (PDF处理) → 步骤 20 (文档Agent)

步骤 23 (API) → 步骤 24 (Dockerfile) → 步骤 26 (集成测试)
```

---

## 阶段九：Web 前端（新增）

> 对标豆包/ChatGPT/DeepSeek 网页版，纯静态前端，无构建工具。

### 步骤 27：后端 — 静态文件服务 + SSE 流式端点

- 在 `src/main.py` 挂载 `static/` 目录
- 在 `src/llm/client.py` 新增 `chat_completion_stream()` 异步生成器
- 在 `src/agents/dispatcher.py` 新增 `dispatch_stream()` 流式调度
- 5 个业务 Agent 全部新增 `stream_execute()` 方法
- 在 `src/api/routes.py` 新增 `POST /api/chat/{session_id}/stream` SSE 端点
- **验证**：`curl -N POST /api/chat/{sid}/stream` 看到逐块到达的 SSE 数据流

### 步骤 28：前端 — 页面布局与样式

- 创建 `static/index.html`（侧边栏 + 聊天区 + 欢迎页 + 快捷操作按钮）
- 创建 `static/css/style.css`（浅色主题、消息气泡、响应式、代码块样式）
- **验证**：浏览器打开 `http://localhost:8000/` 布局完整

### 步骤 29：前端 — 核心交互逻辑

- 创建 `static/js/app.js`
- 实现：会话管理（localStorage）、SSE 流式读取、Markdown 渲染（marked.js）、代码高亮（highlight.js）、复制按钮、PDF 上传、快捷键（Ctrl+N）、移动端侧边栏
- **验证**：发送法律咨询 → 流式显示 → Markdown 正确渲染 → 切换会话正常

### 步骤 30：集成测试

- 前端 + 后端全链路测试（10 项）
- **验证**：全部通过
```

> 标注 `[P]` 的步骤可与前一步并行：步骤 17-22 在步骤 16 完成后可并行开发。
