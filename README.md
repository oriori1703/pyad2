# Yad2 client, CLI & MCP

A Python `uv` workspace for agents searching Yad2, with first-class support for second-hand furniture,
computer parts, and **all marketplace categories**. Browser-backed access extracts structured page data
instead of scraping listing text or guessing private API endpoints.

- **`yad2-client`** — asynchronous, typed library; independently usable without MCP or Typer.
- **`yad2-agent`** — JSON CLI and MCP stdio server built on the library.
- **Other sections** — normalized property/vehicle feeds and listing details through their existing URLs;
  bounded JSON inspection for other Next.js pages.

## Quick start

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```sh
uv sync --locked --all-packages
uv run yad2 install-browser

# The example listing (a numeric market ID works too)
uv run yad2 get https://www.yad2.co.il/market/item/15405193887804

# Used/like-new graphics cards under ₪2,000
uv run yad2 search RTX --product-type 285 --condition 2 --condition 3 --max-price 2000 --sort priceAsc

# Furniture: text, collection, price and city filters
uv run yad2 search 'ספה' --collection furniture --max-price 1500 --city 5000

# Discover live collection slugs
uv run yad2 categories --query computer
uv run yad2 categories --query 'ריהוט'

# Offline product/attribute IDs and readable enum names
uv run yad2 product-types --query computer
uv run yad2 attributes COMPUTER_COMPONENTS
uv run yad2 search RTX --product-type COMPUTER_COMPONENTS --attribute 155=13287

# Pagination: each call returns one full upstream page, plus next_url
uv run yad2 search RTX --page 2
uv run yad2 search-url 'https://www.yad2.co.il/market/search?q=RTX&pageNumber=3'

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

Successful commands emit UTF-8 JSON on stdout. Results include `source_url`, `fetched_at`, and `cached`;
search results also include `total`, `page`, `total_pages`, `next_url`, and parser `warnings`. Missing
prices remain `null`; a listed price of zero stays zero. No phone-reveal action is performed.

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
| `get_listing` | Market numeric ID or full listing URL |
| `list_categories` | Live collection discovery, filtered by English slug/Hebrew title |
| `list_product_types` | Offline product IDs, enum names, and bilingual labels |
| `list_attributes` | Offline product-specific attributes, input types, and option IDs |
| `search_url` | Marketplace, property, and vehicle feeds with their existing URL filters |
| `inspect_page` | Bounded Next.js JSON inspection using a dot path |

All tools declare input/output schemas, structured results, and read-only annotations. Example call:

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

The server owns one lazy browser session and serializes navigation. Concurrent identical requests are
coalesced through the cache. Listing descriptions remain untrusted seller content.

## Browser access and blocking

Plain HTTP requests currently encounter Yad2's Radware verification. This implementation uses Patchright
with Chromium, a matching browser-version user agent, Hebrew locale, and persisted cookies/local storage.
It allows normal browser verification to finish and extracts `__NEXT_DATA__`.

To reduce repeated challenges:

- Reuse a browser context and its saved session between invocations.
- Cache successful reads for five minutes, with a 1,000-page disk-cache bound.
- Space page navigations at least three seconds apart, including across processes sharing the cache.
- On a challenge or HTTP 429, stop immediately and persist a five-minute cooldown, extended by `Retry-After`.
- Keep cached results available during cooldown. `--refresh` and `cache-clear` do not bypass it.

If verification needs interaction:

```sh
uv run yad2 session
```

A visible browser opens for up to three minutes. Complete verification there; a successful page load saves
the session and clears the cooldown. A blocked MCP request releases its browser lock so this command can
run while the MCP process remains alive. Otherwise only one browser owner per data directory is allowed;
close a running owner before opening a second one.

No method guarantees permanent access: Yad2 can change its challenges, page schemas, or availability.
Challenges are errors, never disguised as empty search results. There are no automatic retry loops.

### Configuration

Constructor settings and equivalent environment variables:

| Variable | Default | Meaning |
| --- | --- | --- |
| `YAD2_HEADLESS` | `true` | Set `false` to make regular browsing visible |
| `YAD2_CHANNEL` | `chromium` | Patchright browser channel; `chrome` uses an installed Chrome |
| `YAD2_DATA_DIR` | OS user-data directory / `yad2` | Private browser session state and ownership lock |
| `YAD2_CACHE_DIR` | OS user-cache directory / `yad2` | SQLite cache and request budget |
| `YAD2_TIMEOUT_SECONDS` | `45` | Browser navigation/readiness timeout |
| `YAD2_MIN_INTERVAL_SECONDS` | `3` | Minimum spacing between page navigations |
| `YAD2_CACHE_TTL_SECONDS` | `300` | Cache freshness; `0` disables cache reads |
| `YAD2_COOLDOWN_SECONDS` | `300` | Minimum cooldown after a block |

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


anyio.run(main, backend="asyncio")
```

`Yad2Client(settings=Settings(...), transport=...)` supports explicit configuration and injectable async
transports. Pure parsers consume `PageSnapshot` objects for offline tests. Package dependencies flow
from agent → client, never the reverse.

Async orchestration uses AnyIO, including task groups, locks, worker threads, and filesystem access.
Use AnyIO's `asyncio` backend for browser-backed operations because Patchright requires it. The test
suite uses AnyIO's pytest plugin with the same backend.

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
cache persistence/expiry, pacing, challenge cooldown, CLI output, and a real MCP stdio subprocess round trip.
CI runs offline checks only. The live example test may eventually fail when the listing expires.

Live-verified on **2026-11-05**: the supplied item (NVIDIA 5070 ti, ₪3,500, Tel Aviv–Yafo), RTX searches,
price/product-type filtering, second-page navigation, furniture searches, category discovery, and
property/vehicle feed payloads. This records an observation, not a guarantee of future availability.

## Inspiration

[Guy2co/yad2-mcp](https://github.com/Guy2co/yad2-mcp) demonstrated the Patchright + Next.js approach.
This is an independent Python implementation; marketplace shapes and filters were checked against live
pages rather than assuming that the property site's schema applies to second-hand goods.
