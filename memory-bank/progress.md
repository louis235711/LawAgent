# Progress Log

（按时间倒序记录每次实现的步骤、结果与验证状态）

---

## 2026-05-10

### Phase 9: Web 前端 — 4 步全部完成 ✅

**步骤 1：后端 — 静态文件服务 + SSE 流式端点**
- 修改 `src/main.py` 挂载 `static/` 目录到 `/`
- 新增 `src/llm/client.py` 的 `chat_completion_stream()` 流式生成器
- 新增 `src/agents/dispatcher.py` 的 `dispatch_stream()` 流式调度
- 新增 `src/api/routes.py` 的 `POST /api/chat/{session_id}/stream` SSE 端点
- 5 个 Agent 全部新增 `stream_execute()` 方法
- 验证：SSE 端点返回 200 + 74 chunks + done 事件

**步骤 2：前端 — 页面布局与样式**
- 创建 `static/index.html`（侧边栏 + 聊天区 + 欢迎页 + 快捷操作）
- 创建 `static/css/style.css`（ChatGPT/Doubao 风格，响应式布局，消息气泡，代码块）
- 验证：3 个静态文件全部 200

**步骤 3：前端 — 核心交互逻辑**
- 创建 `static/js/app.js`（session 管理/localStorage、SSE 流式显示、Markdown 渲染、代码高亮、复制按钮、PDF 上传、快捷键、自动滚动、移动端适配）
- 验证：10/10 前端+后端集成测试通过

**步骤 4：集成测试**
- SSE 流式输出 1211 chunks → 2157 字符
- Session 创建 → 流式对话 → 历史查询完整链路通
- 前后端路由无冲突（API /api/* + 静态 /）

### 步骤 26：端到端集成测试 ✅

- 编写 `tests/integration_test.py`（27 个测试用例，覆盖全部 6 类验收标准 + 边界情况）
- 安全模块 5/5：违规拦截、无关拦截、合法通过、寒暄合法、越狱识别
- 法律咨询 4/4：RAG 检索 + 法条引用、追问不触发新 Agent
- 案情分析 4/4：结构化报告、法条+建议、类案搜索
- 文档处理 4/4：PDF 解析+分块+入库、文档问答、合同审查、has_document 标记
- 文书撰写 2/2：模板生成、自定义修改
- 会话管理 4/4：多轮上下文、历史查询、删除 404、不存在 session 自动创建
- 边界情况 4/4：空输入、非 PDF 拒绝、超长输入、XSS 安全
- **验证**：27/27 全部通过

### 步骤 25：准备测试数据 ✅

- data/laws/civil_code_contract_sample.md（民法典合同编节选）
- data/test/test_contract.pdf、sample_contract.pdf（测试 PDF）
- data/templates/loan_contract.md、labor_contract.md、complaint.md（文书模板）
- data/uploads/test-session-doc/（已处理示例）
- **验证**：数据齐全，满足所有测试场景

### 步骤 24：编写 Dockerfile 与 docker-compose 集成 ✅

- 编写 `Dockerfile`（Python 3.10-slim、依赖安装、uvicorn 启动）
- 编写 `.dockerignore`
- 更新 `docker-compose.yml`（新增 app 服务、环境变量注入、依赖健康检查、upload_data volume）
- Docker Hub 在此环境不可达（网络限制），但配置语法验证通过
- **验证**：`docker-compose config --quiet` 通过

### 步骤 23：实现 FastAPI 路由 ✅

- 实现 `src/api/schemas.py`（ChatRequest/ChatResponse/SessionResponse/UploadResponse/HistoryMessage/HealthResponse）
- 实现 `src/api/routes.py`（6 端点：health/session/chat/upload/history/delete）
- 实现 `src/main.py`（FastAPI 入口、lifespan 管理、Agent 注册、CORS、请求日志）
- 安全检测集成在 chat 端点（违规→阻塞返回固定提示）
- **验证**：全部 6 端点通过测试；health 200 + 三服务 ok；session 创建+删除；chat 法律咨询返回完整法律意见+7条法条引用；追问正确使用上下文；history 返回4条消息含时间戳和Token数；delete 后404"会话不存在"

### 步骤 22：实现追问处理 Agent ✅

- 实现 `src/agents/follow_up.py`（纯上下文回答，不触发 RAG/工具）
- 修复 LLM 客户端角色转换（ai→assistant）
- **验证**：在法律咨询上下文中追问案例 → 基于上下文给出示例，未触发新检索

### 步骤 21：实现文书撰写 Agent ✅

- 实现 `src/agents/document_writing.py` + `src/tools/template_manager.py`
- 3 个模板（借款合同/劳动合同/起诉状）；关键词匹配选模板 → LLM 填充 → 法言法语润色
- 支持 Markdown + Word(docx) 导出
- **验证**："写一份借款合同"→正确匹配模板→填充张三/李四/5万/6%→生成完整合同

### 步骤 20：实现文档提问与合同审查 Agent ✅

- 实现 `src/agents/document_qa.py`（双场景路由：文档提问 / 合同审查）
- 文档提问：向量检索 → LLM 回答；合同审查：全文读取 → 风险报告
- **验证**："借款金额是多少"→正确返回 ¥100,000；"审查合同"→高风险报告+缺失条款分析

### 步骤 19：实现 PDF 文档处理模块 ✅

- 实现 `src/document/parser.py`（MinerU → pdfminer → raw 三级降级解析）
- 实现 `src/document/processor.py`（保存 → 解析 → 分块 → 向量化 → Redis 标记）
- **验证**：上传 PDF → local 落盘 → 解析 168 chars → 1 chunk 入 Milvus → Redis has_document=true

### 步骤 18：实现案情分析 Agent ✅

- 实现 `src/agents/case_analysis.py` + `src/tools/web_search.py`（Tavily 搜索）
- 报告结构：案情摘要 → 要素提取 → 法律依据 → 类案参考 → 建议
- Tavily 失败时降级（仅用法条）；报告中法条引用正确
- **验证**：借款纠纷案情 → 7 条法条 + 结构化报告

### 步骤 17：实现法律咨询 Agent ✅

- 实现 `src/agents/legal_consultation.py`（RAG 检索 Top-10 + LLM 生成 + 法条标注）
- 无知识库时降级为 LLM 自身知识 + 提示
- **验证**："借钱的诉讼时效"→ 检索 7 条法条，回答引用民法典条款

### 步骤 16：实现总调度 Agent ✅

- 实现 `src/agents/dispatcher.py`（意图识别、文档状态检查、Agent 注册表、路由分发）
- 调度流程：写 Redis → 检查 has_document → LLM 分类 → 路由 → 执行 → 写 AI 回复
- **验证**："借钱不还"→法律咨询，"能详细说说"→追问/聊天

### 步骤 15：实现 Agent 基类 ✅

- 实现 `src/agents/base.py`（BaseAgent 抽象类 + AgentResponse 数据类）
- AgentResponse 含 content、references、metadata、next_actions
- Agent 间函数直接调用，无消息队列
- **验证**：TestAgent 继承 → execute → 返回正确 AgentResponse

### 步骤 14：法律知识库导入工具 ✅

- 实现 `scripts/import_laws.py`（Markdown 法律文本 → 按章节分块 → 向量化 → Milvus）
- 准备民法典·合同编测试数据（7 章/1206 tokens）
- **验证**：导入成功，检索"借款利率"返回借款合同章节（0.7569）

### 步骤 13：实现 RAG 检索管道 ✅

- 实现 `src/rag/pipeline.py`（embed → Milvus search → rerank）
- insert_chunks 批量向量化入库；retrieve 支持 legal_knowledge 和 session_documents
- **验证**：插入 5 条测试法条 → 检索"借钱不还" → Top-3 全为借贷相关

### 步骤 12：实现文档分块工具 ✅

- 实现 `src/utils/text_chunker.py`（递归分块：标题→段落→句子）
- 支持重叠切片（overlap 可配置）；chunk_by_chapter 保留章节标题
- **验证**：法律条文按章节正确分 3 块，每块不超过 max_tokens

### 步骤 11：初始化 Milvus 并创建 Collection ✅

- 实现 `src/vector_db/milvus_client.py`（连接管理、Collection 创建、IVF_FLAT 索引）
- legal_knowledge（chunk_text + law_name + chapter + article_number + vector）
- session_documents（session_id + document_name + chunk_text + chunk_index + vector）
- **验证**：两个 Collection 创建成功，索引就绪

### 步骤 10：实现 PostgreSQL 消息持久化 ✅

- 实现 `src/database/message_repo.py`（save_message、get_messages）
- 与 memory 模块集成：每次 add_message 自动双写 Redis + PostgreSQL
- **验证**：2 条消息写入后，PostgreSQL 与 Redis 数据一致

### 步骤 9：实现记忆与上下文管理 ✅

- 实现 `src/memory/context_manager.py`（短期记忆追加、摘要触发、上下文组装）
- 65% 阈值触发前 5 轮摘要；上下文组装顺序：System → summary → short-term → current
- 超过 200k Token 自动滑动窗口截断（保留 system + 最新对话）
- **验证**：3 轮对话上下文正确组装（6 messages）；短对话不触发摘要

### 步骤 8：实现安全检测模块 ✅

- 实现 `src/security/guard.py`（固定合规检测 Prompt + DeepSeek API）
- 三分类：合法/无关/违规，兜底规则：非预期输出按合法处理
- **验证**：5 个测试用例全部通过（合法×2、无关×2、违规×1）

### 步骤 7：实现 Token 计数工具 ✅

- 实现 `src/utils/token_counter.py`（tiktoken cl100k_base，兼容 DeepSeek）
- `count_tokens(text)` 和 `count_message_tokens(messages)` 均可用
- **验证**：中文/长文本计数正常，消息数组 Token 统计正确

### 步骤 6：实现 Embedding 和 Rerank 客户端 ✅

- 实现 `src/llm/embedding.py`（DashScope text-embedding-v4，1024 维）
- 实现 `src/llm/rerank.py`（DashScope qwen3-rerank）
- 均支持批量输入 + 重试机制
- **验证**：embedding 返回 2×1024 向量；rerank 将法律文档排在前列

### 步骤 5：实现 LLM 客户端封装 ✅

- 实现 `src/llm/client.py`（DeepSeek API v1 封装，兼容 OpenAI SDK）
- 支持 streaming / 非 streaming 两种模式
- 错误重试：最多 3 次，指数退避
- **验证**：调用 `chat_completion` 发送"你好"，收到 21 字符回复

### 步骤 4：初始化 Redis 会话管理 ✅

- 实现 `src/database/redis.py`（异步 Redis 客户端、session CRUD）
- Key 格式：`legal_agent:session:{session_id}`，Value 为 JSON（short_term_memory、summary_memory、state、has_document 等）
- **验证**：create/get/update/delete 全生命周期测试通过，数据跨次读取一致

### 步骤 3：初始化 PostgreSQL 表结构 ✅

- 创建 `migrations/001_init.sql`（conversation_messages 表 + 索引）
- 实现 `src/database/postgres.py`（连接池、init_db 迁移入口）
- **验证**：执行迁移后 `conversation_messages` 表存在，7 个字段类型正确

### 步骤 2：搭建 Docker 开发环境 ✅

- 编写 `docker-compose.yml`，包含 postgres:16、redis:7-alpine、etcd、minio、milvus:v2.4.0
- 所有服务健康检查配置正确
- **验证**：`docker-compose up -d` 全部启动；`docker-compose ps` 显示 5 个服务均为 healthy

### 步骤 1：创建项目骨架 ✅

- 创建完整目录结构（src/ 及 10 个子包、data/ 及 4 个子目录、migrations/、scripts/）
- 创建 `requirements.txt`（17 个依赖全部安装成功）
- 创建 `src/config.py`（pydantic-settings，覆盖 DeepSeek、DashScope、Tavily、PostgreSQL、Redis、Milvus、上下文窗口配置）
- 创建 `.env.example` 模板
- **验证**：`pip install -r requirements.txt` 成功；config 加载并通过属性访问测试

