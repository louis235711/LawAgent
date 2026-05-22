"""MCP Server — expose LawAgent tools to external MCP clients."""

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from loguru import logger

from src.tools.registry import (
    execute_search_laws,
    execute_search_cases,
    execute_search_documents,
    execute_read_document_full,
    execute_generate_document,
    execute_get_current_time,
    execute_calculate,
)

mcp_server = FastMCP(
    name="lawagent",
    instructions=(
        "LawAgent 法律 AI 工具集。提供中国法律知识库检索、案例搜索、"
        "文档问答、文书生成、数学计算等能力。"
    ),
)


# ── Tools ──────────────────────────────────────────────────

@mcp_server.tool(
    annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False),
)
async def search_laws(query: str, top_k: int = 5) -> str:
    """搜索中国法律知识库，获取相关法条原文、章节和条款号。

    当用户咨询具体法律问题时，应先调用此工具检索相关法条再回答。
    使用法律专业术语进行检索，如"竞业限制 违约金"而非口语化表达。

    Args:
        query: 法律检索查询，使用法律专业术语
        top_k: 返回结果数量，默认5条
    """
    result = await execute_search_laws(query=query, top_k=top_k)
    logger.info(f"[MCP] search_laws query={query!r} → {len(result)} chars")
    return result


@mcp_server.tool(
    annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True),
)
async def search_cases(query: str, max_results: int = 5) -> str:
    """联网搜索类似案例的判决结果和处理方式。

    当需要类案参考、了解实务倾向时调用。

    Args:
        query: 案例搜索查询，如"民间借贷 利息 判决 2024"
        max_results: 最大结果数，默认5条
    """
    result = await execute_search_cases(query=query, max_results=max_results)
    logger.info(f"[MCP] search_cases query={query!r} → {len(result)} chars")
    return result


@mcp_server.tool(
    annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False),
)
async def search_documents(
    query: str,
    session_id: str = "",
    user_id: int = 0,
    top_k: int = 5,
) -> str:
    """在用户已上传的文档中搜索相关内容（向量+关键词混合检索）。

    当用户针对已上传文档的具体内容提问时调用。

    Args:
        query: 文档搜索查询，提取问题中的关键概念
        session_id: 会话ID（必填，用于定位文档）
        user_id: 用户ID（必填，用于定位文档目录）
        top_k: 返回结果数量，默认5条
    """
    if not session_id:
        return "错误：search_documents 需要提供 session_id 参数。请先通过 LawAgent 创建会话并上传文档。"
    result = await execute_search_documents(
        query=query, session_id=session_id, top_k=top_k,
    )
    logger.info(f"[MCP] search_documents query={query!r} session={session_id} → {len(result)} chars")
    return result


@mcp_server.tool(
    annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False),
)
async def read_document_full(
    session_id: str = "",
    user_id: int = 0,
) -> str:
    """读取用户已上传文档的全文。

    适用于合同审查、需要完整阅读整份文档而非片段检索时。

    Args:
        session_id: 会话ID（必填）
        user_id: 用户ID（必填）
    """
    if not session_id:
        return "错误：read_document_full 需要提供 session_id 参数。请先通过 LawAgent 创建会话并上传文档。"
    result = await execute_read_document_full(session_id, user_id=user_id)
    logger.info(f"[MCP] read_document_full session={session_id} → {len(result)} chars")
    return result


@mcp_server.tool(
    annotations=ToolAnnotations(readOnlyHint=False, openWorldHint=False),
)
async def generate_document(
    requirements: str,
    format: str = "md",
    session_id: str = "",
    user_id: int = 0,
) -> str:
    """根据用户需求生成完整的法律文书（合同、起诉状、律师函、申请书等）。

    当用户明确要求起草/撰写/生成法律文书时调用。

    Args:
        requirements: 文书需求描述，包含文书类型、当事人信息、关键条款等
        format: 输出格式，可选 md/docx/pdf/txt，默认md
        session_id: 会话ID（必填，用于保存生成的文件）
        user_id: 用户ID（必填，用于文件路径隔离）
    """
    if not session_id:
        return "错误：generate_document 需要提供 session_id 参数。请先通过 LawAgent 创建会话。"
    result = await execute_generate_document(
        requirements=requirements, format=format,
        session_id=session_id, user_id=user_id,
    )
    logger.info(f"[MCP] generate_document format={format} session={session_id} → {len(result)} chars")
    return result


@mcp_server.tool(
    annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False),
)
async def get_current_time() -> str:
    """获取当前日期和时间。

    适用于用户询问今天日期、计算时间差、诉讼时效计算等需要准确日期的场景。
    """
    result = await execute_get_current_time()
    logger.info(f"[MCP] get_current_time → {result!r}")
    return result


@mcp_server.tool(
    annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False),
)
async def calculate(expression: str) -> str:
    """执行数学计算。

    适用于计算利息、违约金、赔偿金额、诉讼费用等涉及数学运算的场景。
    支持四则运算、百分比、幂运算等。

    Args:
        expression: 数学表达式，如 '(100000 * 0.05 * 365) / 365'
    """
    result = await execute_calculate(expression=expression)
    logger.info(f"[MCP] calculate expr={expression!r} → {result!r}")
    return result
