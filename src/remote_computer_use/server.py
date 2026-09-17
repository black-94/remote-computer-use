from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from mcp.server.fastmcp import FastMCP

from .api import DiscoveryAPI
from .service import DiscoveryService


def build_server(
    service: DiscoveryService,
    *,
    host: str = "127.0.0.1",
    port: int = 8000,
) -> FastMCP[Any]:
    api = DiscoveryAPI(service)

    @asynccontextmanager
    async def lifespan(_: FastMCP[Any]) -> AsyncIterator[dict[str, object]]:
        await service.start()
        try:
            yield {}
        finally:
            await service.stop()

    mcp = FastMCP(
        "remote-computer-use",
        instructions=(
            "Read-only discovery and cached health status for configured SSH remotes. "
            "This server cannot execute caller-supplied commands or proxy requests."
        ),
        host=host,
        port=port,
        lifespan=lifespan,
        json_response=True,
    )

    @mcp.tool(structured_output=True)
    def list_available_remotes() -> dict[str, object]:
        """List names of connectable remotes with at least one available capability."""
        return api.list_available_remotes()

    @mcp.tool(structured_output=True)
    def list_all_remotes() -> dict[str, object]:
        """List every configured remote name and cached status."""
        return api.list_all_remotes()

    @mcp.tool(structured_output=True)
    def list_available_capabilities(remote: str) -> dict[str, object]:
        """Get connection parameters and descriptions of available capabilities."""
        return api.list_available_capabilities(remote)

    @mcp.tool(structured_output=True)
    def list_all_capabilities(remote: str) -> dict[str, object]:
        """List every capability and its cached status for one remote."""
        return api.list_all_capabilities(remote)

    @mcp.tool(structured_output=True)
    def get_remote_status(remote: str) -> dict[str, object]:
        """Get cached remote and capability diagnostics without performing probes."""
        return api.get_remote_status(remote)

    @mcp.tool(structured_output=True)
    async def refresh_health(
        remote: str | None = None,
        capability: str | None = None,
    ) -> dict[str, object]:
        """Run bounded configured health checks; no repair or arbitrary execution."""
        return await api.refresh_health(remote, capability)

    return mcp

