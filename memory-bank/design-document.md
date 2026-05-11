# LawAgent — 设计文档

## 文档版本

V1.0（基于 PRD V1.0 + 需求澄清）

---

## 1. 范围与目标

### 1.1 项目目标

构建基于多智能体架构的法务智能服务系统，为用户提供法律咨询、案情分析、合同审查、法律文书撰写、文档问答一站式服务。

### 1.2 核心范围

- 安全合规检测：所有用户输入必经安全语义校验
- 多 Agent 任务调度：1 个总调度 + 5 个业务 Agent
- 法律知识库 RAG：法律条文向量检索 + 重排
- 文档处理：PDF 解析 → 分块 → 向量化 → 文档问答 / 合同审查
- 会话记忆管理：短期记忆 + 摘要记忆，200k Token 滑动窗口
- 法律文书生成：模板化生成 Word / Markdown 文书
- 联网搜索：案情分析时搜索类案参考

### 1.3 非目标（V1.0 不做）

- Web 前端界面（预留 API，后续开发）
- 多用户认证与权限系统（当前单用户，session_id 隔离多会话）
- 消息队列 / 异步任务系统（Agent 间函数直接调用）
- 支付 / 计费系统
- 移动端适配

---

## 2. 用户故事与旅程

### 2.1 法律咨询

1. 用户创建会话 → 获得 session_id
2. 用户输入法律问题（如"借钱不还怎么办"）
3. 系统执行安全检测 → 通过
4. 总调度识别意图为"法律咨询" → 路由到法律咨询 Agent
5. 法律咨询 Agent 从 Milvus 检索 Top-10 相关法条
6. 结合法条 + 问题 + 上下文记忆，LLM 生成回答
7. 回答附带法条名称 + 条款号
8. 用户追问 → 总调度识别为追问 → 路由到追问处理 Agent → 延续上下文回答

### 2.2 案情分析

1. 用户输入复杂案情描述（文字或上传文档）
2. 安全检测通过 → 总调度路由到案情分析 Agent
3. Agent 提取案情要素（当事人、时间线、争议焦点等）
4. 触发法律知识库检索 + Tavily 联网类案搜索
5. 整合检索结果 → LLM 生成分析报告（案情梳理 + 法条依据 + 类案参考 + 建议）

### 2.3 文档上传与提问

1. 用户上传 PDF 文档
2. MinerU 解析为 Markdown → 递归分块 → 向量化 → 存入 Milvus
3. 系统标记当前会话 state = "has_document"
4. 用户提问（如"这份合同有什么问题"）
5. 总调度检测到会话有文档 → 路由到文档提问 Agent
6. Agent 区分场景：
   - 文档提问 → 向量检索相关内容 → LLM 回答
   - 合同审查 → 全文读取 Markdown → LLM 全文审查 → 撰写生成风险报告

### 2.4 法律文书撰写

1. 用户提出文书需求（如"帮我写一份借款合同"）
2. 总调度路由到文书撰写 Agent
3. Agent 根据文书类型选择模板 → 引导用户补充必要信息
4. 生成规范化文书 → 用户确认 / 修改
5. 导出为 Markdown 或 Word（docx）

### 2.5 多轮追问

1. 用户在某次 Agent 回答后继续追问或补充信息
2. 总调度的意图识别判断为"追问"而非"新问题"
3. 路由到追问处理 Agent
4. Agent 加载完整会话上下文 → 直接回答，不触发 RAG / 领域逻辑

---

## 3. 核心功能行为

### 3.1 安全检测流程

- **触发时机**：每次用户输入文本（不含已上传文档内容）
- **实现方式**：DeepSeek-V4-Flash + 固定合规检测 Prompt
- **输出**：三选一 — `合法` / `无关` / `违规`
- **响应规则**：
  - `违规` / `无关` → 返回固定提示文本，终止后续处理
  - `合法` → 进入多 Agent 调度
- **兜底规则**：非违规 / 非无关 → 一律判定为合法

### 3.2 意图识别与路由

总调度 Agent 负责：

1. 检查 Redis 当前 session 的 state（有无已上传文档）
2. 有文档 → 优先路由文档提问 Agent
3. 无文档 → LLM 意图识别 → 路由对应业务 Agent：
   - 法律咨询 → 法律咨询 Agent
   - 案情分析 → 案情分析 Agent
   - 文书撰写 → 文书撰写 Agent
   - 追问 / 聊天 → 追问处理 Agent
4. 汇总业务 Agent 返回结果 → 统一返回给用户

### 3.3 记忆与上下文管理

- **上下文窗口**：200k Token 滑动窗口
- **记忆结构**：
  - `short_term_memory`：消息数组 `[{role, content, token_count, timestamp}]`
  - `summary_memory`：摘要文本（初始为空）
- **摘要触发**：窗口占用率达 65% 时，对消息数组中前 5 轮对话生成摘要
- **摘要 Token 上限**：动态计算（长对话自动提升上限）
- **上下文组装顺序**：System Prompt → summary_memory → short_term_memory → 当前 User Prompt，并提示以当前User Propmt为主，其他记忆作为补充
- **存储位置**：Redis（`legal_agent:session:{session_id}`），永不过期
- **持久化**：PostgreSQL 存原始对话消息作为最终备份

### 3.4 RAG 法律知识库

- **知识库来源**：民法典、刑法、劳动合同法、司法解释等现行有效法律，这些以pdf文件存在，目前还没整理出来，请预留放置这些pdf文件的目录。
- **分块策略**：优先按法律章节分块，单章超过阈值则递归切分
- **向量化**：text-embedding-v4（阿里云 DashScope）
- **存储**：Milvus
- **检索**：Top-K = 10（法律咨询），文档问答可自定义
- **重排**：qwen3-rerank（阿里云 DashScope）
- **初始状态**：知识库为空，按需导入法律条文

### 3.5 文档处理

- **支持格式**：PDF（通过 MinerU 解析为 Markdown）
- **分块策略**：按章节 / 段落递归分块，支持重叠切片
- **存储**：
  - 原始 PDF → 本地文件系统（如 `data/uploads/{session_id}/`）
  - 解析后 Markdown → 同上目录
  - 切片原文 + 向量 → Milvus（标量字段存储原文，检索时直接返回）
- **会话状态**：当前会话的"文档" = 上传 PDF + 解析 Markdown + Milvus 向量数据

### 3.6 合同审查

- **区别于文档提问**：不做检索，全量读取合同全文
- **审查内容**：无效条款、霸王条款、高风险条款
- **输出格式**：标准化报告（风险等级、问题条款原文、修改建议、法律依据）

### 3.7 文书生成

- **支持类型**：合同类（劳动合同、借款合同等）、诉讼类（起诉状、答辩状、律师函等）
- **能力**：模板库 + 法言法语润色 + 格式规范化
- **导出格式**：Markdown、Word（docx）

### 3.8 联网搜索

- **服务**：Tavily API
- **使用场景**：案情分析 Agent 搜索类案参考
- **不在其他 Agent 中启用**（避免偏离法律领域）

---

## 4. 数据模型

### 4.1 PostgreSQL — 对话消息表

> PostgreSQL 仅存储对话消息。文档切片内容直接存入 Milvus（见 4.3），不在 PostgreSQL 中冗余存储。

| 字段 | 类型 | 说明 |
|---|---|---|
| id | BIGSERIAL | 主键 |
| session_id | VARCHAR(64) | 会话唯一标识 |
| message_role | VARCHAR(10) | user / ai |
| message_content | TEXT | 消息原文 |
| token_count | INT | Token 数 |
| create_time | TIMESTAMP | 创建时间 |
| message_type | VARCHAR(20) | 咨询 / 文档 / 文书 / 案情 / 追问 |

### 4.2 Redis — 会话记忆

- **Key 格式**：`legal_agent:session:{session_id}`
- **Value（JSON）**：
  ```json
  {
    "short_term_memory": [
      {"role": "user", "content": "...", "token_count": 50, "timestamp": "..."},
      {"role": "ai", "content": "...", "token_count": 200, "timestamp": "..."}
    ],
    "summary_memory": "",
    "window_token_count": 250,
    "state": "idle",
    "has_document": false,
    "document_name": null
  }
  ```
- **过期策略**：无过期，永久存储

### 4.3 Milvus — 向量集合

> 文档切片原文直接存入 Milvus 标量字段，检索时随向量结果一并返回，无需回查 PostgreSQL。

- **法律知识库 Collection**：`legal_knowledge`
  - Fields: `id`, `chunk_text`, `law_name`, `chapter`, `article_number`, `vector`
- **文档向量 Collection**：`session_documents`
  - Fields: `id`, `session_id`, `document_name`, `chunk_text`, `chunk_index`, `vector`

### 4.4 文件系统

```
data/
├── uploads/
│   └── {session_id}/
│       ├── original.pdf       # 原始 PDF
│       └── parsed.md          # MinerU 解析结果
├── laws/                      # 待导入的法律 PDF 原文
│   └── *.pdf
└── templates/
    └── {template_name}.md     # 文书模板
```

---

## 5. 边界情况

### 5.1 输入边界

- 空输入 → 返回提示"请输入法律问题"
- 超长输入（接近 200k Token）→ 截断 + 警告
- 非中文输入 → 正常处理（安全检测会判定为"无关"或"合法"）
- 上传非 PDF 文件 → 返回"仅支持 PDF 格式"

### 5.2 会话边界

- 不存在的 session_id → 自动创建新会话
- 会话无记忆 → 仅使用 System Prompt + 当前问题
- Redis 数据丢失 → 从 PostgreSQL 恢复（最近 N 轮）
- 知识库为空 → 跳过 RAG，LLM 基于自身知识回答 + 提示"法律知识库尚未初始化"

### 5.3 并发边界

- 同一 session 并发请求 → 排队处理（无消息队列，依赖 FastAPI 异步 + Redis 锁）
- 同一 PDF 被多个 session 上传 → 各自独立处理

### 5.4 错误处理

- LLM 调用超时 / 失败 → 返回"服务暂时不可用，请稍后重试"
- Milvus 不可用 → 降级为 LLM 直接回答，跳过 RAG
- Redis 不可用 → 从 PostgreSQL 恢复会话记忆
- PostgreSQL 不可用 → 严重错误，返回"系统故障"

---

## 6. 验收标准

### 6.1 安全模块

- [x] 违规输入被拦截，返回固定提示
- [x] 无关输入被拦截，返回固定提示
- [x] 合法法律问题正常通过
- [x] 寒暄开场白判定为合法
- [x] 越狱 Prompt 被识别为违规

### 6.2 法律咨询

- [x] 基础法律问题返回正确解答
- [x] 回答附带了法条名称 + 条款号
- [x] 无知识库时仍可回答（LLM 自身知识）
- [x] 有知识库时回答引用了检索到的法条

### 6.3 案情分析

- [x] 提取了当事人、时间线、争议焦点等要素
- [x] 包含了法条参考
- [x] 联网搜索返回了类案信息
- [x] 给出了初步分析意见 + 建议

### 6.4 文档处理

- [x] PDF 成功解析为 Markdown
- [x] Markdown 成功分块并向量化
- [x] 文档提问返回了相关内容
- [x] 合同审查输出了完整风险报告

### 6.5 文书撰写

- [x] 按模板生成了规范文书
- [x] 支持 Markdown 导出
- [x] 支持 Word（docx）导出
- [x] 支持用户自定义修改

### 6.6 会话管理

- [x] 多轮对话上下文正确维护
- [x] 摘要触发后上下文未丢失关键信息
- [x] 追问不触发新的 Agent 路由
- [x] 会话恢复时记忆正确加载
