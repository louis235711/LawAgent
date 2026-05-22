# LawAgent — 技术栈

## 文档版本

V2.0

---

## 1. 运行时

| 层级 | 选择 | 版本 | 理由 |
|---|---|---|---|
| 语言 | Python | 3.10+ | PRD 指定，生态丰富（AI/LLM SDK 最完善） |
| 异步 | asyncio | 标准库 | FastAPI 原生支持，适合 IO 密集型 LLM 调用 |

## 2. 应用框架

| 层级 | 选择 | 理由 |
|---|---|---|
| API 框架 | FastAPI | PRD 指定，高性能异步框架，OpenAPI 自动文档，类型安全 |
| 进程管理 | Uvicorn | FastAPI 推荐的生产级 ASGI server |

## 3. 存储

| 层级 | 选择 | 版本 / 连接方式 | 理由 |
|---|---|---|---|
| 关系型数据库 | PostgreSQL | psycopg2-binary | PRD 指定，仅存储对话消息（文档切片原文存 Milvus） |
| 缓存 / 会话 | Redis | redis-py | PRD 指定，低延迟读写会话记忆，支持 JSON |
| 向量数据库 | Milvus | pymilvus (Milvus Standalone) | PRD 指定，支持向量检索 + 标量过滤，依赖 etcd + MinIO |

## 4. AI 模型

| 用途 | 模型 | API 端点 | 理由 |
|---|---|---|---|
| 主推理 LLM | DeepSeek-V4-Flash | DeepSeek API (OpenAI SDK + Anthropic SDK 双协议) | PRD 指定，OpenAI SDK 用于 embedding/简单调用，Anthropic SDK 用于 ReAct thinking + tool_use |
| 安全检测 LLM | DeepSeek-V4-Flash | DeepSeek API (OpenAI SDK) | 与主模型统一，减少供应商依赖 |
| Embedding | text-embedding-v4 | `https://dashscope.aliyuncs.com/compatible-mode/v1` | 阿里云 DashScope，中文法律文本效果好 |
| Rerank | gte-rerank-v2 | `https://dashscope.aliyuncs.com/api/v1/services/rerank/text-rerank/text-rerank` | 阿里云 DashScope，GTE 系列重排模型 |

## 5. 文档处理

| 用途 | 选择 | 理由 |
|---|---|---|
| PDF 解析 | PyMuPDF + PaddleOCR (Docker 服务) | PDF 页面渲染为图片后 OCR 提取文本 |
| OCR 引擎 | rapidocr-onnxruntime | PaddleOCR 轻量 ONNX 实现，无需 GPU |
| Word 解析 | python-docx | 段落 + 表格提取，保留标题层级 |
| Excel 解析 | openpyxl | 所有 Sheet 转为 Markdown 表格 |
| 图片 OCR | PaddleOCR (Docker 服务) | PNG/JPG 直接 OCR 提取文字 |
| 文本分块 | 自研层次分块器 | 标题 → 段落 → 句子 → 强制切分（512 token/块，96 重叠） |
| 文书生成 | markdown / python-docx / wkhtmltopdf + pdfkit | 支持 md/docx/pdf/txt 四种格式，PDF 支持中文 |
| PDF 渲染 | wkhtmltopdf | WebKit 引擎，@font-face 引入系统中文字体 |

## 6. 工具 / 工具库

| 用途 | 选择 | 理由 |
|---|---|---|
| Agent 框架 | ReAct（自研，无 LangChain） | Anthropic SDK thinking + tool_use 循环，最多 8 轮迭代 |
| LLM 流式输出 | SSE (Server-Sent Events) | 实时逐字推送到前端，支持 thinking 折叠块 |
| 检索管道 | BM25 (jieba) + 向量 (Milvus) → RRF 融合 → Rerank | 混合检索，法律条文 + 用户文档两路并行 |
| 查询改写 | DeepSeek LLM | 口语→法律术语转换，代词消解 |
| Token 计算 | tiktoken | OpenAI 兼容 tokenizer，用于上下文窗口管理 |
| 联网搜索 | Tavily API | 专为 AI Agent 设计的搜索 API |
| 数学计算 | Python eval() (沙箱) | 简单数学表达式 |
| HTTP 客户端 | httpx | FastAPI 生态标准，支持 async |
| HTML/XML 处理 | lxml | python-docx 富文本段落格式化 |
| Markdown→HTML | Python-Markdown | 文书生成中将 LLM 输出的 Markdown 转为 HTML 供 wkhtmltopdf 渲染 |
| 配置管理 | pydantic-settings | 与 FastAPI / Pydantic 深度集成 |
| 日志 | loguru | 比标准 logging 更简洁，结构化日志 |

## 7. 部署

| 层级 | 选择 | 理由 |
|---|---|---|
| 容器化 | Docker + docker-compose | 统一管理 Python App + PostgreSQL + Redis + Milvus + OCR |
| 反向代理 | Nginx（可选） | 生产环境推荐 |
| PDF 生成 | wkhtmltopdf (系统依赖) | WebKit 渲染引擎，需单独安装二进制 |

### docker-compose 服务规划

```
services:
  app:       # FastAPI + Uvicorn
  postgres:  # PostgreSQL 16
  redis:     # Redis 7
  etcd:      # etcd v3.5.5 (Milvus 元数据)
  minio:     # MinIO (Milvus 对象存储)
  milvus:    # Milvus Standalone v2.4
  ocr:       # PaddleOCR (rapidocr-onnxruntime) Docker 服务
```

## 8. 不使用的技术

| 技术 | 原因 |
|---|---|
| 消息队列（RabbitMQ/Kafka） | Agent 间函数直接调用，不需要 |
| Node.js / Go | PRD 指定 Python |
| Pinecone / Weaviate | PRD 指定 Milvus |
| Celery | OCR 已通过独立 Docker 服务异步处理，无需任务队列 |
| LangChain / CrewAI | 自研 ReAct Agent，轻量可控 |
| 多 Agent 路由 | 已简化为单一 ReAct Agent + 工具调用，无需多 Agent 调度 |
| OAuth / JWT | 单用户，不需要认证系统 |
