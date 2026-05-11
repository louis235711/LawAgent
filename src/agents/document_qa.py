import os
from collections.abc import AsyncGenerator
from typing import Union

from src.agents.base import BaseAgent, AgentResponse
from src.database.redis import get_session
from src.rag.pipeline import retrieve_session_docs
from src.llm.client import chat_completion, chat_completion_stream
from src.config import settings
from loguru import logger

DOC_QA_PROMPT = """你是文档问答助手。根据文档相关内容回答用户的问题。仅基于提供的文档内容作答。

## 文档相关内容
{chunks}

## 用户问题
{question}

## 回答
"""

CONTRACT_REVIEW_PROMPT = """你是专业的合同审查专家。请对以下合同进行全文审查，识别风险并给出修改建议。

## 审查要求
1. **风险等级**：给出总体风险评定（高风险 / 中风险 / 低风险）
2. **问题条款**：逐条列出有问题的条款原文
3. **问题分析**：说明为什么该条款有问题（无效条款、霸王条款、法律风险等）
4. **修改建议**：给出具体修改方案
5. **法律依据**：引用相关法律法规

## 合同全文
{contract}

## 审查报告
"""


class DocumentQAAgent(BaseAgent):
    def __init__(self):
        super().__init__("document_qa")

    async def execute(
        self,
        session_id: str,
        user_input: str,
        context: list[dict],
    ) -> AgentResponse:
        session = await get_session(session_id)
        doc_name = session.get("document_name", "") if session else ""

        # Determine scenario: contract review vs document question
        is_review = self._is_contract_review(user_input)
        logger.info(f"Document QA: is_review={is_review}, doc={doc_name}")

        if is_review:
            return await self._handle_contract_review(session_id, user_input, context, doc_name)
        else:
            return await self._handle_doc_question(session_id, user_input, context)

    def _is_contract_review(self, user_input: str) -> bool:
        keywords = ["审查", "风险", "评估", "审核", "审阅", "霸王条款", "无效条款", "漏洞"]
        return any(kw in user_input for kw in keywords)

    async def stream_execute(
        self, session_id: str, user_input: str, context: list[dict],
    ) -> AsyncGenerator[Union[str, AgentResponse], None]:
        session = await get_session(session_id)
        doc_name = session.get("document_name", "") if session else ""
        is_review = self._is_contract_review(user_input)

        if is_review:
            async for item in self._stream_contract_review(session_id, user_input, context, doc_name):
                yield item
        else:
            async for item in self._stream_doc_question(session_id, user_input, context):
                yield item

    async def _stream_doc_question(
        self, session_id: str, user_input: str, context: list[dict],
    ) -> AsyncGenerator[Union[str, AgentResponse], None]:
        try:
            results = await retrieve_session_docs(user_input, session_id, top_k=5)
        except Exception:
            results = []

        if not results:
            yield AgentResponse(
                content="未在文档中找到相关内容，请确认文档已正确上传，或换个方式提问。",
                metadata={"message_type": "文档", "chunks_found": 0},
            )
            return

        chunks_text = "\n\n---\n\n".join(
            f"【片段 {i+1}】{r['chunk_text']}" for i, r in enumerate(results)
        )
        prompt = DOC_QA_PROMPT.format(chunks=chunks_text, question=user_input)
        messages = context + [{"role": "user", "content": prompt}]

        full = []
        async for chunk in chat_completion_stream(messages=messages, temperature=0.7):
            full.append(chunk)
            yield chunk

        yield AgentResponse(
            content="".join(full),
            references=[
                {"type": "doc_chunk", "text": r["chunk_text"][:200], "score": r["score"]}
                for r in results
            ],
            metadata={"message_type": "文档", "chunks_found": len(results)},
        )

    async def _stream_contract_review(
        self, session_id: str, user_input: str, context: list[dict], doc_name: str,
    ) -> AsyncGenerator[Union[str, AgentResponse], None]:
        md_name = os.path.splitext(doc_name)[0] + ".md"
        md_path = os.path.join(settings.uploads_dir, session_id, md_name)

        try:
            with open(md_path, encoding="utf-8") as f:
                contract_text = f.read()
        except FileNotFoundError:
            yield AgentResponse(
                content="合同文档解析结果未找到，请重新上传。",
                metadata={"message_type": "文档", "error": "markdown not found"},
            )
            return

        prompt = CONTRACT_REVIEW_PROMPT.format(contract=contract_text)
        messages = context + [{"role": "user", "content": prompt}]

        full = []
        async for chunk in chat_completion_stream(messages=messages, temperature=0.5, max_tokens=4096):
            full.append(chunk)
            yield chunk

        yield AgentResponse(
            content="".join(full),
            metadata={"message_type": "文档", "review": True, "doc_name": doc_name},
        )

    async def _handle_doc_question(
        self, session_id: str, user_input: str, context: list[dict],
    ) -> AgentResponse:
        # Vector search in session documents
        try:
            results = await retrieve_session_docs(user_input, session_id, top_k=5)
        except Exception as e:
            logger.warning(f"Document retrieval failed: {e}")
            results = []

        if not results:
            return AgentResponse(
                content="未在文档中找到相关内容，请确认文档已正确上传，或换个方式提问。",
                metadata={"message_type": "文档", "chunks_found": 0},
            )

        chunks_text = "\n\n---\n\n".join(
            f"【片段 {i+1}】{r['chunk_text']}" for i, r in enumerate(results)
        )
        prompt = DOC_QA_PROMPT.format(chunks=chunks_text, question=user_input)
        messages = context + [{"role": "user", "content": prompt}]

        answer = await chat_completion(messages=messages, temperature=0.7)

        return AgentResponse(
            content=answer,
            references=[
                {"type": "doc_chunk", "text": r["chunk_text"][:200], "score": r["score"]}
                for r in results
            ],
            metadata={"message_type": "文档", "chunks_found": len(results)},
        )

    async def _handle_contract_review(
        self,
        session_id: str,
        user_input: str,
        context: list[dict],
        doc_name: str,
    ) -> AgentResponse:
        # Read full Markdown from disk
        md_name = os.path.splitext(doc_name)[0] + ".md"
        md_path = os.path.join(settings.uploads_dir, session_id, md_name)

        try:
            with open(md_path, encoding="utf-8") as f:
                contract_text = f.read()
        except FileNotFoundError:
            return AgentResponse(
                content="合同文档解析结果未找到，请重新上传。",
                metadata={"message_type": "文档", "error": "markdown not found"},
            )

        prompt = CONTRACT_REVIEW_PROMPT.format(contract=contract_text)
        messages = context + [{"role": "user", "content": prompt}]

        report = await chat_completion(messages=messages, temperature=0.5, max_tokens=4096)

        return AgentResponse(
            content=report,
            metadata={"message_type": "文档", "review": True, "doc_name": doc_name},
        )
