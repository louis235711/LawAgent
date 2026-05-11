# Architecture — 文件地图

记录项目中每个重要文件和目录的职责。每次重大功能实现后更新。

---

## 当前结构

```
LawAgent/
├── CLAUDE.md                              # Claude Code 工作指引
├── docker-compose.yml                     # Docker 编排（postgres/redis/etcd/minio/milvus/app）
├── Dockerfile                             # 应用容器镜像（Python 3.10-slim + uvicorn）
├── .dockerignore                          # Docker 构建忽略规则
├── .env                                   # 环境变量（API keys、数据库连接）
├── .env.example                           # 环境变量模板
├── .gitignore                             # Git 忽略规则
├── requirements.txt                       # Python 依赖（12 项）
├── PRD.md                                 # 原始产品需求文档
├── migrations/
│   └── 001_init.sql                       # conversation_messages 表结构
├── memory-bank/
│   ├── design-document.md                 # 系统设计文档（范围、用户旅程、验收标准）
│   ├── tech-stack.md                      # 技术栈选型与理由
│   ├── implementation-plan.md             # 实施计划（26 步，全部完成）
│   ├── progress.md                        # 实施进度日志
│   └── architecture.md                    # 本文件
├── scripts/
│   └── import_laws.py                     # 法律知识库导入工具（Markdown → 分块 → Milvus）
├── static/
│   ├── index.html                          # Web 聊天前端（ChatGPT 风格布局）
│   ├── css/
│   │   └── style.css                       # 完整样式（响应式/气泡/代码块）
│   └── js/
│       └── app.js                          # 核心逻辑（SSE流式/Markdown/会话管理）
├── tests/
│   └── integration_test.py                 # 端到端集成测试（27 用例）
├── data/
│   ├── uploads/{session_id}/              # 用户上传 PDF + 解析 Markdown
│   ├── templates/                         # 法律文书模板（借款合同/劳动合同/起诉状）
│   ├── laws/                              # 法律 PDF/Markdown 原文（待向量化导入）
│   └── test/                              # 测试用 PDF 文件
└── src/
    ├── __init__.py
    ├── config.py                          # pydantic-settings 配置（DeepSeek/DashScope/Tavily/DB/窗口参数）
    ├── main.py                            # FastAPI 入口（lifespan/Agent注册/CORS/日志中间件）
    ├── database/
    │   ├── __init__.py
    │   ├── postgres.py                    # PostgreSQL 连接池 + init_db 迁移
    │   ├── redis.py                       # Redis 异步客户端 + 会话 CRUD
    │   └── message_repo.py                # 消息持久化（save/get_messages）
    ├── llm/
    │   ├── __init__.py
    │   ├── client.py                      # DeepSeek API 封装（streaming/非streaming/重试/角色转换）
    │   ├── embedding.py                   # DashScope text-embedding-v4（1024维）
    │   └── rerank.py                      # DashScope qwen3-rerank 重排
    ├── security/
    │   ├── __init__.py
    │   └── guard.py                       # 合规检测（三分类：合法/无关/违规）
    ├── memory/
    │   ├── __init__.py
    │   └── context_manager.py             # 记忆管理（短期/摘要/200k滑动窗口/双写PG）
    ├── vector_db/
    │   ├── __init__.py
    │   └── milvus_client.py               # Milvus 连接 + 2 个 Collection（legal_knowledge/session_documents）
    ├── rag/
    │   ├── __init__.py
    │   └── pipeline.py                    # RAG 管道（embed→Milvus search→rerank→top_k）
    ├── document/
    │   ├── __init__.py
    │   ├── parser.py                      # PDF 解析（MinerU→pdfminer→raw 三级降级）
    │   └── processor.py                   # 文档处理（保存→解析→分块→向量化→Redis标记）
    ├── agents/
    │   ├── __init__.py
    │   ├── base.py                        # Agent 基类 + AgentResponse 数据类
    │   ├── dispatcher.py                  # 总调度（意图识别/文档检查/路由分发/Agent注册表）
    │   ├── legal_consultation.py          # 法律咨询 Agent（RAG检索+LLM生成+法条标注）
    │   ├── case_analysis.py               # 案情分析 Agent（要素提取+RAG+Tavily搜索）
    │   ├── document_qa.py                 # 文档提问/合同审查 Agent（双场景路由）
    │   ├── document_writing.py            # 文书撰写 Agent（模板匹配+LLM填充+法言法语润色）
    │   └── follow_up.py                   # 追问处理 Agent（纯上下文回答，不触发RAG）
    ├── tools/
    │   ├── __init__.py
    │   ├── web_search.py                  # Tavily 联网搜索（仅案情分析 Agent 使用）
    │   └── template_manager.py            # 文书模板管理（加载/列出/选择，3个模板）
    ├── utils/
    │   ├── __init__.py
    │   ├── token_counter.py               # tiktoken cl100k_base 封装
    │   └── text_chunker.py                # 文本分块（四级降级：标题→段落→句子→强制）
    └── api/
        ├── __init__.py
        ├── routes.py                      # FastAPI 路由（6 端点：health/session/chat/upload/history/delete）
        └── schemas.py                     # Pydantic 模型（ChatRequest/ChatResponse/SessionResponse等）
```

## 当前状态

**阶段**：全部步骤完成（含 Web 前端）。
**已完成步骤**：30/30（原 26 步 + 前端 4 步）
**最后更新**：2026-05-10（Web 前端 4 步完成，前后端集成测试通过）

## 关键架构决策

1. **文档切片不存 PostgreSQL**：切片原文直接存入 Milvus 标量字段，检索时一并返回
2. **Agent 间函数直接调用**：无消息队列，dispatcher → agent.execute() → AgentResponse
3. **安全优先**：所有用户输入先过 security guard（三分类），违规/无关即阻塞
4. **上下文以当前问题为主**：组装时明确标注【历史记忆】vs【当前最新问题】
5. **PDF 三级降级解析**：MinerU → pdfminer.six → raw text
6. **分块四级降级**：标题 → 段落 → 句子标点 → 强制 Token 边界切分
7. **SSE 流式输出**：新增 `POST /api/chat/{sid}/stream`，逐 token 推送到前端
8. **纯静态前端**：HTML+CSS+JS，无构建工具，CDN 引入 marked.js + highlight.js
