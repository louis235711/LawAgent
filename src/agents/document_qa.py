import os
import json
import asyncio
from collections.abc import AsyncGenerator
from typing import Union

from src.agents.base import BaseAgent, AgentResponse
from src.database.redis import get_session
from src.rag.pipeline import retrieve_session_docs
from src.rag.query_rewriter import extract_history_from_context
from src.vector_db.milvus_client import get_collection, SESSION_DOCUMENTS_COLLECTION
from src.llm.client import chat_completion, chat_completion_stream
from src.config import settings
from loguru import logger

DOC_QA_PROMPT = """你是文档问答助手。以下参考资料来自用户上传的文档内容，可能对回答问题有帮助，请参考这些内容作答。

不得超出文档范围编造信息。如果文档内容不足以回答问题，请如实说明。

注意，你不能向用户直接说“片段几”，因为用户不可见，但你可以引用原文部分内容。

## 文档相关内容（来自用户上传的文档）
{chunks}

## 用户问题
{question}

## 回答
"""

DOC_QA_DIRECT_PROMPT = """你是文档问答助手。以下内容是用户上传的文档全文，请基于此回答用户的问题。

不得超出文档范围编造信息。如果文档内容不足以回答问题，请如实说明。
注意，你不能向用户直接说"片段几"，因为用户不可见，但你可以引用原文部分内容。

## 文档全文
{document_text}

## 用户问题
{question}

## 回答
"""

CONTRACT_BATCH_PROMPT = """你是专业的合同审查专家。请严格审查以下合同片段，识别所有可能的法律风险和问题条款。

## 合同片段（第 {batch_start} - {batch_end} 条 / 共 {total} 条）
{chunks}

## 审查要求
对于每个发现的问题，严格按以下格式输出：

### 问题 {batch_start}-{{n}}
- **原文引用**：引用有问题的原文（必须原文照抄，用引号括起来）
- **风险等级**：高风险 / 中风险 / 低风险
- **问题分析**：说明为什么该条款有问题（法律风险、无效条款、权利义务不对等等）
- **修改建议**：给出具体的修改后文本
- **法律依据**：引用相关法律法规的具体条款
"""

CONTRACT_FINAL_PROMPT = """你是专业的合同审查专家。以下是分段审查的结果汇总，请整理成完整的合同审查报告。

## 分段审查结果
{batch_reports}

## 审查报告要求
1. **总体风险评定**：高风险 / 中风险 / 低风险，并给出理由
2. **问题条款汇总**：按原文出现顺序，逐一列出所有问题。每个问题包括：
   - 原文引用（必须照抄原文）
   - 风险等级
   - 问题分析
   - 修改建议
   - 法律依据
3. **总体建议**：给出综合性的合同修改方向和注意事项

## 完整审查报告
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

        is_review = self._is_contract_review(user_input)
        logger.info(f"[AGENT] document_qa session={session_id} review={is_review} doc={doc_name}")

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

    # ── Document Q&A ────────────────────────────────────

    async def _stream_doc_question(
        self, session_id: str, user_input: str, context: list[dict],
    ) -> AsyncGenerator[Union[str, AgentResponse], None]:
        session = await get_session(session_id)

        # Check if we should use RAG or direct context
        use_rag = session.get("use_rag", False) if session else False

        if not use_rag:
            # Chunks < 10: load from JSON, assemble directly
            async for item in self._qa_from_json(session_id, session, user_input, context):
                yield item
        else:
            # Chunks >= 10: hybrid RAG
            try:
                history_text = extract_history_from_context(context)
                results = await retrieve_session_docs(user_input, session_id, top_k=5, history_text=history_text)
            except Exception as e:
                logger.warning(f"Document RAG failed: {e}")
                results = []

            if not results:
                yield AgentResponse(
                    content="未在文档中找到相关内容，请换个方式提问。",
                    metadata={"message_type": "文档", "chunks_found": 0},
                )
                return

            yield {"refs": [
                {"type": "doc_chunk_summary", "count": len(results)}
            ]}

            chunks_text = "\n\n---\n\n".join(
                r['chunk_text'] for r in results
            )
            prompt = DOC_QA_PROMPT.format(chunks=chunks_text, question=user_input)
            messages = context + [{"role": "user", "content": prompt}]

            full = []
            async for chunk in chat_completion_stream(messages=messages, temperature=0.7):
                full.append(chunk)
                yield chunk

            yield AgentResponse(
                content="".join(full),
                references=[{"type": "doc_chunk_summary", "count": len(results)}],
                metadata={"message_type": "文档", "chunks_found": len(results)},
            )

    async def _qa_from_json(
        self, session_id: str, session: dict, user_input: str, context: list[dict],
    ) -> AsyncGenerator[Union[str, AgentResponse], None]:
        """Direct context assembly for small documents (< 10 chunks)."""
        doc_name = session.get("document_name", "")
        if not doc_name:
            yield AgentResponse(
                content="未找到文档，请先上传文档。",
                metadata={"message_type": "文档", "error": "document not found"},
            )
            return

        base_name = os.path.splitext(doc_name)[0]
        json_path = os.path.join(settings.uploads_dir, session_id, f"{base_name}_chunks.json")

        try:
            with open(json_path, "r", encoding="utf-8") as f:
                chunks = json.load(f)
        except FileNotFoundError:
            # Try reading the parsed text file directly
            txt_path = os.path.join(settings.uploads_dir, session_id, f"{base_name}.txt")
            try:
                with open(txt_path, "r", encoding="utf-8") as f:
                    doc_text = f.read()
            except FileNotFoundError:
                yield AgentResponse(
                    content="文档解析结果未找到，请重新上传。",
                    metadata={"message_type": "文档", "error": "files not found"},
                )
                return
        else:
            doc_text = "\n\n---\n\n".join(chunks)

        yield {"refs": [
            {"type": "doc_chunk_summary", "count": len(chunks) if isinstance(chunks, list) else 1}
        ]}

        prompt = DOC_QA_DIRECT_PROMPT.format(document_text=doc_text, question=user_input)
        messages = context + [{"role": "user", "content": prompt}]

        full = []
        async for chunk in chat_completion_stream(messages=messages, temperature=0.7):
            full.append(chunk)
            yield chunk

        yield AgentResponse(
            content="".join(full),
            references=[{"type": "doc_chunk_summary", "count": len(chunks) if isinstance(chunks, list) else 1}],
            metadata={"message_type": "文档", "chunks_found": len(chunks) if isinstance(chunks, list) else 1},
        )

    async def _handle_doc_question(
        self, session_id: str, user_input: str, context: list[dict],
    ) -> AgentResponse:
        session = await get_session(session_id)
        use_rag = session.get("use_rag", False) if session else False

        if not use_rag:
            return await self._qa_from_json_sync(session_id, session, user_input, context)

        try:
            history_text = extract_history_from_context(context)
            results = await retrieve_session_docs(user_input, session_id, top_k=5, history_text=history_text)
        except Exception as e:
            logger.warning(f"Document RAG failed: {e}")
            results = []

        if not results:
            return AgentResponse(
                content="未在文档中找到相关内容，请换个方式提问。",
                metadata={"message_type": "文档", "chunks_found": 0},
            )

        chunks_text = "\n\n---\n\n".join(
            r['chunk_text'] for r in results
        )
        prompt = DOC_QA_PROMPT.format(chunks=chunks_text, question=user_input)
        messages = context + [{"role": "user", "content": prompt}]

        answer = await chat_completion(messages=messages, temperature=0.7)

        return AgentResponse(
            content=answer,
            references=[{"type": "doc_chunk_summary", "count": len(results)}],
            metadata={"message_type": "文档", "chunks_found": len(results)},
        )

    async def _qa_from_json_sync(
        self, session_id: str, session: dict, user_input: str, context: list[dict],
    ) -> AgentResponse:
        """Non-streaming direct context assembly."""
        doc_name = session.get("document_name", "")
        base_name = os.path.splitext(doc_name)[0]
        json_path = os.path.join(settings.uploads_dir, session_id, f"{base_name}_chunks.json")

        try:
            with open(json_path, "r", encoding="utf-8") as f:
                chunks = json.load(f)
        except FileNotFoundError:
            txt_path = os.path.join(settings.uploads_dir, session_id, f"{base_name}.txt")
            try:
                with open(txt_path, "r", encoding="utf-8") as f:
                    doc_text = f.read()
            except FileNotFoundError:
                return AgentResponse(
                    content="文档解析结果未找到，请重新上传。",
                    metadata={"message_type": "文档", "error": "files not found"},
                )
        else:
            doc_text = "\n\n---\n\n".join(chunks)

        prompt = DOC_QA_DIRECT_PROMPT.format(document_text=doc_text, question=user_input)
        messages = context + [{"role": "user", "content": prompt}]
        answer = await chat_completion(messages=messages, temperature=0.7)

        return AgentResponse(
            content=answer,
            references=[{"type": "doc_chunk_summary", "count": len(chunks) if isinstance(chunks, list) else 1}],
            metadata={"message_type": "文档", "chunks_found": len(chunks) if isinstance(chunks, list) else 1},
        )

    # ── Contract Review ──────────────────────────────────

    async def _stream_contract_review(
        self, session_id: str, user_input: str, context: list[dict], doc_name: str,
    ) -> AsyncGenerator[Union[str, AgentResponse], None]:
        # 1. Load all chunks from Milvus in order
        try:
            chunks = self._load_chunks_ordered(session_id)
        except Exception as e:
            logger.error(f"Failed to load chunks for review: {e}")
            yield AgentResponse(
                content="文档加载失败，请重新上传。",
                metadata={"message_type": "文档", "error": "chunks load failed"},
            )
            return

        if not chunks:
            yield AgentResponse(
                content="文档为空，请确认已正确上传文档。",
                metadata={"message_type": "文档", "error": "no chunks"},
            )
            return

        total = len(chunks)
        batch_size = 20
        batches = []
        for i in range(0, total, batch_size):
            batch_chunks = chunks[i:i + batch_size]
            batch_text = "\n\n---\n\n".join(
                f"【{j+1}】{chunk}" for j, chunk in enumerate(batch_chunks, start=i)
            )
            batches.append({
                "start": i + 1,
                "end": min(i + batch_size, total),
                "text": batch_text,
            })

        logger.info(f"[AGENT] contract review: {total} chunks → {len(batches)} batches")

        yield {"refs": [{"type": "doc_chunk_summary", "count": total}]}

        # 2. Parallel batch review
        async def review_batch(batch: dict) -> str:
            prompt = CONTRACT_BATCH_PROMPT.format(
                batch_start=batch["start"],
                batch_end=batch["end"],
                total=total,
                chunks=batch["text"],
            )
            messages = [{"role": "user", "content": prompt}]
            try:
                return await chat_completion(messages=messages, temperature=0.3, max_tokens=2048)
            except Exception as e:
                logger.error(f"Batch review {batch['start']}-{batch['end']} failed: {e}")
                return f"## 批次 {batch['start']}-{batch['end']}\n\n审查失败: {e}"

        tasks = [review_batch(b) for b in batches]
        batch_reports = await asyncio.gather(*tasks)
        logger.info(f"[AGENT] contract review: {len(batch_reports)} batch reports completed")

        # 3. Assemble final review
        combined_reports = "\n\n---\n\n".join(
            f"## 分段 {b['start']}-{b['end']}\n{r}"
            for b, r in zip(batches, batch_reports)
        )
        final_prompt = CONTRACT_FINAL_PROMPT.format(batch_reports=combined_reports)
        messages = context + [{"role": "user", "content": final_prompt}]

        full = []
        async for chunk in chat_completion_stream(messages=messages, temperature=0.5, max_tokens=4096):
            full.append(chunk)
            yield chunk

        yield AgentResponse(
            content="".join(full),
            references=[{"type": "doc_chunk_summary", "count": total}],
            metadata={"message_type": "文档", "review": True, "doc_name": doc_name, "total_chunks": total},
        )

    async def _handle_contract_review(
        self, session_id: str, user_input: str, context: list[dict], doc_name: str,
    ) -> AgentResponse:
        try:
            chunks = self._load_chunks_ordered(session_id)
        except Exception as e:
            logger.error(f"Failed to load chunks for review: {e}")
            return AgentResponse(
                content="文档加载失败，请重新上传。",
                metadata={"message_type": "文档", "error": "chunks load failed"},
            )

        if not chunks:
            return AgentResponse(
                content="文档为空，请确认已正确上传文档。",
                metadata={"message_type": "文档", "error": "no chunks"},
            )

        total = len(chunks)
        batch_size = 20
        batches = []
        for i in range(0, total, batch_size):
            batch_chunks = chunks[i:i + batch_size]
            batch_text = "\n\n---\n\n".join(
                f"【{j+1}】{chunk}" for j, chunk in enumerate(batch_chunks, start=i)
            )
            batches.append({"start": i + 1, "end": min(i + batch_size, total), "text": batch_text})

        async def review_batch(batch: dict) -> str:
            prompt = CONTRACT_BATCH_PROMPT.format(
                batch_start=batch["start"], batch_end=batch["end"],
                total=total, chunks=batch["text"],
            )
            messages = [{"role": "user", "content": prompt}]
            try:
                return await chat_completion(messages=messages, temperature=0.3, max_tokens=2048)
            except Exception as e:
                return f"## 批次 {batch['start']}-{batch['end']}\n\n审查失败: {e}"

        batch_reports = await asyncio.gather(*[review_batch(b) for b in batches])

        combined_reports = "\n\n---\n\n".join(
            f"## 分段 {b['start']}-{b['end']}\n{r}"
            for b, r in zip(batches, batch_reports)
        )
        final_prompt = CONTRACT_FINAL_PROMPT.format(batch_reports=combined_reports)
        messages = context + [{"role": "user", "content": final_prompt}]
        report = await chat_completion(messages=messages, temperature=0.5, max_tokens=4096)

        return AgentResponse(
            content=report,
            references=[{"type": "doc_chunk_summary", "count": total}],
            metadata={"message_type": "文档", "review": True, "doc_name": doc_name, "total_chunks": total},
        )

    def _load_chunks_ordered(self, session_id: str) -> list[str]:
        """Load document chunks from Milvus ordered by chunk_index.
        Falls back to JSON file if Milvus has no chunks (small docs)."""
        coll = get_collection(SESSION_DOCUMENTS_COLLECTION)
        offset = 0
        batch = 500
        rows = []
        while True:
            results = coll.query(
                expr=f'session_id == "{session_id}"',
                output_fields=["chunk_text", "chunk_index"],
                limit=batch,
                offset=offset,
            )
            if not results:
                break
            rows.extend(results)
            if len(results) < batch:
                break
            offset += batch

        if rows:
            rows.sort(key=lambda r: r.get("chunk_index", 0))
            return [r.get("chunk_text") or "" for r in rows]

        # Fallback: load from JSON (small docs not in Milvus)
        import glob
        pattern = os.path.join(settings.uploads_dir, session_id, "*_chunks.json")
        json_files = glob.glob(pattern)
        if json_files:
            with open(json_files[0], "r", encoding="utf-8") as f:
                return json.load(f)
        return []
