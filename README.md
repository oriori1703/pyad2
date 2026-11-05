# Yad2 client, CLI & MCP

A Python `uv` workspace for agents searching Yad2, with first-class support for second-hand furniture,
computer parts, and **all marketplace categories**. HTTP-first access uses verified JSON endpoints and
structured page data, with Chromium for session verification and browser-only pages.

- **`yad2-client`** — asynchronous, typed library; independently usable without MCP or Typer.
- **`yad2-agent`** — JSON CLI and MCP stdio server built on the library.
- **Other sections** — normalized property/vehicle feeds and listing details through their existing URLs;
  bounded JSON inspection for other Next.js pages.

## Quick start

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```sh
uv sync --locked --all-packages
uv run yad2 install-browser
uv run yad2 session  # Initialize saved cookies and the matching HTTP user agent once.

# The example listing (a numeric market ID works too)
uv run yad2 get https://www.yad2.co.il/market/item/15405193887804

# Used/like-new graphics cards under ₪2,000
uv run yad2 search RTX --product-type 285 --condition 2 --condition 3 --max-price 2000 --sort priceAsc

# Furniture: a text search, or a filtered collection
uv run yad2 search 'ספה' --max-price 1500 --city 5000
uv run yad2 search --collection furniture --max-price 1500 --city 5000

# Discover live collection slugs
uv run yad2 categories --query computer
uv run yad2 categories --query 'ריהוט'

# Offline product/attribute IDs and readable enum names
uv run yad2 product-types --query computer
uv run yad2 attributes COMPUTER_COMPONENTS
uv run yad2 search RTX --product-type COMPUTER_COMPONENTS --attribute 155=13287

# Pagination: a single page, or a bounded sequential scan
uv run yad2 search RTX --page 2
uv run yad2 search RTX --max-pages 3 --max-items 100
uv run yad2 search-url 'https://www.yad2.co.il/market/search?q=RTX&pageNumber=3'

# Live filter options and counts for a query or collection
uv run yad2 filters RTX --product-type COMPUTER_COMPONENTS
uv run yad2 filters --collection furniture

# Save a search locally; each check fetches fresh pages
uv run yad2 watch save gpu --search-json '{"query":"RTX","product_types":["285"],"max_price":2000}' --max-pages 3
uv run yad2 watch check gpu
uv run yad2 watch list
uv run yad2 watch delete gpu

# Other sections: copy the site's filtered URL; query names differ by section
uv run yad2 search-url 'https://www.yad2.co.il/realestate/rent?city=5000&maxPrice=6000'
```

Queries remain in the supplied language. Hebrew usually works best for furniture; English product/model
names work well for hardware. Results reflect Yad2's relevance and promotion logic, not exact substring
matching. Conditions currently use `1` = new in packaging, `2` = like new, `3` = used. Computer parts use
product type `285`; use returned `product_type_id` and `city_id` fields or the site's filtered URLs for
other IDs. `--city`, `--area`, `--condition`, `--product-type`, and `--attribute` are repeatable.
Product types accept numeric IDs, enum names, or exact English/Hebrew labels in the CLI.
Attribute filters use `ATTRIBUTE_ID=OPTION_ID[,OPTION_ID]`; combine multiple options for one attribute.
Use a text query **or** a collection: observed collection URLs containing `q` redirect to global search.
Those combinations retain browser routing; bounded scans reject a changed feed rather than claiming
collection-specific coverage.

Successful commands emit UTF-8 JSON on stdout. Single-page results include `source_url`, `fetched_at`, and
`cached`; search results also include `total`, `page`, `total_pages`, `next_url`, and parser `warnings`. Missing
prices remain `null`; a listed price of zero stays zero. No phone-reveal action is performed.

### Live filters and bounded searches

`filters` reads the verified frontend filter endpoint over HTTP, with the same cache, pacing,
and challenge handling as searches. Supply a query or collection. `--search-json` accepts a complete
`MarketSearch` object and replaces the simpler arguments. Returned product types, conditions, cities,
price bounds, and attribute options reflect that search. Each attribute reports `total_options` and
`options_complete`: the frontend may return only a subset. The offline publishing taxonomy remains
useful for broader discovery but does not imply that every option is currently searchable.
Some collection filters contain repeated IDs with conflicting labels/counts. Those rows remain intact,
`warnings` identifies affected groups, and affected attribute option lists are not marked complete.

`search` and `search-url` preserve their single-page output with default limits. Changing `--max-pages`
(1–20) or `--max-items` (1–1,000) returns a `SearchBatch`: unique listings, per-page provenance, duplicate
count, warnings, `next_url`, `stop_reason`, and `complete`. Pages are fetched sequentially; errors abort
the scan. Stop reasons are `exhausted`, `page_limit`, `item_limit`, and `pagination_unknown`.
`complete` requires traversal from page 1 to known exhaustion with consistent metadata and no warnings;
it describes observed coverage, not a frozen snapshot of a changing marketplace. When an item limit
cuts through a page, `next_url` points back to that page; resuming callers must deduplicate and use a
large enough item budget to progress past the already seen entries.

### Saved searches

Watches live in `YAD2_DATA_DIR/watches.sqlite3` and run **on demand** with `watch check`; there is no
background scheduler. The first successful check establishes a baseline. Later checks report new and
returned listings, known-price changes (including drops), and `no_longer_in_results`. Only a complete
scan can mark a previously observed listing absent; absence does not establish sale or deletion.
An incomplete scan still updates observed listings, and a newly encountered listing may have existed
outside earlier scan limits. Unknown prices are not treated as zero or reported as price changes.

Checks always fetch fresh pages. Failed scans preserve the previous baseline; overlapping checks or
configuration edits cannot overwrite newer state. Saving an unchanged definition preserves its baseline;
changing its search or limits resets it. Saved searches must start on page 1. Deleting a watch removes
its definition and observation history; `cache-clear` leaves watches intact.

## MCP setup

Add this to your MCP client's server configuration, replacing the absolute project path:

```json
{
  "mcpServers": {
    "yad2": {
      "command": "uv",
      "args": ["--directory", "/absolute/path/to/yad2", "run", "--locked", "--package", "yad2-agent", "yad2-mcp"]
    }
  }
}
```

`uv run yad2 serve` is an equivalent stdio entry point. Install the browser before starting the server.
Use an absolute path to `uv` if your desktop client's PATH does not include it.

| Tool | Purpose |
| --- | --- |
| `search_market` | Typed marketplace query/filter object, plus optional `refresh` |
| `get_search_filters` | Live query-specific filter options, counts, and option-list completeness |
| `search_pages` | Bounded marketplace pagination with deduplication and explicit coverage |
| `get_listing` | Market numeric ID or full listing URL |
| `list_categories` | Live collection discovery, filtered by English slug/Hebrew title |
| `list_product_types` | Offline product IDs, enum names, and bilingual labels |
| `list_attributes` | Offline product-specific attributes, input types, and option IDs |
| `search_url` | Marketplace, property, and vehicle feeds with their existing URL filters |
| `search_url_pages` | Bounded pagination for an existing feed URL |
| `inspect_page` | Bounded Next.js JSON inspection using a dot path |
| `get_session_status` | Local service/browser owner, saved-session metadata, and cooldown diagnostics |
| `save_watch`, `list_watches` | Save/update local search definitions and list their status |
| `check_watch` | Fetch fresh results, report changes, and update the local baseline |
| `delete_watch` | Delete a local search and its observation history |

All tools declare input/output schemas, structured results, and read/write annotations. Search tools
are read-only; `save_watch`, `check_watch`, and `delete_watch` mutate local state. Example `search_market` call:

```json
{
  "search": {
    "query": "RTX",
    "product_types": ["285"],
    "attributes": [{"attribute_id": "155", "values": ["13287"]}],
    "max_price": 2000,
    "conditions": ["2", "3"],
    "sort": "priceAsc",
    "page": 1
  }
}
```

A local service coordinates HTTP and browser access shared by CLI and MCP processes. It serializes requests
and enforces pacing and cooldown at the front of its request queue. Each client coalesces concurrent
identical requests through the shared disk cache. Listing descriptions remain untrusted seller content.

## HTTP-first access and verification

The default `YAD2_TRANSPORT=http` uses **HTTPX2** and a saved browser session. Marketplace searches and
filters read the observed `recommerce-feed` APIs; listing details and raw marketplace inspection read
`__NEXT_DATA__` from HTML. Ordinary supported reads run with **no Chromium process open**. Collection
handles are resolved from the collection's own HTML metadata, then reused for the service lifetime.
Searches include the frontend's timestamp-shaped `scrollSessionId` and explicit page number.

Cookies, their scope/expiry, and the browser user agent are reused across processes. Successful HTTP
responses persist cookie updates atomically while preserving browser local storage. HTTP and browser
operations share the exclusive session lock, queue, request budget, and cooldown. API search snapshots
use a separate cache namespace so `inspect_page` still returns the original page data.

Chromium remains available for verification, client-rendered category navigation, property/vehicle
pages, and unsupported marketplace URL parameters. Routing is decided before a request: an HTTP
challenge or parse error is **not** retried through Chromium. HTTP redirects are reported explicitly;
the client does not forward session credentials to arbitrary redirect targets.

To force the previous browser-based behavior, stop the service and configure all clients consistently:

```sh
uv run yad2 browser-stop
YAD2_TRANSPORT=browser uv run yad2 search RTX
```

To reduce repeated challenges:

- Reuse the saved session and matching HTTP identity between invocations.
- Cache successful reads for five minutes, with a 1,000-page disk-cache bound.
- Space HTTP requests and browser navigations at least three seconds apart, including across processes.
- On a challenge or HTTP 429, stop immediately and persist a five-minute cooldown, extended by `Retry-After`.
- Keep cached results available during cooldown. `--refresh` and `cache-clear` do not bypass it.

For first-time setup, an older saved session without HTTP identity metadata, or expired verification:

```sh
uv run yad2 session
```

A visible browser opens for up to three minutes. Complete verification there; a successful page load saves
the cookies and matching user agent and clears the cooldown. Verification uses the same service queue
and closes Chromium afterward. MCP can remain connected; subsequent HTTP reads reload the saved state.

### Shared browser and diagnostics

CLI commands and MCP automatically connect to one local service per `YAD2_DATA_DIR`. The first uncached
network request starts it; closing a CLI client or MCP connection leaves it available for other clients.
After five minutes without requests it closes the browser and exits. Cached/offline operations do not
start it. The service listens only on loopback and authenticates requests using a private local token.

```sh
# Inspect ownership and cooldown without starting a service or navigating Yad2.
uv run yad2 status

# Stop the service once its requests have finished; the next network request starts it again.
uv run yad2 browser-stop
```

`status` returns JSON with the service PID, configured transport, actual `browser_running` state, current
operation, queue length, session-lock owner, HTTP identity presence, saved-state timestamp, cache count,
and remaining cooldown. The historical `browser_lock` field also covers HTTP cookie-state access.
Credentials and cookies are omitted; HTTP identity presence does not establish session validity.
Saved-state presence does not assert that an account login is still valid. Ordinary public searches do
not require account login; a complete taxonomy refresh does.

**Upgrading an already running MCP server:** restart it to load the new tools. Run `uv run yad2 browser-stop`
to stop any older service; the next uncached request starts the updated HTTP-first protocol. Run
`uv run yad2 session` once if `status` reports missing HTTP identity metadata.
An older process or a direct catalog capture can still own the exclusive browser lock; `status` identifies
this situation. Finish that operation before using the shared service. When changing service settings,
run `browser-stop` first. Startup diagnostics are in `browser-service.log` in the private data directory.

Clients wait up to two minutes in the queue. A lost service connection is reported without replaying a
network request. After a service crash, a subsequent command can start a new service using the saved state.

No method guarantees permanent access: Yad2 can change its challenges, page schemas, or availability.
Challenges are errors, never disguised as empty search results. There are no automatic retry loops.

### Configuration

Constructor settings and equivalent environment variables:

| Variable | Default | Meaning |
| --- | --- | --- |
| `YAD2_TRANSPORT` | `http` | HTTP-first marketplace access; `browser` forces browser-backed reads |
| `YAD2_HEADLESS` | `true` | Set `false` to make regular browsing visible |
| `YAD2_CHANNEL` | `chromium` | Patchright browser channel; `chrome` uses an installed Chrome |
| `YAD2_DATA_DIR` | OS user-data directory / `yad2` | Browser session state, ownership lock, and saved searches |
| `YAD2_CACHE_DIR` | OS user-cache directory / `yad2` | SQLite cache and request budget |
| `YAD2_TIMEOUT_SECONDS` | `45` | HTTP request and browser navigation/readiness timeout |
| `YAD2_MIN_INTERVAL_SECONDS` | `3` | Minimum spacing between HTTP requests or browser navigations |
| `YAD2_CACHE_TTL_SECONDS` | `300` | Cache freshness; `0` disables cache reads |
| `YAD2_COOLDOWN_SECONDS` | `300` | Minimum cooldown after a block |
| `YAD2_SERVICE_IDLE_SECONDS` | `300` | Idle time before the shared browser service exits |
| `YAD2_SERVICE_START_TIMEOUT_SECONDS` | `20` | Maximum wait for local service startup |
| `YAD2_BROWSER_QUEUE_TIMEOUT_SECONDS` | `120` | Maximum wait for another shared HTTP/browser operation to finish |

State is outside the repository. Browser state files are saved with owner-only permissions. On macOS,
default state is under `~/Library/Application Support/yad2` and cache under `~/Library/Caches/yad2`.
`uv run yad2 cache-clear` deletes page data but preserves the request budget.

Application errors are JSON on stderr: exit `2` for invalid input/other failures, `3` for verification,
and `4` for rate limiting/cooldown. Typer argument/option usage errors use its standard stderr help.
MCP failures use protocol error results with actionable messages.

## Python library

```python
import anyio

from yad2_client import MarketSearch, Yad2Client


async def main() -> None:
    async with Yad2Client() as client:
        results = await client.search(MarketSearch(query="RTX", product_types=("285",), max_price=2000))
        for listing in results.listings:
            print(listing.title, listing.price, listing.url)
        if results.listings:
            details = await client.get_listing(results.listings[0].url)
            print(details.listing.description)

        filters = await client.filters(MarketSearch(query="RTX", product_types=("285",)))
        print(filters.attributes)
        batch = await client.search_pages(MarketSearch(query="RTX"), max_pages=3, max_items=100)
        print(batch.complete, batch.stop_reason, batch.next_url)


anyio.run(main, backend="asyncio")
```

`Yad2Client(settings=Settings(...), transport=...)` supports explicit configuration and injectable async
transports. Pure parsers consume `PageSnapshot` objects for offline tests. Package dependencies flow
from agent → client, never the reverse.
The default transport uses the shared service. For explicit in-process ownership, inject
`BrowserTransport(settings)` from `yad2_client.browser` and close the client when finished; this mode
holds the exclusive browser lock and is intended for standalone browser workflows.
An injected `HttpTransport(settings)` from `yad2_client` provides direct HTTP-only access with a previously
initialized session. Use it through `Yad2Client` for parsing, caching, and persistent challenge cooldown;
the shared default is preferred when CLI/MCP or multiple clients may run concurrently.

Async orchestration uses AnyIO, including task groups, locks, worker threads, and filesystem access.
Use AnyIO's `asyncio` backend for browser-backed operations because Patchright requires it. The test
suite uses AnyIO's pytest plugin with the same backend.

### Comparison-friendly listing attributes

Listings retain their raw `attributes` and also expose `normalized_attributes` when the payload contains
explicit attribute rows (often on listing details). Each entry keeps `raw_value`, bilingual labels,
option ID, attribute ID/name, and `id_source`. IDs present in the listing are marked `listing`; missing
IDs resolved by an unambiguous label in that product's catalog are marked `catalog`. Unknown attributes
remain available without invented IDs. Fetch listing details when feed cards omit these attributes.

Explicit measurements normalize to `cm`, `GB`, `MHz`, or `kg` through `numeric_value` and `unit`.
Units must appear in the value or attribute label; ranges and unitless values remain raw. Storage
conversion uses decimal units (1 TB = 1,000 GB). These fields describe seller-supplied values, not
verified dimensions or hardware compatibility. Public result types are exported from `yad2_client`.

### Product and attribute enums

The bundled marketplace taxonomy comes from the authenticated publishing catalog, which includes
product types and options absent from current search results. It covers marketplace goods; property
and vehicle taxonomies are separate. Reading the bundled snapshot requires neither login nor a browser.
The **2026-11-05** snapshot contains **267 product types, 322 attributes, and 3,616 attribute-option IDs**.

```python
from yad2_client import Attribute, AttributeFilter, MarketSearch, ProductType
from yad2_client.catalog import catalog_product, catalog_products
from yad2_client.options import ComputerComponentTypeOption

assert ProductType("285") is ProductType.COMPUTER_COMPONENTS
print(ProductType.COMPUTER_COMPONENTS.label_he)  # רכיבי מחשב
print(ProductType.COMPUTER_COMPONENTS.label_en)  # Computer Components

search = MarketSearch(
    query="RTX",
    product_types=(ProductType.COMPUTER_COMPONENTS,),
    attributes=(
        AttributeFilter(
            attribute_id=Attribute.COMPUTER_COMPONENT_TYPE,
            values=(ComputerComponentTypeOption.כרטיס_מסך,),
        ),
    ),
)
print(catalog_products("furniture"))
print(catalog_product(ProductType.COMPUTER_COMPONENTS).attributes)
```

`ProductType` and `Attribute` are string enums: their values are Yad2 IDs and they serialize as strings.
`yad2_client.options` contains one option enum per attribute and an `OPTION_ENUMS` registry keyed by
`Attribute`. Product/attribute names use the source English labels. Option names preserve the supplied
labels, often Hebrew-only; their `label_en` is empty when the source supplies no English translation.
Duplicate names receive an ID suffix. The original labels remain available on every enum member.

The global option enums contain the union across products. Use `catalog_product()` / `list_attributes`
to discover which options apply to a particular product, including its input type and required flag.
Publishing fields are not necessarily available as search filters; `AttributeFilter` represents
option-ID filters observed in the search frontend, not arbitrary numeric/text publishing values.
Raw numeric IDs remain supported for additions newer than the snapshot. Snapshot results include
their fetch date and source URL; enum conversion raises `ValueError` for unknown IDs.

#### Refreshing the snapshot

The refresh tool uses authenticated, sequential browser GET requests, with at least three seconds
between requests, stopping on challenges or errors. It never submits the publishing form. Use a fresh
capture directory for a new snapshot; reuse it only to resume an interrupted capture.

```sh
# Sign in locally and leave the browser open; metadata is stored outside the repository.
uv run yad2 browser-stop
uv run python tools/update_catalog.py --login --capture-dir "$HOME/.cache/yad2-catalog-2026-11-05"

# Resume using the saved session and already captured responses.
uv run python tools/update_catalog.py --fetch --capture-dir "$HOME/.cache/yad2-catalog-2026-11-05"

# Deterministic offline regeneration from a complete capture.
uv run python tools/update_catalog.py --capture-dir "$HOME/.cache/yad2-catalog-2026-11-05"
```

Generation requires a response for every catalog product; explicit empty attribute arrays are valid,
but missing responses, conflicting labels, and malformed data fail. Only normalized taxonomy metadata
is checked in under `yad2_client/data/catalog.json`; session state remains in the private data directory.

### Extending site coverage

Search and item adapters currently cover observed marketplace, property, and vehicle payloads. Other site
sections may use different frontends; universal normalized coverage is not claimed. Inspect structured
data without changing the browser layer:

```sh
uv run yad2 page 'https://www.yad2.co.il/vehicles/cars' --path props.pageProps
uv run yad2 page 'https://www.yad2.co.il/vehicles/cars' --path props.pageProps.dehydratedState.queries.0.state.data
```

Oversized JSON values return `omitted: true`, size and child keys rather than broken/truncated JSON.
Descend with a narrower path. Pages without `__NEXT_DATA__` produce an explicit unsupported-page error.

## Development

```sh
uv sync --locked --all-packages
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy .
uv build --all-packages

# Opt-in live smoke check (installed Chromium and working Yad2 access required)
uv run pytest -m live tests/test_live.py
```

Offline tests cover URL/filter validation, observed payload shapes, missing/malformed data, pagination,
cache persistence/expiry, pacing, challenge cooldown, attribute normalization, saved-search concurrency,
CLI output, cookie scope/persistence, HTTP cancellation, cross-client session updates, and a real MCP
stdio subprocess round trip. Saved-search comparisons and conflict handling are tested offline.
CI runs offline checks only. The live example test may eventually fail when the listing expires.

Live-verified on **2026-11-05**: the supplied item (NVIDIA 5070 ti, ₪3,500, Tel Aviv–Yafo), RTX searches,
price/product-type filtering, second-page navigation, furniture searches, category discovery, and
property/vehicle feed payloads. This records an observation, not a guarantee of future availability.
HTTP-first validation on the same date covered filtered hardware searches, listing attributes, furniture
filters (including conflicting upstream IDs), two-page hardware and furniture scans (42 unique listings
each), and fresh CLI/MCP requests sharing a restarted service with `browser_running: false`. Session
reuse across processes is verified; long-term cookie lifetime remains dependent on Yad2 verification.

## Inspiration

[Guy2co/yad2-mcp](https://github.com/Guy2co/yad2-mcp) demonstrated the Patchright + Next.js approach.
This is an independent Python implementation; marketplace shapes and filters were checked against live
pages rather than assuming that the property site's schema applies to second-hand goods.
