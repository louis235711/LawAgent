from collections.abc import AsyncGenerator
from typing import Union

from src.agents.base import BaseAgent, AgentResponse
from src.rag.pipeline import retrieve_legal
from src.tools.web_search import search_web
from src.llm.client import chat_completion, chat_completion_stream
from loguru import logger

CASE_ANALYSIS_PROMPT = """你是一个专业的法律案件分析师。请根据以下信息对用户描述的案情进行全面分析。

## 要求
1. **案情摘要**：用一段话概括案件基本情况
2. **要素提取**：列出当事人、时间线、争议焦点等关键要素
3. **法律依据**：引用相关法条（必须标注法条名称和条款号），不得编造
4. **类案参考**：参考提供的类案检索结果，给出类似案例的处理方式，未检索到时如实说明
5. **初步建议**：给出具体的法律建议或维权步骤，不确定时建议咨询持证律师

## 相关法条
{law_references}

## 类案检索结果
{case_references}

## 用户案情描述
{question}

## 分析报告
"""


class CaseAnalysisAgent(BaseAgent):
    def __init__(self):
        super().__init__("case_analysis")

    async def execute(
        self,
        session_id: str,
        user_input: str,
        context: list[dict],
    ) -> AgentResponse:
        logger.info(f"[AGENT] case_analysis execute session={session_id}")
        # 1. RAG retrieval for relevant laws
        try:
            law_results = await retrieve_legal(user_input, top_k=5)
        except Exception as e:
            logger.warning(f"Law retrieval failed: {e}")
            law_results = []

        law_text = "\n\n".join(
            f"【{i+1}】{r['chunk_text']}" for i, r in enumerate(law_results)
        ) if law_results else "暂无相关法条"

        # 2. Web search for similar cases
        search_query = f"类案 {user_input[:100]} 判决"
        try:
            web_results = await search_web(search_query, max_results=5)
        except Exception as e:
            logger.warning(f"Web search failed: {e}")
            web_results = []

        case_text = "\n\n".join(
            f"【案例{i+1}】{r['title']}\n{r['content']}\n来源：{r['url']}"
            for i, r in enumerate(web_results)
        ) if web_results else "未找到类案参考"

        # 3. Generate analysis report
        prompt = CASE_ANALYSIS_PROMPT.format(
            law_references=law_text,
            case_references=case_text,
            question=user_input,
        )
        messages = context + [{"role": "user", "content": prompt}]

        try:
            report = await chat_completion(messages=messages, temperature=0.7)
        except Exception as e:
            logger.error(f"LLM call failed: {e}")
            return AgentResponse(
                content="抱歉，服务暂时不可用，请稍后重试。",
                metadata={"error": str(e), "message_type": "案情"},
            )

        # Build references
        references = []
        for r in law_results:
            references.append({
                "type": "law", "text": r["chunk_text"][:200], "score": r["score"],
                "law_name": r.get("law_name", ""), "chapter": r.get("chapter", ""),
                "article_number": r.get("article_number", ""),
            })
        for r in web_results:
            references.append({"type": "case", "title": r["title"], "url": r["url"]})

        return AgentResponse(
            content=report,
            references=references,
            metadata={
                "message_type": "案情",
                "law_count": len(law_results),
                "case_count": len(web_results),
            },
        )

    async def stream_execute(
        self, session_id: str, user_input: str, context: list[dict],
    ) -> AsyncGenerator[Union[str, AgentResponse], None]:
        logger.info(f"[AGENT] case_analysis stream session={session_id}")
        try:
            law_results = await retrieve_legal(user_input, top_k=5)
        except Exception:
            law_results = []

        law_text = "\n\n".join(
            f"【{i+1}】{r['chunk_text']}" for i, r in enumerate(law_results)
        ) if law_results else "暂无相关法条"

        search_query = f"类案 {user_input[:100]} 判决"
        try:
            web_results = await search_web(search_query, max_results=5)
        except Exception:
            web_results = []

        case_text = "\n\n".join(
            f"【案例{i+1}】{r['title']}\n{r['content']}\n来源：{r['url']}"
            for i, r in enumerate(web_results)
        ) if web_results else "未找到类案参考"

        # Build references before LLM call
        references = []
        for r in law_results:
            references.append({
                "type": "law", "text": r["chunk_text"][:200], "score": r["score"],
                "law_name": r.get("law_name", ""), "chapter": r.get("chapter", ""),
                "article_number": r.get("article_number", ""),
            })
        for r in web_results:
            references.append({"type": "case", "title": r["title"], "url": r["url"]})

        yield {"refs": references}

        prompt = CASE_ANALYSIS_PROMPT.format(
            law_references=law_text, case_references=case_text, question=user_input,
        )
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
                metadata={"error": str(e), "message_type": "案情"},
            )
            return

        yield AgentResponse(
            content="".join(full),
            references=references,
            metadata={
                "message_type": "案情",
                "law_count": len(law_results),
                "case_count": len(web_results),
            },
        )
