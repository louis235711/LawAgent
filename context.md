# LawAgent 上下文与记忆系统

## 概述

上下文窗口管理系统负责在 LLM 的 token 限制内，尽可能保留完整的对话上下文。核心思路：**摘要 + 短期记忆双层结构**，按 token 量自适应压缩，长对话不丢历史。

---

## 数据结构

每个会话在 Redis 中维护以下字段（同步持久化到 PostgreSQL `session_memory` 表）：

| 字段 | 类型 | 说明 |
|---|---|---|
| `short_term_memory` | `[{role, content, token_count}]` | 近期对话全文，按时间顺序追加 |
| `summary_list` | `[str, str, ...]` | 历史摘要数组，每次压缩追加一段，最多 20 段 |
| `window_token_count` | `int` | 当前窗口总 token 数 = 所有摘要 tokens + 所有短期记忆 tokens |
| `state` | `"idle" \| "has_document"` | 会话状态（预留扩展） |
| `has_document` | `bool` | 是否已上传文档 |
| `document_name` | `str \| null` | 文档名 |

### 消息格式

```json
{
  "role": "user",
  "content": "咨询内容…",
  "token_count": 234
}
```

---

## 双层记忆模型

```
┌────────────────────────────────────────────────────┐
│ short_term_memory（近期对话原文）                     │
│ [msg1, msg2, msg3, msg4, msg5, msg6, ...]          │
│                                                     │
│  ↑ 超出窗口后触发摘要，头部消息被压缩 ↑                │
│                                                     │
│ summary_list（历史摘要数组）                          │
│ ["第1阶段摘要…", "第2阶段摘要…", ...]                │
└────────────────────────────────────────────────────┘
```

- **short_term_memory**：保留最近 N 条消息的**完整原文**，用于 LLM 回顾最接近的对话上下文
- **summary_list**：每段摘要覆盖一个批次的消息，按时间顺序排列。第 1 段最旧，最后一段最新

---

## 摘要触发机制

### 触发条件

```
window_token_count >= max_context_tokens × summary_trigger_ratio
```

以默认配置为例：`200000 × 0.70 = 140000 tokens`。窗口累计到 14 万 token 时触发。

### 批量选取策略（自适应）

不按固定轮数，而是按**实际 token 量**动态取：

```
从 short_term_memory 头部开始累计
  → 累计 token 数 ≥ min_batch_tokens（40000）
  → 或取到只剩最后 4 条（2 轮）为止
  → 将这些消息送去摘要
```

这样无论消息长短，每次压缩的量是可控的，一次触发就能把窗口从 ~70% 压回 ~50%。

### 摘要生成

- **Prompt**：明确要求输出两部分——「用户在这几轮的主要内容」和「AI 给出的核心法律意见」
- **输出**：固定 2048 tokens
- **温度**：0.3（保证稳定）
- 生成的摘要追加到 `summary_list` 末尾

### 滑动窗口

`summary_list` 最大长度 20。超过 20 段时弹出最旧的一段（FIFO）。

---

## Context 组装

每次 LLM 调用前，`assemble_context()` 按以下优先级组装最终消息列表：

```
1. System Prompt（角色指令 + Context 层次说明）
2. 历史摘要 — 每段摘要作为独立的 system 消息
   【历史摘要—第1阶段】
   【历史摘要—第2阶段】
   ...
3. 短期记忆原文回顾
   【近期对话回顾】
   用户: xxx
   AI: yyy
   ...
4. 当前最新问题（user role）
   【当前最新问题】
```

**优先级原则**：当前最新问题 > 短期记忆原文 > 历史摘要。通过 system 消息中的 `CONTEXT_INSTRUCTION` 向 LLM 明确这一层级关系。

### Token 保护机制

组装后如果超过 `max_context_tokens`，采用**滑动窗口截断**：
- 保留所有 system 消息（角色指令 + 所有摘要）
- 从短期记忆中成对（用户+AI）丢弃最早的消息
- 直到总 token 数回到限制以内

---

## 持久化与容灾

### 双写机制

每次 `update_session()` 同时写入 Redis 和 PostgreSQL：

```
Redis（主读，低延迟） ←→ PostgreSQL `session_memory`（持久化）
```

### 自动恢复

`get_session()` 的读取流程：

```
1. 读 Redis → 命中则直接返回
2. Redis 缺失 → 查询 PostgreSQL session_memory
3. PG 有数据 → 自动重建 Redis，返回数据
4. 都没有 → 返回 None，创建新会话
```

Redis 重启、内存淘汰等场景下，会话记忆不会丢失。

---

## 前端感知

摘要触发时的用户可见行为：

1. 前端发送新消息 → 后端在调度过程中触发摘要
2. SSE 流推送 `{"status": "summarizing"}` 事件
3. 前端 AI 气泡显示 `📝 摘要中...`
4. 摘要完成后开始正常的流式输出

整个过程在意图识别之后、LLM 流式输出之前同步执行，用户感知为短暂的延迟后开始收到回复。

---

## 可配置参数

| 参数 | 默认值 | 说明 |
|---|---|---|
| `max_context_tokens` | 200000 | LLM 最大上下文窗口 |
| `summary_trigger_ratio` | 0.70 | 触发摘要的窗口占用比例 |
| `min_batch_tokens` | 40000 | 每次摘要的最小 token 批量 |

**内部常量**（非配置项）：

| 常量 | 值 | 说明 |
|---|---|---|
| `SUMMARY_OUTPUT_TOKENS` | 2048 | 每段摘要固定输出 token 数 |
| `SUMMARY_MAX_LEN` | 20 | 摘要数组最大长度 |
| `KEEP_RECENT_MESSAGES` | 4 | 摘要后至少保留的原文条数（2 轮） |

---

## 关键文件

| 文件 | 职责 |
|---|---|
| `src/memory/context_manager.py` | 摘要触发、context 组装、消息管理 |
| `src/database/redis.py` | Redis 会话 CRUD、PG 同步 |
| `src/database/session_repo.py` | PostgreSQL `session_memory` 表读写 |
| `src/database/message_repo.py` | PostgreSQL `conversation_messages` 表读写 |
| `src/agents/dispatcher.py` | 调度流程中嵌入摘要检查和状态推送 |
| `src/api/routes.py` | SSE 端点处理 `status`、`delta`、`done` 三种事件 |
| `src/config.py` | 上下文相关配置项 |
| `migrations/001_init.sql` | `conversation_messages` 表 |
| `migrations/002_session_memory.sql` | `session_memory` 表 |
