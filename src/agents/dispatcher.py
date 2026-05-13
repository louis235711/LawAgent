from src.agents.base import BaseAgent, AgentResponse
from src.database.redis import get_session
from src.config import settings
from src.llm.client import chat_completion
from src.memory.context_manager import add_message, check_and_summarize, assemble_context
from src.memory.long_term import load_long_term_memory
from loguru import logger

INTENT_PROMPT = """你是法律AI意图识别模块。用户没有上传文档，只从以下类别中选择一个输出：

- 文书撰写：用户要求生成、撰写、起草法律文书（合同、起诉状、律师函、协议、申请书等）。典型表达："生成一篇合同""写一份起诉状""起草协议""帮我写律师函"
- 案情分析：用户描述具体案情经过，要求分析、梳理法律关系或给出处理建议。典型表达："我遇到了这样一个事...""帮我分析这个案子""这种情况怎么处理"
- 法律咨询：用户询问法律问题、法条含义、维权方法、法律程序等一般性问题，没有描述具体案情。典型表达："XX法条是什么意思""工伤怎么认定""离婚需要什么材料"
- 追问/聊天：寒暄、感谢、告别，或对上一条回答的追问、澄清、补充细节

严格只输出一个类别名称，不要解释。

用户输入：{user_input}

类别："""

DOCUMENT_INTENT_PROMPT = """用户已上传文档。只从以下两个类别中选择一个输出：

- 合同审查：用户要求审查、审阅、审核文档中的合同条款，评估法律风险，发现漏洞或不公平条款。典型表达："审查这份合同""帮我看看合同有什么问题""审阅一下风险"
- 文档提问：用户针对文档内容提问、总结、查询、解释、要求摘录等（不含审查意图）。典型表达："文档讲了什么""帮我总结一下""合同里关于违约怎么说的"

严格只输出"合同审查"或"文档提问"，不要解释。

用户输入：{user_input}

类别："""

def _keyword_precheck(user_input: str) -> str | None:
    """Fast keyword-based intent detection, returns intent or None to fall through to LLM."""
    inp = user_input.strip()

    # Document writing patterns
    writing_patterns = [
        "写一篇", "写一份", "写个", "写一个", "写一", "撰写", "起草",
        "帮我写", "给我写", "生成一篇", "生成一份", "生成一个", "生成一",
        "拟一份", "拟一个", "拟写", "草拟",
        "生成报告", "导出报告", "生成md", "导出md",
        "出一篇", "出一份", "输出一份", "输出一篇",
    ]
    for p in writing_patterns:
        if p in inp:
            return "文书撰写"

    # Case analysis patterns (explicit case descriptions)
    case_patterns = ["案情", "案件", "案发", "涉案", "分析一下这个", "帮我分析", "分析我的"]
    for p in case_patterns:
        if p in inp:
            return "案情分析"

    # Chat / greeting patterns — short non-legal inputs
    chat_patterns = ["你好", "您好", "嗨", "hi", "hello", "hey", "谢谢", "多谢", "thank", "thanks", "再见", "bye", "早上好", "晚上好", "下午好", "在吗", "在吗？", "哈喽", "嗨喽"]
    if inp.lower() in [p.lower() for p in chat_patterns]:
        return "追问/聊天"

    return None

# Maps intent → agent
_agent_registry: dict[str, BaseAgent] = {}

SYSTEM_PROMPT = """你是一个专业的法律AI助手，服务于中国法律体系，为用户提供法律咨询、案情分析、文书撰写、合同审查等服务。

## 全局约束

- 仅基于中国现行有效法律法规回答。
- 绝对禁止编造：不得虚构法条名称、条文编号、司法解释文号、案例名称、判决结果。
- 引用法条时必须标注具体法律名称及条款号（格式：《民法典》第XXX条），便于用户核实。
- 对不确定或超出知识范围的问题，必须明确告知"此问题建议咨询持证律师"或"当前未检索到相关法条"，不得猜测或编造。
- 对已废止或可能不再适用的法规，需主动提示用户注意时效性。"""


def _build_system_prompt() -> str:
    """Build the system prompt with long-term user preferences injected."""
    memory_md = load_long_term_memory()
    if memory_md:
        return SYSTEM_PROMPT + "\n\n## 用户偏好（长期记忆）\n" + memory_md
    return SYSTEM_PROMPT


def register_agent(intent: str, agent: BaseAgent):
    _agent_registry[intent] = agent


class DispatcherAgent:
    """Routes user input to the appropriate business agent."""

    async def dispatch(
        self,
        session_id: str,
        user_input: str,
    ) -> AgentResponse:
        # 1. Persist user message
        await add_message(session_id, "user", user_input, message_type="咨询")

        # 2. Check session document state
        session = await get_session(session_id)
        has_document = session.get("has_document", False) if session else False

        # 3. Intent recognition
        if has_document:
            intent = await self._classify_document_intent(user_input)
        else:
            intent = await self._classify_intent(user_input)
        logger.info(f"[DISPATCH] session={session_id} intent={intent} has_doc={has_document}")

        # 4. Route to business agent
        agent = _agent_registry.get(intent)
        if agent is None:
            # Fallback: send to follow_up or legal_consultation
            agent = _agent_registry.get("追问/聊天") or _agent_registry.get("法律咨询")

        if agent is None:
            logger.error(f"[DISPATCH] no agent available for intent={intent}")
            return AgentResponse(
                content="系统初始化中，请稍后再试。",
                metadata={"intent": intent, "agent": "none"},
            )

        logger.info(f"[DISPATCH] routing to agent={agent.name}")

        # 5. Check and handle summarization before context assembly
        await check_and_summarize(session_id)

        # 6. Assemble context and execute agent
        enhanced_prompt = _build_system_prompt()
        context = await assemble_context(session_id, enhanced_prompt, user_input)
        logger.debug(f"[DISPATCH] context assembled: {len(context)} messages")
        response = await agent.execute(session_id, user_input, context)

        # 7. Persist AI response
        memory_content = response.memory_content if response.memory_content is not None else response.content
        await add_message(
            session_id, "ai", memory_content,
            message_type=response.metadata.get("message_type", "咨询"),
            references=response.references,
            metadata=response.metadata,
        )

        response.metadata["intent"] = intent
        response.metadata["agent"] = agent.name
        return response

    async def dispatch_stream(
        self,
        session_id: str,
        user_input: str,
    ):
        """Streaming version of dispatch. Yields text chunks, then AgentResponse."""
        from src.agents.base import AgentResponse

        # 1. Persist user message
        await add_message(session_id, "user", user_input, message_type="咨询")

        # 2. Check session document state
        session = await get_session(session_id)
        has_document = session.get("has_document", False) if session else False

        # 3. Intent recognition
        if has_document:
            intent = await self._classify_document_intent(user_input)
        else:
            intent = await self._classify_intent(user_input)
        logger.info(f"[DISPATCH] session={session_id} intent={intent} has_doc={has_document} stream=true")

        # 4. Route to business agent
        agent = _agent_registry.get(intent)
        if agent is None:
            agent = _agent_registry.get("追问/聊天") or _agent_registry.get("法律咨询")

        if agent is None:
            logger.error(f"[DISPATCH] no agent available for intent={intent}")
            yield AgentResponse(
                content="系统初始化中，请稍后再试。",
                metadata={"intent": intent, "agent": "none"},
            )
            return

        logger.info(f"[DISPATCH] routing to agent={agent.name}")

        # 5. Check summarization
        if await check_and_summarize(session_id):
            yield {"status": "summarizing"}

        # 6. Assemble context and stream from agent
        enhanced_prompt = _build_system_prompt()
        context = await assemble_context(session_id, enhanced_prompt, user_input)

        full_text = []
        final_response = None
        async for item in agent.stream_execute(session_id, user_input, context):
            if isinstance(item, AgentResponse):
                final_response = item
            elif isinstance(item, dict):
                yield item  # status/refs event
            else:
                full_text.append(item)
                yield item  # text chunk

        if final_response is None:
            final_response = AgentResponse(
                content="".join(full_text),
                metadata={"message_type": "咨询"},
            )

        # 7. Persist AI response
        memory_content = final_response.memory_content if final_response.memory_content is not None else final_response.content
        await add_message(
            session_id, "ai", memory_content,
            message_type=final_response.metadata.get("message_type", "咨询"),
            references=final_response.references,
            metadata=final_response.metadata,
        )

        final_response.metadata["intent"] = intent
        final_response.metadata["agent"] = agent.name
        yield final_response  # metadata signal

    async def _classify_intent(self, user_input: str) -> str:
        # Keyword pre-filter for clear-cut cases
        intent = _keyword_precheck(user_input)
        if intent:
            logger.info(f"[DISPATCH] keyword precheck → {intent}")
            return intent

        prompt = INTENT_PROMPT.format(user_input=user_input)
        raw = await chat_completion(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            max_tokens=20,
        )
        raw = raw.strip()
        valid = {"法律咨询", "案情分析", "文书撰写", "追问/聊天"}
        if raw not in valid:
            raw = "法律咨询"  # default
        return raw

    async def _classify_document_intent(self, user_input: str) -> str:
        """When document exists, classify between doc QA and contract review."""
        # Keyword pre-check for contract review
        review_keywords = ["审查", "审阅", "审核", "风险评估", "霸王条款", "无效条款", "漏洞"]
        if any(kw in user_input for kw in review_keywords):
            return "合同审查"

        prompt = DOCUMENT_INTENT_PROMPT.format(user_input=user_input)

        raw = await chat_completion(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            max_tokens=10,
        )
        raw = raw.strip()
        if "审查" in raw:
            return "合同审查"
        return "文档提问"
