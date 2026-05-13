from __future__ import annotations

from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from abc import ABC, abstractmethod
from typing import Union


@dataclass
class AgentResponse:
    content: str
    references: list[dict] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)

    # Suggested next actions (empty = dispatcher decides)
    next_actions: list[str] = field(default_factory=list)

    # Content to store in conversation memory (if different from display content)
    memory_content: str | None = None


class BaseAgent(ABC):
    """Base class for all business agents.

    Agents communicate via direct function calls — no message queue needed.
    The dispatcher calls agent.execute() and receives AgentResponse.
    """

    def __init__(self, name: str):
        self.name = name

    @abstractmethod
    async def execute(
        self,
        session_id: str,
        user_input: str,
        context: list[dict],
    ) -> AgentResponse:
        ...

    async def stream_execute(
        self,
        session_id: str,
        user_input: str,
        context: list[dict],
    ) -> AsyncGenerator[Union[str, AgentResponse], None]:
        """Stream LLM response chunks. Default: yield full content at once.

        Override in subclasses for true token-by-token streaming.
        Each yield is either a str (text chunk) or AgentResponse (final metadata).
        """
        response = await self.execute(session_id, user_input, context)
        yield response.content
        yield response
