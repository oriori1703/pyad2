import os
import socket
import subprocess
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import anyio
import pytest
from anyio.streams.buffered import BufferedByteReceiveStream
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client

from yad2_client.models import PageSnapshot


@asynccontextmanager
async def connect_server(transport: str) -> AsyncIterator[ClientSession]:
    entrypoint = ["-c", "from yad2_agent.server import main; main()"]
    if transport == "stdio":
        parameters = StdioServerParameters(command=sys.executable, args=entrypoint, env=dict(os.environ))
        async with stdio_client(parameters) as (read, write), ClientSession(read, write) as session:
            yield session
        return

    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    if transport == "http-cli":
        entrypoint = ["-c", "from yad2_agent.cli import app; app()", "serve"]
    command = [sys.executable, *entrypoint, "--transport", "http", "--host", "127.0.0.1", "--port", str(port)]
    async with await anyio.open_process(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE) as process:
        try:
            assert process.stderr is not None
            with anyio.fail_after(15):
                # Wait for the local socket to be bound, without probing Yad2 or retrying MCP calls.
                await BufferedByteReceiveStream(process.stderr).receive_until(b"Uvicorn running on", 65536)
                async with (
                    streamable_http_client(f"http://127.0.0.1:{port}/mcp") as (read, write),
                    ClientSession(read, write) as session,
                ):
                    yield session
        finally:
            with anyio.CancelScope(shield=True):
                if process.returncode is None:
                    process.terminate()
                with anyio.move_on_after(5):
                    await process.wait()
                if process.returncode is None:
                    process.kill()
                    await process.wait()


@pytest.mark.parametrize("transport", ["stdio", "http", "http-cli"])
async def test_round_trip_schemas_structured_results_and_errors(seeded_cache: PageSnapshot, transport: str) -> None:
    async with connect_server(transport) as session:
        await session.initialize()
        tools = (await session.list_tools()).tools
        assert {tool.name for tool in tools} == {
            "search_market",
            "search_url",
            "search_url_pages",
            "get_listing",
            "list_categories",
            "list_product_types",
            "list_attributes",
            "get_session_status",
            "get_search_filters",
            "search_pages",
            "save_watch",
            "list_watches",
            "check_watch",
            "delete_watch",
            "inspect_page",
        }
        assert all(tool.output_schema for tool in tools)
        writes = {"save_watch", "check_watch", "delete_watch"}
        assert all(
            tool.annotations is not None and tool.annotations.read_only_hint == (tool.name not in writes)
            for tool in tools
        )
        status = await session.call_tool("get_session_status", {})
        assert not status.is_error and status.structured_content is not None
        assert status.structured_content["service_state"] == "stopped"
        products = await session.call_tool("list_product_types", {"query": "computer"})
        assert not products.is_error and products.structured_content is not None
        assert "285" in {product["id"] for product in products.structured_content["products"]}
        attributes = await session.call_tool("list_attributes", {"product_type": "COMPUTER_COMPONENTS"})
        assert not attributes.is_error and attributes.structured_content is not None
        assert "155" in {attribute["id"] for attribute in attributes.structured_content["attributes"]}
        result = await session.call_tool("search_market", {"search": {"query": "RTX"}})
        assert not result.is_error
        assert result.structured_content is not None
        assert result.structured_content["listings"][0]["id"] == "123"
        assert result.structured_content["cached"] is True
        pages = await session.call_tool("search_pages", {"search": {"query": "RTX"}, "max_pages": 3})
        assert not pages.is_error and pages.structured_content is not None
        assert pages.structured_content["complete"] is True
        url_pages = await session.call_tool("search_url_pages", {"url": seeded_cache.url, "max_pages": 3})
        assert not url_pages.is_error and url_pages.structured_content is not None
        assert url_pages.structured_content["complete"] is True
        saved = await session.call_tool("save_watch", {"definition": {"name": "gpu", "search": {"query": "RTX"}}})
        assert not saved.is_error
        watches = await session.call_tool("list_watches", {})
        assert watches.structured_content is not None
        assert watches.structured_content["watches"][0]["definition"]["name"] == "gpu"
        deleted = await session.call_tool("delete_watch", {"name": "gpu"})
        assert deleted.structured_content == {"name": "gpu", "deleted": True}
        failure = await session.call_tool("get_listing", {"id_or_url": "https://example.com/"})
        assert failure.is_error
        invalid = await session.call_tool("search_market", {"search": {"page": 0}})
        assert invalid.is_error
