# LawAgent — 技术栈

## 文档版本

V1.0

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
| 向量数据库 | Milvus | pymilvus (Milvus Lite / Standalone) | PRD 指定，支持向量检索 + 标量过滤 |

> **Milvus 部署**：开发阶段用 Milvus Lite（嵌入模式），生产用 Docker 部署 Milvus Standalone。

## 4. AI 模型

| 用途 | 模型 | API 端点 | 理由 |
|---|---|---|---|
| 主推理 LLM | DeepSeek-V4-Flash | DeepSeek API | PRD 指定，高性能低成本 |
| 安全检测 LLM | DeepSeek-V4-Flash | DeepSeek API | 与主模型统一，减少供应商依赖 |
| Embedding | text-embedding-v4 | `https://dashscope.aliyuncs.com/compatible-mode/v1` | 阿里云 DashScope，中文法律文本效果好 |
| Rerank | qwen3-rerank | `https://dashscope.aliyuncs.com/api/v1/services/rerank/text-rerank/text-rerank` | 阿里云 DashScope，与 embedding 同生态 |

## 5. 文档处理

| 用途 | 选择 | 理由 |
|---|---|---|
| PDF 解析 | MinerU | PRD 指定，PDF → Markdown 转换质量高 |
| Word 生成 | python-docx | PRD 指定，纯 Python 实现，无外部依赖 |
| Markdown 渲染 | Python 标准库 + 第三方扩展 | 文书导出用 |

## 6. 工具 / 工具库

| 用途 | 选择 | 理由 |
|---|---|---|
| Token 计算 | tiktoken | PRD 指定，OpenAI 兼容 tokenizer |
| 联网搜索 | Tavily API | 用户指定，专为 AI Agent 设计的搜索 API |
| HTTP 客户端 | httpx | FastAPI 生态标准，支持 async |
| 配置管理 | pydantic-settings | 与 FastAPI / Pydantic 深度集成 |
| 日志 | loguru | 比标准 logging 更简洁，结构化日志 |

## 7. 部署

| 层级 | 选择 | 理由 |
|---|---|---|
| 容器化 | Docker + docker-compose | 用户指定，统一管理 Python App + PostgreSQL + Redis + Milvus |
| 反向代理 | Nginx（可选） | 生产环境推荐 |

### docker-compose 服务规划

```
services:
  app:       # FastAPI + Uvicorn
  postgres:  # PostgreSQL 16
  redis:     # Redis 7
  milvus:    # Milvus Standalone (etcd + minio)
```

## 8. 不使用的技术

| 技术 | 原因 |
|---|---|
| 消息队列（RabbitMQ/Kafka） | Agent 间函数直接调用，不需要 |
| Node.js / Go | PRD 指定 Python |
| Pinecone / Weaviate | PRD 指定 Milvus |
| Celery | 无异步任务需求（V1.0 同步请求-响应） |
| OAuth / JWT | 单用户，不需要认证系统 |
