from collections.abc import AsyncGenerator
from typing import Union

from src.agents.base import BaseAgent, AgentResponse
from src.llm.client import chat_completion, chat_completion_stream
from loguru import logger

FOLLOW_UP_SYSTEM = """你是一个法律AI助手，当前用户正在追问之前的问题。

## 重要原则
1. 基于对话历史上下文回答，不要重复之前已经完整解释过的内容
2. 如果用户在补充事实，确认理解并适当更新分析
3. 如果用户要求更详细的解释，在之前回答的基础上深入展开
4. 如果用户在澄清或纠正，承认并修正之前的理解
5. 保持回答简洁、精准，不要引入与当前话题无关的新内容
6. 不要触发新的法律检索或案例分析——这是追问模式，仅基于已有对话内容回答
"""


class FollowUpAgent(BaseAgent):
    def __init__(self):
        super().__init__("follow_up")

    async def execute(
        self,
        session_id: str,
        user_input: str,
        context: list[dict],
    ) -> AgentResponse:
        # Insert follow-up instruction as a system message before the conversation
        messages = [{"role": "system", "content": FOLLOW_UP_SYSTEM}]

        # Add conversation history (already in context from dispatcher)
        for msg in context:
            if msg["role"] != "system":
                messages.append(msg)

        # The last user message is already appended by assemble_context
        # We just need to ensure the follow-up instruction is present

        answer = await chat_completion(messages=messages, temperature=0.7)

        return AgentResponse(
            content=answer,
            metadata={"message_type": "追问", "mode": "context_only"},
        )

    async def stream_execute(
        self, session_id: str, user_input: str, context: list[dict],
    ) -> AsyncGenerator[Union[str, AgentResponse], None]:
        messages = [{"role": "system", "content": FOLLOW_UP_SYSTEM}]
        for msg in context:
            if msg["role"] != "system":
                messages.append(msg)

        full = []
        async for chunk in chat_completion_stream(messages=messages, temperature=0.7):
            full.append(chunk)
            yield chunk

        yield AgentResponse(
            content="".join(full),
            metadata={"message_type": "追问", "mode": "context_only"},
        )
