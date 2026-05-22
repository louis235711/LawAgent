"""MCP Client — connect to external MCP servers via stdio transport."""

import asyncio
import shlex
from mcp import ClientSession
from mcp.client.stdio import stdio_client, StdioServerParameters
from src.config import settings
from loguru import logger


class MCPClientManager:
    """Manages connections to multiple external MCP servers (stdio transport)."""

    def __init__(self):
        self._sessions: dict[str, ClientSession] = {}
        self._contexts: dict[str, tuple] = {}  # context managers for cleanup
        self._tools: dict[str, dict] = {}  # tool_name -> {"server": str, "tool": Tool}

    async def connect_all(self):
        """Connect to all configured MCP servers and discover their tools."""
        servers = {
            "time": settings.mcp_time_server_cmd,
            "calculator": settings.mcp_calculator_server_cmd,
            "fetch": settings.mcp_fetch_server_cmd,
        }

        for name, cmd in servers.items():
            if not cmd:
                continue
            try:
                await self._connect_server(name, cmd)
            except Exception as e:
                logger.warning(f"[MCP Client] Failed to start {name}: {e}")

    async def _connect_server(self, name: str, cmd: str):
        """Start a MCP server subprocess and discover its tools."""
        parts = shlex.split(cmd)
        if not parts:
            return

        command = parts[0]
        args = parts[1:]

        params = StdioServerParameters(
            command=command,
            args=args,
            encoding_error_handler="replace",
        )
        ctx = stdio_client(params)
        read, write = await ctx.__aenter__()
        session = ClientSession(read, write)
        await session.__aenter__()
        await asyncio.wait_for(session.initialize(), timeout=30)

        self._sessions[name] = session
        self._contexts[name] = ctx

        # Discover tools
        tools_response = await session.list_tools()
        for tool in tools_response.tools:
            tool_name = f"mcp_{name}_{tool.name}"
            self._tools[tool_name] = {"server": name, "tool": tool}
            logger.info(f"[MCP Client] Discovered tool: {tool_name}")

        logger.info(f"[MCP Client] Started {name} ({cmd}), "
                     f"{len(tools_response.tools)} tools")

    async def disconnect_all(self):
        """Disconnect from all MCP servers."""
        for name, session in self._sessions.items():
            try:
                await session.__aexit__(None, None, None)
            except Exception as e:
                logger.warning(f"[MCP Client] Error closing session {name}: {e}")

        for name, ctx in self._contexts.items():
            try:
                await ctx.__aexit__(None, None, None)
            except Exception as e:
                logger.warning(f"[MCP Client] Error closing context {name}: {e}")

        self._sessions.clear()
        self._contexts.clear()
        self._tools.clear()
        logger.info("[MCP Client] All connections closed")

    def list_external_tools(self) -> list[dict]:
        """Return discovered tools in Anthropic format."""
        result = []
        for tool_name, info in self._tools.items():
            tool = info["tool"]
            result.append({
                "name": tool_name,
                "description": f"[MCP:{info['server']}] {tool.description or tool.name}",
                "input_schema": tool.inputSchema,
            })
        return result

    async def call_tool(self, name: str, arguments: dict) -> str:
        """Call an external MCP tool by name. Returns result text."""
        if name not in self._tools:
            return f"未知的外部工具: {name}"

        info = self._tools[name]
        server_name = info["server"]
        tool = info["tool"]
        session = self._sessions.get(server_name)

        if session is None:
            return f"外部 MCP Server '{server_name}' 未连接"

        try:
            result = await session.call_tool(tool.name, arguments)
            # Extract text from content blocks
            parts = []
            for block in result.content:
                if hasattr(block, "text"):
                    parts.append(block.text)
                else:
                    parts.append(str(block))
            return "\n".join(parts) or "(empty result)"
        except Exception as e:
            logger.error(f"[MCP Client] Tool call failed ({name}): {e}")
            return f"外部工具调用失败: {e}"


# Singleton
_mcp_client: MCPClientManager | None = None


def get_mcp_client() -> MCPClientManager:
    global _mcp_client
    if _mcp_client is None:
        _mcp_client = MCPClientManager()
    return _mcp_client
