# LawAgent

多智能体架构法务智能服务系统 —— 一站式法律 AI 助手。

## 功能概览

| 功能 | 说明 |
|---|---|
| 法律咨询 | Query 改写 + BM25/向量双路 RRF 融合 + Rerank 检索，附法条引用 |
| 案情分析 | 提取案情要素，检索法条 + 联网类案搜索，生成结构化分析报告 |
| 文档问答 | 上传文档（PDF/Word/Excel/图片等），对内容提问，混合检索精准定位 |
| 合同审查 | 分块并行审查 + 最终整合报告，风险等级、问题条款、修改建议、原文引用 |
| 文书撰写 | LLM 生成法律文书，支持 pdf/docx/md/txt 导出，PDF 排版美观适合打印，前端一键下载 |
| 追问/聊天 | 多轮对话上下文维护 + 长期用户偏好记忆，不触发新检索 |

**安全机制**：所有用户输入先经合规检测（合法 / 无关 / 违规），违规内容直接拦截。

## 系统架构

```
用户请求 → 安全检测 → ReAct Agent（思考+工具调用循环）
    │                                    │
    ▼                                    ▼
合规拦截(直接拒绝)               thinking → tool_use → tool_result
                                          │            │
                               ┌──────────┴─────┬──────┘
                               ▼                ▼
                         RAG 检索 + 工具     LLM 综合 → 流式输出
```

**架构**：ReAct（Thinking + Tool Use）自主循环，LLM 先思考再决定调用工具或直接回答。工具调用结果反馈给 LLM 综合判断，最多 8 轮迭代，到达上限强制输出。支持流式 SSE 推送 thinking 折叠块 + 文本逐字 + 工具状态。

## 技术栈

| 层级 | 选型 |
|---|---|
| 语言 | Python 3.10+ |
| API 框架 | FastAPI + Uvicorn |
| 前端 | 纯静态 HTML/CSS/JS（marked.js + highlight.js + SSE 流式） |
| 关系型数据库 | PostgreSQL 16（对话消息 + 元数据持久化） |
| 缓存/会话 | Redis 7（短期记忆 + 会话状态） |
| 向量数据库 | Milvus 2.4（法律知识库 + 会话文档向量） |
| LLM | DeepSeek-V4 |
| Embedding | DashScope text-embedding-v4 (1024维) |
| Rerank | DashScope qwen3-rerank |
| OCR | PaddleOCR (Docker 服务) |
| 文档解析 | python-docx / openpyxl / PaddleOCR |
| 文档生成 | python-docx / wkhtmltopdf + pdfkit（PDF 渲染） |
| 联网搜索 | Tavily API |
| 分词 | jieba（BM25 检索） |
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

访问 http://localhost:8000 打开前端界面，或访问 http://localhost:8000/docs 查看 API 文档。

> **Windows 注意**：uvicorn 热重载在 Windows 上有端口绑定问题，项目默认不启用 `--reload`。修改代码后需要手动重启服务。

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
| POST | `/api/chat/{session_id}/stream` | 流式发送消息（SSE），支持逐字输出 |
| POST | `/api/upload/{session_id}` | 上传文档（PDF/Word/Excel/PNG/JPG/MD/TXT） |
| GET | `/api/download/{session_id}/{filename}` | 下载生成的文书文件 |
| GET | `/api/session/{session_id}/history` | 获取会话历史消息 |
| DELETE | `/api/session/{session_id}/document` | 移除会话已上传文档 |
| DELETE | `/api/session/{session_id}` | 删除会话（含 Postgres/Redis/Milvus/磁盘） |

### 请求示例

```bash
# 创建会话
curl -X POST http://localhost:8000/api/session

# 发送法律咨询
curl -X POST http://localhost:8000/api/chat/{session_id} \
  -H "Content-Type: application/json" \
  -d '{"message": "借钱不还怎么处理？"}'

# 上传文档（支持 pdf/docx/xlsx/png/jpg/md/txt）
curl -X POST http://localhost:8000/api/upload/{session_id} \
  -F "file=@contract.docx"

# 流式对话（SSE）
curl -N -X POST http://localhost:8000/api/chat/{session_id}/stream \
  -H "Content-Type: application/json" \
  -d '{"message": "审查这份合同"}'

# 下载生成的文书
curl -O http://localhost:8000/api/download/{session_id}/借款合同.docx

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
│   ├── agents/              # ReAct Agent（自主思考+工具调用）+ 调度器
│   ├── llm/                 # LLM/Embedding/Rerank 客户端
│   ├── rag/                 # RAG 检索管道（BM25+向量+RRF+Rerank+查询改写）
│   ├── memory/              # 上下文管理（短期+摘要+长期偏好记忆）
│   ├── database/            # PostgreSQL + Redis
│   ├── vector_db/           # Milvus 连接与 Collection
│   ├── document/            # 多格式文档解析与处理
│   ├── tools/               # 工具注册表 + 文书生成 + 联网搜索 + 计算器
│   ├── security/            # 合规安全检测
│   └── utils/               # Token 计数 + 文本分块
├── static/                 # 前端静态文件（HTML/CSS/JS）
├── data/
│   ├── laws/                # 法律知识库原文
│   ├── templates/           # 文书模板
│   ├── uploads/             # 用户上传文档
│   ├── generated/           # AI 生成文书
│   ├── memory.md            # 用户长期偏好记忆
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

- 多用户认证与权限
- MCP 协议接入第三方法律服务
- 消息队列异步处理（OCR、文档解析等重任务）
- 法律知识库扩充（刑法、劳动法、公司法等）
- PDF 文档解析与表格提取增强

## License

MIT
