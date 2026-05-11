# LawAgent

多智能体架构法务智能服务系统 —— 一站式法律 AI 助手。

## 功能概览

| 功能 | 说明 |
|---|---|
| 法律咨询 | 基于法律知识库 RAG 检索，回答法律问题并附法条引用 |
| 案情分析 | 提取案情要素，检索法条 + 联网类案搜索，生成结构化分析报告 |
| 文档问答 | 上传 PDF，对文档内容提问，向量检索精准定位 |
| 合同审查 | 全量读取合同文本，输出风险等级、问题条款、修改建议 |
| 文书撰写 | 模板化生成借款合同、劳动合同、起诉状等，支持 Markdown/Word 导出 |
| 追问/聊天 | 多轮对话上下文维护，基于历史记忆回答，不触发新检索 |

**安全机制**：所有用户输入先经合规检测（合法 / 无关 / 违规），违规内容直接拦截。

## 系统架构

```
用户请求 → 安全检测 → 总调度 Agent → 意图识别 → 路由分发
                                           │
              ┌────────────────────────────┼────────────────────────────┐
              ▼                ▼           ▼           ▼                ▼
         法律咨询         案情分析     文档提问    合同审查         文书撰写
         (RAG检索)      (RAG+搜索)   (向量检索)  (全文审查)     (模板填充)
              │                │           │           │                │
              └────────────────┴───────────┴───────────┴────────────────┘
                                           │
                                           ▼
                              追问/聊天 Agent（上下文回答）
```

**数据流**：Query → Embedding (DashScope text-embedding-v4) → Milvus 检索 → Rerank (qwen3-rerank) → LLM 生成 (DeepSeek-V4-Flash) → 回复

## 技术栈

| 层级 | 选型 |
|---|---|
| 语言 | Python 3.10+ |
| API 框架 | FastAPI + Uvicorn |
| 关系型数据库 | PostgreSQL 16（对话消息） |
| 缓存/会话 | Redis 7（会话记忆） |
| 向量数据库 | Milvus 2.4（法律知识库 + 文档向量） |
| LLM | DeepSeek-V4-Flash |
| Embedding | DashScope text-embedding-v4 (1024维) |
| Rerank | DashScope qwen3-rerank |
| PDF 解析 | MinerU / pdfminer.six |
| 联网搜索 | Tavily API |
| 容器化 | Docker + docker-compose |

## 快速开始

### 1. 环境准备

- Python 3.10+
- Docker Desktop
- DeepSeek API Key / DashScope API Key / Tavily API Key

### 2. 启动基础服务

```bash
docker-compose up -d postgres redis etcd minio milvus
```

### 3. 安装依赖

```bash
pip install -r requirements.txt
```

### 4. 配置环境变量

```bash
cp .env.example .env
# 编辑 .env 填入 API Key
```

### 5. 导入法律知识库（可选）

```bash
python scripts/import_laws.py
```

### 6. 启动服务

```bash
python src/main.py
# 或
uvicorn src.main:app --host 0.0.0.0 --port 8000
```

访问 http://localhost:8000/docs 查看 Swagger API 文档。

### Docker 一键启动

```bash
docker-compose up -d
```

## API 端点

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/health` | 健康检查（PG/Redis/Milvus 连通性） |
| POST | `/api/session` | 创建会话，返回 session_id |
| POST | `/api/chat/{session_id}` | 发送消息，返回 AI 回复 + 引用 |
| POST | `/api/upload/{session_id}` | 上传 PDF 文档 |
| GET | `/api/session/{session_id}/history` | 获取会话历史消息 |
| DELETE | `/api/session/{session_id}` | 删除会话 |

### 请求示例

```bash
# 创建会话
curl -X POST http://localhost:8000/api/session

# 发送法律咨询
curl -X POST http://localhost:8000/api/chat/{session_id} \
  -H "Content-Type: application/json" \
  -d '{"message": "借钱不还怎么处理？"}'

# 上传合同 PDF
curl -X POST http://localhost:8000/api/upload/{session_id} \
  -F "file=@contract.pdf"

# 获取历史
curl http://localhost:8000/api/session/{session_id}/history
```

## 项目结构

```
LawAgent/
├── src/
│   ├── main.py              # FastAPI 入口
│   ├── config.py            # 配置管理
│   ├── api/                 # 路由与数据模型
│   ├── agents/              # 6 个 Agent（调度 + 5 业务）
│   ├── llm/                 # LLM/Embedding/Rerank 客户端
│   ├── rag/                 # RAG 检索管道
│   ├── memory/              # 上下文管理（短期+摘要，200k 窗口）
│   ├── database/            # PostgreSQL + Redis
│   ├── vector_db/           # Milvus 连接与 Collection
│   ├── document/            # PDF 解析与文档处理
│   ├── tools/               # 联网搜索 + 模板管理
│   ├── security/            # 合规安全检测
│   └── utils/               # Token 计数 + 文本分块
├── data/
│   ├── laws/                # 法律知识库原文
│   ├── templates/           # 文书模板
│   ├── uploads/             # 用户上传文档
│   └── test/                # 测试文件
├── tests/                   # 集成测试（27 用例）
├── memory-bank/             # 设计文档 / 技术栈 / 实施计划
├── migrations/              # SQL 迁移
├── scripts/                 # 知识库导入工具
├── docker-compose.yml       # Docker 编排
├── Dockerfile               # 应用镜像
├── requirements.txt         # Python 依赖
└── .env.example             # 环境变量模板
```

## 配置项

| 变量 | 说明 | 默认值 |
|---|---|---|
| `DEEPSEEK_API_KEY` | DeepSeek API 密钥 | — |
| `DASHSCOPE_API_KEY` | 阿里云 DashScope API 密钥 | — |
| `TAVILY_API_KEY` | Tavily 搜索 API 密钥 | — |
| `POSTGRES_HOST` | PostgreSQL 地址 | localhost |
| `REDIS_HOST` | Redis 地址 | localhost |
| `MILVUS_HOST` | Milvus 地址 | localhost |
| `MAX_CONTEXT_TOKENS` | 上下文窗口上限 | 200000 |
| `SUMMARY_TRIGGER_RATIO` | 摘要触发比例 | 0.65 |
| `APP_PORT` | 服务端口 | 8000 |

完整配置见 `.env.example`。

## 运行测试

```bash
python tests/integration_test.py
```

测试覆盖：安全检测 / 法律咨询 / 案情分析 / 文档处理 / 文书撰写 / 会话管理 / 边界情况，共 27 个用例。

## 后续规划

- Web 前端界面
- 多用户认证与权限
- 消息队列异步处理
- 更多法律文书模板
- 法律知识库扩充（刑法、劳动法等）

## License

MIT
