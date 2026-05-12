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
7. 不得在追问中编造新的法条、案例或司法解释，如需引用未讨论过的法律依据，必须明确告知用户此信息需要核实
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
        # Keep global constraints + summaries from context, append follow-up instruction
        messages = list(context)
        messages.append({"role": "system", "content": FOLLOW_UP_SYSTEM})

        answer = await chat_completion(messages=messages, temperature=0.7)

        return AgentResponse(
            content=answer,
            metadata={"message_type": "追问", "mode": "context_only"},
        )

    async def stream_execute(
        self, session_id: str, user_input: str, context: list[dict],
    ) -> AsyncGenerator[Union[str, AgentResponse], None]:
        # Keep global constraints + summaries from context, append follow-up instruction
        messages = list(context)
        messages.append({"role": "system", "content": FOLLOW_UP_SYSTEM})

        full = []
        async for chunk in chat_completion_stream(messages=messages, temperature=0.7):
            full.append(chunk)
            yield chunk

        yield AgentResponse(
            content="".join(full),
            metadata={"message_type": "追问", "mode": "context_only"},
        )
