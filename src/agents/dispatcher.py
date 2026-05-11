from src.agents.base import BaseAgent, AgentResponse
from src.database.redis import get_session
from src.config import settings
from src.llm.client import chat_completion
from src.memory.context_manager import add_message, check_and_summarize, assemble_context
from loguru import logger

INTENT_PROMPT = """你是一个法律AI系统的意图识别模块。分析用户输入，输出以下类别之一：

- 法律咨询：用户询问法律问题、法条解读、维权建议等
- 案情分析：用户描述具体案情，要求分析、梳理或给出处理建议
- 文书撰写：用户要求生成法律文书（合同、起诉状、律师函等）
- 合同审查：用户要求审查合同、分析条款风险（注意：仅在用户明确表达审查意图时输出）
- 文档提问：用户针对已上传文档的具体提问
- 追问/聊天：用户对上一轮回复的追问、澄清、补充信息，或寒暄聊天

严格只输出一个类别名称。

用户输入：{user_input}

类别："""

# Maps intent → (agent, message_type)
_agent_registry: dict[str, BaseAgent] = {}

SYSTEM_PROMPT = "你是一个专业的法律AI助手，为用户提供法律咨询、案情分析、文书撰写、合同审查等服务。"


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
        logger.info(f"Session {session_id}: intent={intent}, has_document={has_document}")

        # 4. Route to business agent
        agent = _agent_registry.get(intent)
        if agent is None:
            # Fallback: send to follow_up or legal_consultation
            agent = _agent_registry.get("追问/聊天") or _agent_registry.get("法律咨询")

        if agent is None:
            return AgentResponse(
                content="系统初始化中，请稍后再试。",
                metadata={"intent": intent, "agent": "none"},
            )

        # 5. Check and handle summarization before context assembly
        await check_and_summarize(session_id)

        # 6. Assemble context and execute agent
        context = await assemble_context(session_id, SYSTEM_PROMPT, user_input)
        response = await agent.execute(session_id, user_input, context)

        # 7. Persist AI response
        await add_message(
            session_id, "ai", response.content,
            message_type=response.metadata.get("message_type", "咨询"),
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
        logger.info(f"Session {session_id} (stream): intent={intent}, has_document={has_document}")

        # 4. Route to business agent
        agent = _agent_registry.get(intent)
        if agent is None:
            agent = _agent_registry.get("追问/聊天") or _agent_registry.get("法律咨询")

        if agent is None:
            yield AgentResponse(
                content="系统初始化中，请稍后再试。",
                metadata={"intent": intent, "agent": "none"},
            )
            return

        # 5. Check summarization
        await check_and_summarize(session_id)

        # 6. Assemble context and stream from agent
        context = await assemble_context(session_id, SYSTEM_PROMPT, user_input)

        full_text = []
        final_response = None
        async for item in agent.stream_execute(session_id, user_input, context):
            if isinstance(item, AgentResponse):
                final_response = item
            else:
                full_text.append(item)
                yield item  # text chunk

        if final_response is None:
            final_response = AgentResponse(
                content="".join(full_text),
                metadata={"message_type": "咨询"},
            )

        # 7. Persist AI response
        await add_message(
            session_id, "ai", final_response.content,
            message_type=final_response.metadata.get("message_type", "咨询"),
        )

        final_response.metadata["intent"] = intent
        final_response.metadata["agent"] = agent.name
        yield final_response  # metadata signal

    async def _classify_intent(self, user_input: str) -> str:
        prompt = INTENT_PROMPT.format(user_input=user_input)
        raw = await chat_completion(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            max_tokens=20,
        )
        raw = raw.strip()
        valid = {"法律咨询", "案情分析", "文书撰写", "合同审查", "文档提问", "追问/聊天"}
        if raw not in valid:
            raw = "法律咨询"  # default
        return raw

    async def _classify_document_intent(self, user_input: str) -> str:
        """When document exists, classify between doc QA and contract review."""
        prompt = f"""用户已上传文档。判断用户意图：

- 合同审查：用户要求审查合同、评估风险、分析条款
- 文档提问：用户针对文档内容的普通提问

用户输入：{user_input}

严格只输出：合同审查 或 文档提问"""

        raw = await chat_completion(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            max_tokens=10,
        )
        raw = raw.strip()
        if "审查" in raw:
            return "合同审查"
        return "文档提问"
