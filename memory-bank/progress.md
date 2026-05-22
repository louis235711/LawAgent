# Progress Log

（按时间倒序记录每次实现的步骤、结果与验证状态）

---

## 2026-05-15 — Phase 10: ReAct 自主决策 Agent 架构（计划阶段，待实施）

### 架构概述

```
当前:  Dispatcher → Intent Classify (LLM 5分类) → Business Agent (硬编码 RAG→LLM→输出)
                    ↓ 容易分类错误，无法多步组合

目标:  Dispatcher → ReActAgent (单Agent)
                      ↓
                    Loop {
                      Thinking → Tool Use → Observation → Thinking → ... → Final Answer
                    }
                    自主决策：查什么、怎么查、何时结束
                    每一步 Thinking 可被用户看到（增强可信度）
```

**核心变化：**
1. LLM 调用：OpenAI SDK → Anthropic SDK（`/anthropic` 端点，原生 thinking block）
2. 5 个业务 Agent → 5 个 Tool（Anthropic tool_use 格式）
3. 意图分类 Prompt → 取消（ReAct 自主决策）
4. 短期记忆：每轮存 user/AI 消息 → 每轮存完整 ReAct 轨迹（thinking → tool_use → result → ... → text）
5. 压缩机制：按 token 窗口截断 → 按 turn 分组摘要 + thinking block 优先丢弃
6. 前端：纯文本流式 → thinking 折叠区 + 工具调用进度

---

### 步骤 1：LLM Client — Anthropic SDK 接入 + thinking 支持

**文件：`src/llm/client.py`**

**变化**：保留现有 OpenAI SDK 调用（RAG/embedding/rerank/安全检测不变），新增 Anthropic SDK 调用路径供 ReAct 使用。

**新增依赖**：`anthropic >= 0.49.0`（`requirements.txt`）

**新增函数**：

```python
from anthropic import AsyncAnthropic, APIError as AnthropicError

_anthropic_client: AsyncAnthropic | None = None

def get_anthropic_client() -> AsyncAnthropic:
    global _anthropic_client
    if _anthropic_client is None:
        _anthropic_client = AsyncAnthropic(
            api_key=settings.deepseek_api_key,
            base_url="https://api.deepseek.com/anthropic",
        )
    return _anthropic_client


async def chat_completion_react(
    messages: list[dict],        # Anthropic 格式: [{"role":"user","content":"..."}]
    system: str,                 # System prompt（Anthropic 的 system 是独立参数）
    tools: list[dict],           # Anthropic tool 定义
    model: str = "deepseek-v4-flash",
    max_tokens: int = 4096,
    thinking_budget: int = 2048, # thinking token 预算
    max_retries: int = 3,
) -> dict:
    """ReAct 专用：带 thinking block 的非流式调用。

    返回 Anthropic Message 对象：
    {
      "content": [
        {"type": "thinking", "thinking": "我需要先检索..."},
        {"type": "tool_use", "id": "toolu_xxx", "name": "search_laws", "input": {...}}
      ]
    }
    或
    {
      "content": [
        {"type": "thinking", "thinking": "信息充分，可以回答了"},
        {"type": "text", "text": "根据《民法典》..."}
      ]
    }
    """
    client = get_anthropic_client()
    for attempt in range(max_retries):
        try:
            response = await client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=system,
                messages=messages,
                tools=tools,
                thinking={"type": "enabled", "budget_tokens": thinking_budget},
            )
            return response
        except AnthropicError as e:
            ...
    raise RuntimeError(...)


async def chat_completion_react_stream(
    messages: list[dict],
    system: str,
    tools: list[dict],
    model: str = "deepseek-v4-flash",
    max_tokens: int = 4096,
    thinking_budget: int = 2048,
    max_retries: int = 3,
):
    """ReAct 专用：带 thinking block 的流式调用。

    Yield 事件类型：
    - {"type": "thinking_delta", "thinking": "..."}        — thinking 增量
    - {"type": "text_delta", "text": "..."}                 — 文本增量（最终回答）
    - {"type": "tool_use", "id": "...", "name": "...", "input": {...}}  — 工具调用
    - {"type": "done"}                                      — 消息结束
    """
```

**关键细节**：
- Anthropic 消息内容是数组格式：`content: [{type:"text"/"thinking"/"tool_use", ...}]`
- `system` 是 `messages.create()` 的独立参数，不是 message
- `thinking.budget_tokens` 控制思考长度上限
- **多轮回传规则**：下一轮 messages 中必须保留上一轮的 `thinking` + `tool_use` + `tool_result` 完整 block，否则 DeepSeek Anthropic 端点返回 400

**验证标准**：
- 发送含 tool 定义的请求 → 返回 content 包含 thinking + tool_use
- 发送不需要 tool 的请求 → 返回 content 包含 thinking + text
- 流式模式正确输出 thinking_delta → tool_use → done 事件序列

---

### 步骤 2：Tool Registry — Anthropic 工具格式定义与执行

**新建文件：`src/tools/registry.py`**

使用 Anthropic tool_use 格式（与 OpenAI function calling 结构不同）：

```python
# Anthropic tool 格式
TOOLS = [
    {
        "name": "search_laws",
        "description": "搜索中国法律知识库，获取相关法条原文、章节和条款号。调用时机：用户咨询具体法律问题时，必须先检索再回答。",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "法律检索查询，使用法律专业术语，如'竞业限制 违约金'而非'不让去同行上班'",
                },
                "top_k": {
                    "type": "integer",
                    "description": "返回结果数量，默认5条",
                    "default": 5,
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "search_cases",
        "description": "联网搜索类似案例的判决结果和处理方式。调用时机：案情分析、类案参考、实务倾向判断。",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "案例搜索查询，如'民间借贷 利息 判决'",
                },
                "max_results": {
                    "type": "integer",
                    "description": "最大结果数，默认5条",
                    "default": 5,
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "search_documents",
        "description": "在用户上传的文档中搜索相关内容（向量+关键词检索）。调用时机：用户针对已上传文档提问。",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "文档搜索查询，提取问题中的关键概念",
                },
                "top_k": {
                    "type": "integer",
                    "description": "返回结果数量，默认5条",
                    "default": 5,
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "read_document_full",
        "description": "读取用户上传文档的全文。调用时机：合同审查、需要完整阅读文档而非片段检索时。",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "generate_document",
        "description": "根据用户需求生成完整的法律文书（合同、起诉状、律师函、申请书等）。调用时机：用户明确要求起草/撰写/生成文书。",
        "input_schema": {
            "type": "object",
            "properties": {
                "requirements": {
                    "type": "string",
                    "description": "文书需求描述，包含文书类型、当事人信息、关键条款等。越详细越好。",
                },
                "format": {
                    "type": "string",
                    "enum": ["md", "docx", "txt"],
                    "description": "输出格式，默认md",
                },
            },
            "required": ["requirements"],
        },
    },
]
```

**工具执行器**（不变）：

| 工具名 | 执行函数 | 来源 |
|---|---|---|
| `search_laws` | `execute_search_laws(query, top_k, session_id)` | 调用 `retrieve_legal()` |
| `search_cases` | `execute_search_cases(query, max_results)` | 调用 `search_web()` |
| `search_documents` | `execute_search_documents(query, session_id, top_k)` | 调用 `retrieve_session_docs()` |
| `read_document_full` | `execute_read_document_full(session_id)` | 调用 `DocumentQAAgent._load_chunks_ordered()` |
| `generate_document` | `execute_generate_document(requirements, format, session_id)` | 调用 `DocumentWritingAgent._generate()` 逻辑 |

```python
async def execute_tool(name: str, input_: dict, session_id: str) -> str:
    """执行工具并返回序列化结果字符串。失败时返回错误描述文本（不抛异常）。"""

def format_tool_result(tool_name: str, result_str: str, tool_use_id: str) -> dict:
    """格式化为 Anthropic tool_result content block."""
    return {
        "type": "tool_result",
        "tool_use_id": tool_use_id,
        "content": result_str,
    }
```

**验证标准**：同前，5 个工具独立调用正确，结果序列化正确。

---

### 步骤 3：ReAct Agent — 显式 thinking 循环

**新建文件：`src/agents/react_agent.py`**

**核心循环（Anthropic 原生 thinking）**：

```
1. 构建 system prompt（含工具使用原则 + 会话状态）
2. 构建 messages = context_messages + current_user_msg
3. 保存 user_input 到短期记忆 (turn_id=T)
4. 创建 turn 的消息收集器: turn_messages = []

5. for iteration in 1..MAX_ITERATIONS (8):
   a. 调用 chat_completion_react(messages, system, tools, thinking_budget=2048)

   b. 解析 response.content:
      content_blocks = response.content
      # 例如: [{"type":"thinking","thinking":"需要检索法条..."},
      #        {"type":"tool_use","id":"toolu_1","name":"search_laws","input":{...}}]

   c. 提取 thinking block(s):
      保存到短期记忆（step_type: "thinking", content: thinking_text）
      流式 yield {"type": "thinking", "text": thinking_text}

   d. if response 包含 tool_use blocks:
      for each tool_use:
        yield {"status": "tool_call", "tool": name, "input": input}
        result = await execute_tool(name, input, session_id)
        yield {"status": "tool_result", "tool": name, "summary": summarize(result)}

      将 assistant 完整 content 追加到 turn_messages
      将 tool_result blocks 追加到 turn_messages
      将 assistant content + tool_results 追加到 messages（供下一轮）
      continue 下一轮迭代

   e. else (response 包含 text block，无 tool_use):
      这是最终回答
      text = 拼接所有 text blocks
      保存 final_answer 到短期记忆
      save_turn_to_memory(turn_id, turn_messages)  # 将整轮轨迹一次性写入记忆
      流式 yield text chunks
      yield AgentResponse(content=text, references=extract_refs(), metadata=...)
      return

6. 达到 MAX_ITERATIONS:
   去掉 tools，追加 "请基于以上检索结果给出最终回答"
   调用 chat_completion_react（不带 tools）
   流式 yield 文本
   return
```

**System Prompt**：

```markdown
你是专业法律AI助手，你可以：
- 自主思考（thinking）：在调用工具前先分析需求、规划步骤
- 使用工具：检索法条、搜索案例、查询文档、生成文书
- 综合回答：基于工具结果给出最终法律意见

## 工作流程
1. 先 thinking 分析用户需求，判断需要什么信息
2. 调用必要的工具获取信息
3. 评估工具结果是否充分 → 不够继续查，够了给最终回答

## 工具使用原则
- 法律咨询 → 先 search_laws
- 案情分析 → search_laws + search_cases
- 文书撰写 → 直接 generate_document
- 文档相关 → 先 search_documents，审查用 read_document_full
- 寒暄/追问/聊天 → 直接回答，不调工具
- 工具结果充分后立即给出最终回答，不要无意义循环

## 输出要求
- 引用法条标注名称+条款号
- 不确定时诚实告知
- 不得编造法条或案例

## 当前会话状态
{session_state}
```

**流式事件类型总览**（前端接收）：

| 事件 | 前端展示 |
|---|---|
| `{"type": "thinking", "text": "..."}` | 折叠区："🤔 正在思考..." |
| `{"status": "tool_call", "tool": "search_laws", "input": {...}}` | "🔍 正在检索法条..." |
| `{"status": "tool_result", "tool": "search_laws", "summary": "找到3条"}` | "✅ 检索完成，找到3条" |
| `{"delta": "文本片段..."}` | 流式文字渲染 |
| `{"done": true, ...}` | 完成 |

**references 自动提取**：
从所有 `search_laws` 的 tool_result 中提取法条名+条款号，从 `search_cases` 中提取案例标题+URL，填充 `AgentResponse.references`。

**记忆写入策略**：
每轮完整的 ReAct 轨迹（thinking + tool_use + tool_result + text）在 turn 结束时**一次性写入** short_term_memory，而非每一步单独写。这样压缩时天然按 turn 分组，且避免了"写了一半 turn 就触发压缩"的问题。

**验证标准**：
- "借钱不还怎么办" → thinking 推理 → search_laws → thinking 评估 → 最终回答
- "帮我分析劳动纠纷" → search_laws + search_cases → 完整分析报告
- "你好" → thinking（无需工具）→ 直接回复
- 8 轮未结束 → 强制总结
- thinking 内容在前后端正确传递

---

### 步骤 4：短期记忆重构 — 保存完整 ReAct 轨迹（含 thinking）

**修改文件：`src/memory/context_manager.py`**

**新消息结构**：

```json
// 用户输入
{"role": "user", "content": "...", "token_count": 50,
 "turn_id": "t_a1b", "step_type": "user_input"}

// AI thinking
{"role": "ai", "content": "用户问民间借贷...需要检索民法典合同编...",
 "token_count": 80, "turn_id": "t_a1b", "step_type": "thinking"}

// AI tool_use（摘要形式存储，完整 tool_use 存 Anthropic 格式 messages）
{"role": "ai", "content": "search_laws(query='民间借贷 违约责任')",
 "token_count": 20, "turn_id": "t_a1b", "step_type": "tool_call",
 "tool_name": "search_laws"}

// tool_result
{"role": "tool", "content": "《民法典》第679条: ...\n\n第680条: ...",
 "token_count": 300, "turn_id": "t_a1b", "step_type": "observation",
 "tool_name": "search_laws"}

// 最终回答
{"role": "ai", "content": "根据《民法典》第679条...",
 "token_count": 200, "turn_id": "t_a1b", "step_type": "final_answer"}
```

**新增 API**：

| 函数 | 说明 |
|---|---|
| `new_turn_id() -> str` | 生成 UUID 前 8 位作为 turn_id |
| `save_turn(session_id, turn_messages: list[dict])` | 一次性写入整轮的 ReAct 轨迹到 Redis + PG |
| `get_turns(session_id) -> list[list[dict]]` | 按 turn_id 分组返回消息 |
| `assemble_anthropic_context(session_id, system, current_input) -> tuple[str, list[dict]]` | 组装为 Anthropic 格式的 (system, messages) |

**assemble_anthropic_context 关键逻辑**：

```python
async def assemble_anthropic_context(
    session_id: str, system_prompt: str, current_input: str
) -> tuple[str, list[dict]]:
    """
    返回 (system, messages) 供 Anthropic messages.create() 使用。

    格式转换：
    - Redis 中的 thinking → Anthropic thinking block
    - Redis 中的 tool_call → Anthropic tool_use block
    - Redis 中的 observation → Anthropic tool_result block
    - Redis 中的 final_answer → Anthropic text block
    """
    data = await get_session(session_id)
    system = system_prompt + "\n\n" + CONTEXT_INSTRUCTION

    messages = []
    turns = _group_by_turn(data["short_term_memory"])

    for turn in turns[:-1]:  # 历史 turns → user 消息 + assistant 消息
        messages.extend(_turn_to_anthropic_messages(turn))

    # 最后一个 turn 的 assistant 部分尚未生成（当前正在处理）
    # 只需加 user message
    last_turn = turns[-1] if turns else []
    user_msg = _extract_user_message(last_turn)
    messages.append(user_msg)

    # 当前输入
    messages.append({"role": "user", "content": f"【当前问题】\n{current_input}"})

    return system, messages


def _turn_to_anthropic_messages(turn: list[dict]) -> list[dict]:
    """将一轮 turn 的 Redis 消息转换为 Anthropic 格式的 message list。

    Redis 存储格式:
      [{role:user, step_type:user_input, content:...},
       {role:ai, step_type:thinking, content:...},
       {role:ai, step_type:tool_call, content:..., tool_name:...},
       {role:tool, step_type:observation, content:..., tool_name:...},
       {role:ai, step_type:final_answer, content:...}]

    Anthropic 输出格式:
      [{"role": "user", "content": "..."},
       {"role": "assistant", "content": [
          {"type": "thinking", "thinking": "..."},
          {"type": "tool_use", "id": "toolu_1", "name": "...", "input": {...}},
        ]},
       {"role": "user", "content": [
          {"type": "tool_result", "tool_use_id": "toolu_1", "content": "..."},
        ]},
       {"role": "assistant", "content": [
          {"type": "thinking", "thinking": "..."},
          {"type": "text", "text": "..."},
        ]}]
    """
```

**关键约束 — thinking 回传**：
DeepSeek Anthropic 端点要求多轮对话中上一轮的 `thinking` block **不能丢弃**（否则 400 错误）。`_turn_to_anthropic_messages()` 必须保留所有 thinking block。

**PostgreSQL migration（`migrations/002_react_memory.sql`）**：同前，新增 turn_id/step_type/tool_name 三列，扩展 message_type CHECK。

**验证标准**：
- 一轮 ReAct 后，Redis 包含完整 thinking → tool_call → observation → final_answer
- `assemble_anthropic_context()` 输出正确的 Anthropic 格式
- 多轮回传不报 400
- 旧会话兼容

---

### 步骤 5：记忆压缩机制重构 — thinking 优先丢弃

**修改文件：`src/memory/context_manager.py`**

**压缩策略（借鉴 Claude Code 的压缩优先级）**：

```
1. 按 turn_id 分组
2. 计算每组 turn 的 token 总数
3. 从最早 turn 开始累计，直到累计 >= min_batch_tokens（40K）
4. 对取出的完整 turn(s) 压缩：

   压缩优先级（越高越先丢弃）：
   ① thinking block — 最占 token，上下文窗口中最不需要原文
   ② tool_call 细节 — 保留摘要即可（"调用了 search_laws"）
   ③ observation — 提取关键法条/案例到摘要
   ④ final_answer — 保留核心结论
   ⑤ user_input — 保留核心诉求

5. 压缩后存入 summary_list，原 turns 从 short_term_memory 删除
6. 至少保留最近 KEEP_RECENT_TURNS（2）个 turn 不压缩
```

**新摘要 Prompt**：

```
请对以下对话轮次进行摘要。每轮包含用户问题、AI思考过程、工具调用结果、最终回答。

## 摘要要求
1. 用户核心诉求和关键事实
2. AI 思考的主要方向（从 thinking 中提取思路要点）
3. 使用的工具和获取的关键信息（保留法条名+条款号）
4. 最终回答的核心结论

注意：thinking 只提炼思路要点，不需要原文照录。但法条引用必须保留具体名称和条款号。

{formatted_turns}
```

**上下文组装变化**：

```
当前: System Prompt → summary_list → 近期对话回顾 → 当前问题
新:   System Prompt → summary_list → [Turn N-1 完整 Anthropic 消息] → [Turn N 完整 Anthropic 消息] → 当前问题
```

**token 窗口截断策略**（超 200K 时）：

| 优先级 | 操作 | 说明 |
|---|---|---|
| 1 | 截断最早 turn 的 thinking | thinking 原文很长，先截 |
| 2 | 截断最早 turn 的 observation | tool 结果可被摘要替代 |
| 3 | 移除最早 turn 完整 | 整个 turn 放入 summary_list |

**验证标准**：
- 3 轮 ReAct 后触发压缩 → thinking 被提炼为思路要点
- 法条引用在摘要中保留
- 压缩后上下文组装正确，多轮回传不报错

---

### 步骤 6：Dispatcher 简化

**修改文件：`src/agents/dispatcher.py`**

与上一版相同：
- 删除 `INTENT_PROMPT`、`DOCUMENT_INTENT_PROMPT`、`_keyword_precheck`、`_classify_intent`、`_classify_document_intent`
- 删除 `_agent_registry`、`register_agent`
- `dispatch()` / `dispatch_stream()`:
  1. 安全检测
  2. 写入用户消息到记忆
  3. 检查 `session.has_document`（构建 session_state 字符串）
  4. **组装 Anthropic 格式 context**（调用 `assemble_anthropic_context`）
  5. 调用 `ReActAgent.stream_execute(session_id, user_input, system, messages)`
  6. 写入 AI 回复到记忆

**简化后的代码**：

```python
class DispatcherAgent:
    def __init__(self):
        self.react_agent = ReActAgent()

    async def dispatch(self, session_id, user_input):
        # 1. Persist user message (new turn)
        turn_id = new_turn_id()
        await add_memory_entry(session_id, "user", user_input, turn_id, "user_input")
        # 2. Check session state
        session = await get_session(session_id)
        has_document = session.get("has_document", False) if session else False
        # 3. Assemble Anthropic context
        system, messages = await assemble_anthropic_context(
            session_id, _build_system_prompt(has_document), user_input
        )
        # 4. Execute ReAct
        response = await self.react_agent.execute(session_id, user_input, system, messages, turn_id)
        # 5. Persist AI response (turn already saved by react_agent)
        return response

验证标准：同前。
```

### 步骤 7：main.py — 注册简化

**修改文件：`src/main.py`**

```python
def _register_agents():
    # 旧 5 个 Agent 不再注册。仅注册兜底 Agent。
    register_agent("react", ReActAgent())
    logger.info("ReActAgent registered")
```

或更直接：dispatcher 不再需要 registry，硬编码 ReActAgent。

---

### 步骤 8：API Routes — 处理 thinking + tool 事件

**修改文件：`src/api/routes.py`**

`chat_stream` 端点处理新事件类型：

```python
async def generate():
    async for item in dispatcher.dispatch_stream(session_id, user_input):
        if isinstance(item, AgentResponse):
            yield f"data: {json.dumps({
                'done': True, 'content': item.content,
                'references': item.references, 'metadata': item.metadata
            }, ensure_ascii=False)}\n\n"
        elif isinstance(item, dict):
            # item 可能是:
            # {"type": "thinking", "text": "..."}
            # {"status": "tool_call", "tool": "...", "input": {...}}
            # {"status": "tool_result", "tool": "...", "summary": "..."}
            yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"
        else:
            # text delta
            yield f"data: {json.dumps({'delta': item}, ensure_ascii=False)}\n\n"
```

---

### 步骤 9：前端 — thinking 折叠 + 工具进度

**修改文件：`static/js/app.js`** 和 **`static/css/style.css`**

**新增 UI 元素**：

```
╔══════════════════════════════╗
║ 🤔 思考中...                 ║  ← 可折叠，默认展开（点击收起）
║   用户问的是民间借贷纠纷，   ║
║   需要检索《民法典》合同编   ║
║   关于借款合同的规定...      ║
╚══════════════════════════════╝
  🔍 正在检索法条...
  ✅ 检索完成，找到 3 条相关法条
╔══════════════════════════════╗
║ 🤔 继续思考...               ║  ← thinking 可有多段
║   检索结果覆盖了合同要件和   ║
║   违约责任，还缺诉讼时效...  ║
╚══════════════════════════════╝
  🔍 正在检索法条...
  ✅ 检索完成，找到 2 条法条

  📋 根据《民法典》第679条...   ← 最终回答流式渲染
```

**JS 逻辑变化**：

```javascript
case "thinking":
    showThinkingBlock(data.text);     // 默认展开，可折叠
    break;
case "tool_call":
    showToolProgress(data.tool, data.input);
    break;
case "tool_result":
    completeToolProgress(data.tool, data.summary);
    break;
case "delta":
    appendTextChunk(data.delta);
    break;
```

**CSS 新增**：
- `.thinking-block` — 浅蓝/浅灰背景，左边框，等宽字体
- `.thinking-block.collapsed` — 收起状态（只显示 "🤔 思考中..."）
- `.tool-progress` — 工具调用行内指示器
- `.tool-progress.completed` — 完成状态（绿色勾）

---

### 步骤 10：旧文件清理

同上版：5 个业务 Agent 文件保留但不再加载。

---

### 步骤 11：集成测试更新

**修改文件：`tests/integration_test.py`**

新增 ReAct 特定测试用例：
- ReAct thinking → search_laws → final_answer 完整链路
- ReAct 多工具组合（search_laws + search_cases）
- thinking 内容正确存储和回传（不报 400）
- 压缩后 thinking 被提炼，法条保留
- 无需工具场景（寒暄无 thinking 或简短 thinking）
- MAX_ITERATIONS 强制结束
- 工具调用失败降级

---

### 步骤 12：文档更新

**修改文件：**
- `memory-bank/architecture.md` — 架构图加入 thinking block、Anthropic 端点、新 SDK
- `memory-bank/design-document.md` — 更新记忆管理、ReAct 决策流程
- `CLAUDE.md` — 更新技术决策

---

### 风险控制

| 风险 | 缓解措施 |
|---|---|
| LLM 无限循环调用工具 | MAX_ITERATIONS=8，超限强制总结 |
| **thinking block 回传 400** | `_turn_to_anthropic_messages()` 严格保留所有 thinking block，测试覆盖多轮场景 |
| thinking 太长撑爆 context | thinking_budget 上限 2048 tokens；压缩时 thinking 优先丢弃 |
| 工具调用结果太长 | 每个 tool_result 截断到 2000 tokens |
| Anthropic SDK 不稳定 | 保留 OpenAI 调用路径用于非 ReAct 场景（安全检测、RAG 等不受影响） |
| 旧会话兼容 | 旧数据无 turn_id，assemble 时自动降级为简单 user/AI 格式 |
| 工具调用耗时过长 | 单工具 30s 超时，失败不阻断后续迭代 |

---

### 文件变更清单

| 操作 | 文件 | 说明 |
|---|---|---|
| 修改 | `src/llm/client.py` | 新增 `chat_completion_react` / `chat_completion_react_stream`（Anthropic SDK） |
| 修改 | `requirements.txt` | 新增 `anthropic >= 0.49.0` |
| **新建** | `src/tools/registry.py` | 5 个工具（Anthropic 格式）+ 执行器 |
| **新建** | `src/agents/react_agent.py` | ReAct 循环（显式 thinking） |
| 修改 | `src/agents/dispatcher.py` | 删除意图分类，Anthropic 上下文组装 |
| 修改 | `src/memory/context_manager.py` | 消息结构（turn_id/step_type）+ `assemble_anthropic_context` + 按 turn 压缩 |
| 修改 | `src/main.py` | 注册简化 |
| 修改 | `src/api/routes.py` | SSE 新增 thinking/tool_call/tool_result 事件 |
| 修改 | `static/js/app.js` | thinking 折叠区 + 工具进度 |
| 修改 | `static/css/style.css` | thinking-block / tool-progress 样式 |
| **新建** | `migrations/002_react_memory.sql` | conversation_messages 新增 3 列 + CHECK 扩展 |
| 修改 | `tests/integration_test.py` | 重写为 ReAct 测试 |
| 修改 | `memory-bank/architecture.md` | 架构更新 |
| 修改 | `memory-bank/design-document.md` | 设计更新 |
| 保留 | `src/agents/legal_consultation.py` 等 5 个文件 | 保留供参考，不再加载 |

---

### 步骤 6：Dispatcher 简化

**修改文件：`src/agents/dispatcher.py`**

**变化：**
- 删除 `INTENT_PROMPT`、`DOCUMENT_INTENT_PROMPT`、`_keyword_precheck`、`_classify_intent`、`_classify_document_intent`
- 删除 `_agent_registry`、`register_agent`
- `dispatch()` / `dispatch_stream()`:
  1. 安全检测（保留，但移到 routes 层做？→ 保留在 dispatcher 中）
  2. 写入用户消息到记忆
  3. 检查 session.has_document（用于工具可用性提示）
  4. 构建 session_state 字符串注入 system prompt
  5. 调用 `ReActAgent.execute()` / `ReActAgent.stream_execute()`
  6. 写入 AI 回复到记忆

**简化后的代码结构：**
```python
class DispatcherAgent:
    def __init__(self):
        self.react_agent = ReActAgent()
    
    async def dispatch(self, session_id, user_input):
        # 1. Persist user message
        # 2. Check session state
        # 3. Execute ReAct
        # 4. Persist AI response
        # 5. Return AgentResponse
    
    async def dispatch_stream(self, session_id, user_input):
        # 同上，流式透传
```

**验证标准：**
- 任意输入 → 直接进入 ReActAgent
- 无意图识别日志
- has_document 正确传递给 agent

---

### 步骤 7：main.py — 注册简化

**修改文件：`src/main.py`**

```python
def _register_agents():
    # 旧: 注册 5+ 个 agent
    # 新: 只注册 ReActAgent（或不需要注册，dispatcher 直接持有）
    register_agent("react", ReActAgent())  # 兜底用
    logger.info("ReActAgent registered")
```

或者更简洁：dispatcher 不再需要 registry，直接硬编码 ReActAgent。

**验证标准：**
- 服务启动无报错
- 旧 agent 文件保留但不再 import

---

### 步骤 8：API Routes — 处理新 SSE 事件类型

**修改文件：`src/api/routes.py`**

`chat_stream` 端点新增处理 `tool_call` 和 `tool_result` 状态事件：

```python
async def generate():
    async for item in dispatcher.dispatch_stream(session_id, user_input):
        if isinstance(item, AgentResponse):
            yield f"data: {json.dumps({'done': True, ...})}\n\n"
        elif isinstance(item, dict):
            # {"status": "tool_call", "tool": "...", "args": {...}}
            # {"status": "tool_result", "tool": "...", "summary": "..."}
            yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"
        else:
            # text chunk
            yield f"data: {json.dumps({'delta': item}, ensure_ascii=False)}\n\n"
```

**验证标准：**
- SSE 流正确传递 tool_call/tool_result 事件
- 前端能解析新事件类型

---

### 步骤 9：前端 — 工具调用进度展示

**修改文件：`static/js/app.js`** 和 **`static/css/style.css`**

**新增 UI 元素：**
- 工具调用进度条/指示器：在 AI 消息气泡上方显示
  ```
  🔍 正在检索法条...
  ✅ 检索完成，找到 3 条相关法条
  🌐 正在搜索类案...
  ✅ 搜索完成，找到 2 个类似案例
  ```
- 工具调用状态用不同颜色/图标区分
- 工具结果可折叠（默认折叠，点击展开查看详情）

**JS 逻辑变化：**
```javascript
case "tool_call":
    showToolProgress(data.tool, data.args);
    break;
case "tool_result":
    updateToolProgress(data.tool, data.summary);
    break;
case "delta":
    appendTextChunk(data.delta);
    break;
```

**前端 pipeline 状态栏更新：**
```
旧: 法律咨询 Agent → RAG检索 → LLM生成
新: ReAct Agent → search_laws → search_cases → 生成回答
```

**验证标准：**
- 发送法律咨询 → 前端显示 "正在检索法条…" → 完成后显示结果数 → 流式显示回答
- 工具调用状态正确折叠/展开
- 快速连续工具调用显示正确

---

### 步骤 10：旧文件清理

**保留但不再加载的 Agent 文件：**
- `src/agents/legal_consultation.py` → 保留，逻辑被 `search_laws` tool 复用
- `src/agents/case_analysis.py` → 保留，逻辑被 `search_laws` + `search_cases` 复用
- `src/agents/document_qa.py` → 保留，逻辑被 `search_documents` + `read_document_full` 复用
- `src/agents/document_writing.py` → 保留，逻辑被 `generate_document` tool 复用
- `src/agents/follow_up.py` → 保留，但 ReActAgent 无工具调用时行为等同于 FollowUp

**不删除原因：** 可供参考、回退、或后续 A/B 对比测试

---

### 步骤 11：集成测试与更新

**修改文件：`tests/integration_test.py`**

- 更新意图识别测试 → ReAct 工具调用测试
- 新增测试用例：
  - ReAct 自动选择工具（法律咨询 → search_laws）
  - ReAct 多工具组合（案情分析 → search_laws + search_cases）
  - ReAct 无需工具（寒暄直接回答）
  - ReAct 达到 max iterations 强制结束
  - 工具调用失败降级
  - ReAct 轨迹完整存入记忆
  - 压缩后关键信息不丢失
- 旧 agent 直接调用的测试 → 改为通过 ReActAgent 调用

**验证标准：** 所有测试通过

---

### 步骤 12：文档更新

**修改文件：**
- `memory-bank/architecture.md` — 更新架构图、Agent 数量、工具列表
- `memory-bank/design-document.md` — 更新 3.2（意图识别→ReAct决策）、3.3（记忆管理）、移除 3.2 中的意图分类描述
- `CLAUDE.md` — 更新关键架构决策

---

### 风险控制

| 风险 | 缓解措施 |
|---|---|
| LLM 无限循环调用工具 | MAX_ITERATIONS=8，超限强制总结 |
| 工具调用结果太长撑爆 context | 每个工具结果截断到 2000 tokens |
| DeepSeek function calling 不稳定 | 保留纯文本兜底（去掉 tools 参数重试一次） |
| 旧会话兼容性 | 新增字段用默认值，旧数据无 turn_id 时自动生成 |
| 工具调用耗时过长（Tavily 超时） | 单工具超时 30s，失败不阻断后续迭代 |
| 流式 tool_calls 拼接错误 | 充分测试 DeepSeek 流式 tool_calls 响应格式 |

---

### 文件变更清单

| 操作 | 文件 | 说明 |
|---|---|---|
| 修改 | `src/llm/client.py` | 新增 `chat_completion_with_tools` / `chat_completion_stream_with_tools` |
| **新建** | `src/tools/registry.py` | 5 个工具定义 + 执行器 |
| **新建** | `src/agents/react_agent.py` | ReAct 循环核心 |
| 修改 | `src/agents/dispatcher.py` | 删除意图分类，简化为 ReAct 透传 |
| 修改 | `src/memory/context_manager.py` | 消息结构 + 按 turn 压缩 |
| 修改 | `src/main.py` | 注册简化 |
| 修改 | `src/api/routes.py` | 新 SSE 事件类型 |
| 修改 | `static/js/app.js` | 工具调用进度 UI |
| 修改 | `static/css/style.css` | 工具状态样式 |
| **新建** | `migrations/002_react_memory.sql` | conversation_messages 新增 3 列 |
| 修改 | `tests/integration_test.py` | 重写为 ReAct 测试 |
| 修改 | `memory-bank/architecture.md` | 架构更新 |
| 修改 | `memory-bank/design-document.md` | 设计更新 |
| 保留 | `src/agents/legal_consultation.py` 等 5 个文件 | 保留供参考，不再加载 |

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
- 安全模块 5/5：违规拦截、无关内容违规拦截、合法通过、寒暄合法、越狱识别
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
- 二分类：合法/违规，无关内容归入违规，兜底规则：非预期输出按合法处理
- **验证**：5 个测试用例全部通过（合法×2、违规×3）

### 步骤 7：实现 Token 计数工具 ✅

- 实现 `src/utils/token_counter.py`（tiktoken cl100k_base，兼容 DeepSeek）
- `count_tokens(text)` 和 `count_message_tokens(messages)` 均可用
- **验证**：中文/长文本计数正常，消息数组 Token 统计正确

### 步骤 6：实现 Embedding 和 Rerank 客户端 ✅

- 实现 `src/llm/embedding.py`（DashScope text-embedding-v4，1024 维）
- 实现 `src/llm/rerank.py`（DashScope gte-rerank-v2）
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

