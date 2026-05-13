from src.llm.client import chat_completion
from loguru import logger

REWRITE_PROMPT = """将以下用户问题改写为适合法律检索的查询语句。要求：
1. 将口语化表达转换为法律专业术语（如"不让去同行上班"→"竞业限制"）
2. 如果问题中包含指代词（这个、那个、该、此、上述等），请根据对话历史还原为具体内容
3. 只输出改写后的查询，不要解释

## 对话历史
{history}

## 用户问题
{query}

## 改写后的查询"""


async def rewrite_query(query: str, history_text: str = "") -> str:
    """Rewrite user query for better legal RAG retrieval.

    - Resolves pronouns/demonstratives using conversation history
    - Converts colloquial language to legal terminology
    - Returns original query if rewriting fails or is unnecessary
    """
    if not history_text.strip():
        # No history to resolve references — still try to legalize the query
        history_text = "（无对话历史）"

    prompt = REWRITE_PROMPT.format(history=history_text, query=query)

    try:
        result = await chat_completion(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            max_tokens=128,
        )
        rewritten = result.strip()
        if rewritten and rewritten != query:
            logger.info(f"[REWRITE] \"{query[:60]}\" → \"{rewritten[:80]}\"")
            return rewritten
        return query
    except Exception as e:
        logger.warning(f"Query rewrite failed, using original: {e}")
        return query


def extract_history_from_context(context: list[dict], max_turns: int = 3) -> str:
    """Extract recent conversation turns from assembled context for query rewriting.

    The context contains system messages with memory blocks like:
      【近期对话回顾】
      用户: ...\nAI: ...

    Returns a compact history string suitable for the rewrite prompt.
    """
    # 1. Try to find the "近期对话回顾" memory block
    for msg in context:
        if msg.get("role") != "system":
            continue
        content = msg.get("content", "")
        if "近期对话回顾" in content:
            # Extract the conversation part after the header
            lines = content.split("\n")
            conversation_lines = []
            for line in lines:
                if line.startswith("用户:") or line.startswith("AI:"):
                    conversation_lines.append(line)
                elif line.startswith("【") or line.startswith("近期对话回顾"):
                    continue
            if conversation_lines:
                return "\n".join(conversation_lines[-max_turns * 2:])

    # 2. Fallback: extract the last user message (current question)
    for msg in reversed(context):
        if msg.get("role") == "user":
            content = msg.get("content", "")
            # Strip the "【当前最新问题】" wrapper if present
            for prefix in ["【当前最新问题】", "【当前最新问题】\n"]:
                if content.startswith(prefix):
                    content = content[len(prefix):]
            return "用户: " + content

    return ""
