# yad2-client

Async browser-backed Yad2 library. Public entry points: `Yad2Client`, `Settings`, `MarketSearch`, `SortOrder`,
and typed result models. The default transport shares an automatically started local browser service
across processes; it expires after five idle minutes. An injected `BrowserTransport` provides exclusive
in-process ownership and releases browser resources when the client context exits.

`search` handles all market collections; `search_url` and `get_listing` support observed marketplace,
real-estate and vehicle schemas. `page` exposes raw `PageSnapshot` data for custom adapters. `categories`
discovers the current marketplace collection tree. No MCP/CLI dependencies are imported by this package.

See the workspace README for setup, filtering, configuration, and limitations.
