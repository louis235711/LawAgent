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
├── requirements.txt                       # Python 依赖（13 项，含 anthropic SDK）
├── PRD.md                                 # 原始产品需求文档
├── migrations/
│   ├── 001_init.sql                       # conversation_messages 表结构
│   └── 002_react_memory.sql               # ReAct 记忆扩展（turn_id/step_type/tool_name）
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
│   │   └── style.css                       # 完整样式（响应式/气泡/代码块/thinking/工具进度）
│   └── js/
│       └── app.js                          # 核心逻辑（SSE流式/ReAct thinking/工具进度/Markdown）
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
    │   ├── client.py                      # DeepSeek 双 SDK（OpenAI + Anthropic）/ReAct thinking/streaming/重试
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
    │   ├── dispatcher.py                  # 调度器（简化为 ReAct 透传，无意图分类）
    │   ├── react_agent.py                 # ★ ReAct Agent（单Agent，显式 thinking + tool_use 循环）
    │   ├── legal_consultation.py          # [归档] 法律咨询 Agent
    │   ├── case_analysis.py               # [归档] 案情分析 Agent
    │   ├── document_qa.py                 # [归档] 文档提问/合同审查 Agent
    │   ├── document_writing.py            # [归档] 文书撰写 Agent
    │   └── follow_up.py                   # [归档] 追问处理 Agent
    ├── tools/
    │   ├── __init__.py
    │   ├── registry.py                    # ★ 工具注册表（5工具 Anthropic 格式 + 执行器）
    │   ├── web_search.py                  # Tavily 联网搜索
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

**阶段**：Phase 10 — ReAct 自主决策 Agent 架构（已实施，待集成测试）。
**已完成步骤**：核心 9 步完成（LLM/工具/Agent/记忆/调度/前端/迁移），集成测试 + 文档更新待补。
**最后更新**：2026-05-15（ReAct 架构实施）

## 关键架构决策

1. **ReAct 单 Agent 替代多 Agent**：dispatcher 不再做意图分类，统一由 ReActAgent 自主决策
2. **Anthropic 端点 + 显式 thinking**：使用 DeepSeek `/anthropic` 端点，原生 thinking block，用户可展开查看推理过程
3. **5 工具封装**：search_laws / search_cases / search_documents / read_document_full / generate_document
4. **记忆按 turn 分组**：每轮 ReAct 轨迹（thinking → tool_use → observation → final_answer）存入 structured memory
5. **压缩按 turn 优先丢弃 thinking**：压缩时完整 turn 为单位，thinking 最先降级为摘要
6. **文档切片不存 PostgreSQL**：切片原文直接存入 Milvus 标量字段，检索时一并返回
7. **安全优先**：所有用户输入先过 security guard（三分类），违规/无关即阻塞
8. **PDF 三级降级解析**：MinerU → pdfminer.six → raw text
9. **SSE 流式输出**：支持 thinking_delta / tool_call / tool_result / delta 多种事件
10. **纯静态前端**：HTML+CSS+JS，无构建工具，CDN 引入 marked.js + highlight.js
