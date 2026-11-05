import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from yad2_client.models import PageSnapshot


async def test_stdio_round_trip_schemas_structured_results_and_errors(seeded_cache: PageSnapshot) -> None:
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-c", "from yad2_agent.server import main; main()"],
        env=dict(os.environ),
    )
    async with stdio_client(parameters) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        tools = (await session.list_tools()).tools
        assert {tool.name for tool in tools} == {
            "search_market",
            "search_url",
            "get_listing",
            "list_categories",
            "list_product_types",
            "list_attributes",
            "inspect_page",
        }
        assert all(tool.outputSchema for tool in tools)
        assert all(tool.annotations is not None and tool.annotations.readOnlyHint for tool in tools)
        products = await session.call_tool("list_product_types", {"query": "computer"})
        assert not products.isError and products.structuredContent is not None
        assert "285" in {product["id"] for product in products.structuredContent["products"]}
        attributes = await session.call_tool("list_attributes", {"product_type": "COMPUTER_COMPONENTS"})
        assert not attributes.isError and attributes.structuredContent is not None
        assert "155" in {attribute["id"] for attribute in attributes.structuredContent["attributes"]}
        result = await session.call_tool("search_market", {"search": {"query": "RTX"}})
        assert not result.isError
        assert result.structuredContent is not None
        assert result.structuredContent["listings"][0]["id"] == "123"
        assert result.structuredContent["cached"] is True
        failure = await session.call_tool("get_listing", {"id_or_url": "https://example.com/"})
        assert failure.isError
        invalid = await session.call_tool("search_market", {"search": {"page": 0}})
        assert invalid.isError
