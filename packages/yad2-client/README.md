# yad2-client

Async HTTP-first Yad2 library. Public entry points: `Yad2Client`, `Settings`, `MarketSearch`, `SortOrder`,
`DataSource`, `RawPayload`, `HttpTransport`, and typed result models. The default transport shares an automatically started local
HTTP/browser service across processes; it expires after five idle minutes. HTTPX2 reuses verified browser
cookies and a matching user agent for marketplace APIs and listing HTML without keeping Chromium open.
Run `yad2 session` once to initialize the session; challenges require explicit verification.
`Settings(transport="browser")` forces browser reads. An injected `BrowserTransport` provides exclusive
in-process browser ownership; `HttpTransport` provides HTTP-only access through `Yad2Client`.

`search` handles all market collections; `search_url` and `get_listing` support observed marketplace,
real-estate and vehicle schemas. `page` exposes raw `PageSnapshot` data for custom adapters. `categories`
discovers the current marketplace collection tree. No MCP/CLI dependencies are imported by this package.

Browser and HTTP sources implement the same `DataSource` interface and return unwrapped `RawPayload`
JSON for searches, filters, and listing details. Shared, network-free parsers validate these objects and
construct domain models. Sources only fetch/extract; HTTP does not construct Next.js wrappers or invoke
domain parsers. `PageSnapshot` remains the separate original-page inspection contract. Custom fetch-only
transports are automatically adapted, and successful payloads share a source-independent cache.

See the workspace README for setup, filtering, configuration, and limitations.
