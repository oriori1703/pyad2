"""Data-source interfaces: raw JSON out, domain parsing belongs to the client."""

from typing import Protocol, runtime_checkable

from yad2_client.errors import ParseError
from yad2_client.extraction import extract_api, extract_listing, extract_search
from yad2_client.models import PageSnapshot, RawPayload


class Transport(Protocol):
    """Minimal legacy page transport; adapted to DataSource by Yad2Client."""

    async def fetch(self, url: str) -> PageSnapshot: ...

    async def close(self) -> None: ...


@runtime_checkable
class DataSource(Transport, Protocol):
    """All implementations return the same unwrapped JSON object for each operation."""

    async def fetch_search(self, url: str) -> RawPayload: ...

    async def fetch_filters(self, url: str) -> RawPayload: ...

    async def fetch_listing(self, url: str) -> RawPayload: ...


@runtime_checkable
class FilterTransport(Protocol):
    """Compatibility for custom page transports returning the original filter envelope."""

    async def fetch_filters(self, url: str) -> PageSnapshot: ...


class PageDataSource:
    """Adapt older fetch-only custom transports to the raw-payload interface."""

    def __init__(self, transport: Transport) -> None:
        self.transport = transport

    async def fetch(self, url: str) -> PageSnapshot:
        return await self.transport.fetch(url)

    async def fetch_search(self, url: str) -> RawPayload:
        return extract_search(await self.transport.fetch(url))

    async def fetch_listing(self, url: str) -> RawPayload:
        return extract_listing(await self.transport.fetch(url))

    async def fetch_filters(self, url: str) -> RawPayload:
        if not isinstance(self.transport, FilterTransport):
            raise ParseError("This custom transport does not support live filter discovery")
        return extract_api(await self.transport.fetch_filters(url), "filters")

    async def close(self) -> None:
        await self.transport.close()
