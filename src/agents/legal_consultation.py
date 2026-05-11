from collections.abc import AsyncGenerator
from typing import Union

from src.agents.base import BaseAgent, AgentResponse
from src.rag.pipeline import retrieve_legal
from src.llm.client import chat_completion, chat_completion_stream
from loguru import logger

CONSULTATION_PROMPT = """你是一个专业的法律顾问。请根据以下信息回答用户的法律咨询问题。

## 要求
1. 用通俗易懂的语言解答，让非法律专业人士也能理解
2. **必须标注**所引用的法条名称和条款号（格式：《民法典》第XXX条）
3. 如果涉及多个法律问题，请逐一解答
4. 给出具体的操作建议或维权步骤
5. 如果没有相关知识库法条可供参考，请基于你的法律知识回答，但明确告知用户"当前法律知识库有限，回答仅供参考"

## 参考法条
{references}

## 用户问题
{question}

## 你的回答
"""

NO_REFERENCES_TEXT = "（当前法律知识库未收录相关法条，以下回答基于通用法律知识）"


class LegalConsultationAgent(BaseAgent):
    def __init__(self):
        super().__init__("legal_consultation")

    async def execute(
        self,
        session_id: str,
        user_input: str,
        context: list[dict],
    ) -> AgentResponse:
        # 1. RAG retrieval
        try:
            law_results = await retrieve_legal(user_input, top_k=10)
        except Exception as e:
            logger.warning(f"RAG retrieval failed: {e}")
            law_results = []

        # 2. Build references
        if law_results:
            ref_text = "\n\n".join(
                f"【{i+1}】{r['chunk_text']}" for i, r in enumerate(law_results)
            )
            references = [
                {"type": "law", "text": r["chunk_text"][:200], "score": r["score"]}
                for r in law_results
            ]
        else:
            ref_text = NO_REFERENCES_TEXT
            references = []

        # 3. Generate response
        prompt = CONSULTATION_PROMPT.format(references=ref_text, question=user_input)
        messages = context + [{"role": "user", "content": prompt}]

        try:
            answer = await chat_completion(messages=messages, temperature=0.7)
        except Exception as e:
            logger.error(f"LLM call failed: {e}")
            return AgentResponse(
                content="抱歉，服务暂时不可用，请稍后重试。",
                metadata={"error": str(e), "message_type": "咨询"},
            )

        return AgentResponse(
            content=answer,
            references=references,
            metadata={"message_type": "咨询", "law_count": len(law_results)},
        )

    async def stream_execute(
        self, session_id: str, user_input: str, context: list[dict],
    ) -> AsyncGenerator[Union[str, AgentResponse], None]:
        try:
            law_results = await retrieve_legal(user_input, top_k=10)
        except Exception:
            law_results = []

        if law_results:
            ref_text = "\n\n".join(
                f"【{i+1}】{r['chunk_text']}" for i, r in enumerate(law_results)
            )
            references = [
                {"type": "law", "text": r["chunk_text"][:200], "score": r["score"]}
                for r in law_results
            ]
        else:
            ref_text = NO_REFERENCES_TEXT
            references = []

        prompt = CONSULTATION_PROMPT.format(references=ref_text, question=user_input)
        messages = context + [{"role": "user", "content": prompt}]

        full = []
        try:
            async for chunk in chat_completion_stream(messages=messages, temperature=0.7):
                full.append(chunk)
                yield chunk
        except Exception as e:
            logger.error(f"LLM stream failed: {e}")
            yield AgentResponse(
                content="抱歉，服务暂时不可用，请稍后重试。",
                metadata={"error": str(e), "message_type": "咨询"},
            )
            return

        yield AgentResponse(
            content="".join(full),
            references=references,
            metadata={"message_type": "咨询", "law_count": len(law_results)},
        )
