"""JSON-first command line interface; diagnostics never contaminate stdout."""

import json
import subprocess
import sys
from collections.abc import Awaitable, Callable
from typing import Annotated

import anyio
import typer
from pydantic import BaseModel

from yad2_agent.page import PageView, inspect_page
from yad2_agent.server import serve
from yad2_agent.watches import WatchDefinition, WatchStore
from yad2_client import MarketSearch, Settings, SortOrder, Yad2Client
from yad2_client.cache import Cache
from yad2_client.catalog import catalog_product, catalog_products, resolve_product_type
from yad2_client.errors import ChallengeRequired, RateLimited, Yad2Error
from yad2_client.models import AttributeFilter
from yad2_client.shared_browser import SharedBrowserTransport, session_status, stop_service

app = typer.Typer(no_args_is_help=True, help="Search Yad2 listings. JSON on stdout; errors on stderr.")
watch_app = typer.Typer(no_args_is_help=True, help="Save searches locally and check for listing changes.")
app.add_typer(watch_app, name="watch")
Refresh = Annotated[bool, typer.Option(help="Fetch fresh data instead of using the cache.")]


def _run(action: Callable[[Yad2Client], Awaitable[BaseModel]]) -> None:
    async def execute() -> BaseModel:
        async with Yad2Client() as client:
            return await action(client)

    try:
        result = anyio.run(execute, backend="asyncio")
    except (Yad2Error, ValueError, OSError) as exc:
        _fail(exc)
    else:
        typer.echo(result.model_dump_json(indent=2))


def _fail(exc: Exception) -> None:
    code = exc.code if isinstance(exc, Yad2Error) else "invalid_input" if isinstance(exc, ValueError) else "io_error"
    typer.echo(json.dumps({"error": {"code": code, "message": str(exc)}}, ensure_ascii=False), err=True)
    status = 3 if isinstance(exc, ChallengeRequired) else 4 if isinstance(exc, RateLimited) else 2
    raise typer.Exit(status) from exc


@app.command()
def search(
    query: Annotated[str, typer.Argument(help="Search text (Hebrew or English).")] = "",
    collection: Annotated[str | None, typer.Option(help="Collection slug from `yad2 categories`.")] = None,
    min_price: Annotated[int | None, typer.Option(min=0)] = None,
    max_price: Annotated[int | None, typer.Option(min=0)] = None,
    city: Annotated[list[str] | None, typer.Option(help="Yad2 city ID; repeatable.")] = None,
    area: Annotated[list[str] | None, typer.Option(help="Yad2 area ID; repeatable.")] = None,
    product_type: Annotated[list[str] | None, typer.Option(help="Product type ID or enum name; repeatable.")] = None,
    attribute: Annotated[list[str] | None, typer.Option(help="ATTRIBUTE_ID=OPTION_ID[,OPTION_ID]; repeatable.")] = None,
    condition: Annotated[list[str] | None, typer.Option(help="1=packaged new, 2=like new, 3=used; repeatable.")] = None,
    sort: SortOrder = SortOrder.RELEVANCE,
    page: Annotated[int, typer.Option(min=1, max=10000)] = 1,
    max_pages: Annotated[int, typer.Option(min=1, max=20)] = 1,
    max_items: Annotated[int, typer.Option(min=1, max=1000)] = 250,
    refresh: Refresh = False,
) -> None:
    """Search the marketplace; --max-pages enables a bounded, deduplicated scan."""
    _run(
        lambda client: _search(
            client,
            MarketSearch(
                query=query,
                collection=collection,
                min_price=min_price,
                max_price=max_price,
                cities=tuple(city or ()),
                areas=tuple(area or ()),
                product_types=tuple(resolve_product_type(value) for value in product_type or ()),
                conditions=tuple(condition or ()),
                attributes=tuple(_attribute_filter(value) for value in attribute or ()),
                sort=sort,
                page=page,
            ),
            refresh=refresh,
            max_pages=max_pages,
            max_items=max_items,
        )
    )


async def _search(
    client: Yad2Client, search: MarketSearch, *, max_pages: int, max_items: int, refresh: bool
) -> BaseModel:
    if max_pages == 1 and max_items == 250:
        return await client.search(search, refresh=refresh)
    return await client.search_pages(search, max_pages=max_pages, max_items=max_items, refresh=refresh)


@app.command("filters")
def live_filters(
    query: Annotated[str, typer.Argument()] = "",
    collection: str | None = None,
    product_type: Annotated[list[str] | None, typer.Option(help="Product ID or enum name; repeatable.")] = None,
    search_json: Annotated[
        str | None, typer.Option(help="Full MarketSearch JSON; replaces the simple search arguments.")
    ] = None,
    refresh: Refresh = False,
) -> None:
    """Discover live search options/counts, including option-list completeness."""
    _run(
        lambda client: client.filters(
            MarketSearch.model_validate_json(search_json)
            if search_json is not None
            else MarketSearch(
                query=query,
                collection=collection,
                product_types=tuple(resolve_product_type(value) for value in product_type or ()),
            ),
            refresh=refresh,
        )
    )


@watch_app.command("save")
def save_watch(
    name: str,
    search_json: Annotated[str, typer.Option(help="MarketSearch JSON using numeric filter IDs.")],
    max_pages: Annotated[int, typer.Option(min=1, max=20)] = 3,
    max_items: Annotated[int, typer.Option(min=1, max=1000)] = 250,
) -> None:
    """Save a local search; changing its configuration resets the observation baseline."""
    _run(
        lambda client: WatchStore(client.settings.data_dir).save(
            WatchDefinition(
                name=name,
                search=MarketSearch.model_validate_json(search_json),
                max_pages=max_pages,
                max_items=max_items,
            )
        )
    )


@watch_app.command("list")
def list_watches() -> None:
    """List saved searches and the time of their last successful check."""
    _run(lambda client: WatchStore(client.settings.data_dir).list())


@watch_app.command("check")
def check_watch(name: str) -> None:
    """Fetch fresh pages and atomically report new/returned listings and price changes."""
    _run(lambda client: WatchStore(client.settings.data_dir).check(name, client))


@watch_app.command("delete")
def delete_watch(name: str) -> None:
    """Delete a local saved search and its observation history."""
    _run(lambda client: WatchStore(client.settings.data_dir).delete(name))


def _attribute_filter(value: str) -> AttributeFilter:
    key, separator, options = value.partition("=")
    if not separator:
        raise ValueError("Attribute filters must use ATTRIBUTE_ID=OPTION_ID[,OPTION_ID]")
    return AttributeFilter(attribute_id=key, values=tuple(options.split(",")))


@app.command("product-types")
def product_types(query: str = "") -> None:
    """Find product IDs, enum names and bilingual labels in the bundled offline catalog."""
    typer.echo(catalog_products(query).model_dump_json(indent=2))


@app.command("attributes")
def product_attributes(product_type: str) -> None:
    """List a product type's attributes and applicable option IDs, entirely offline."""
    try:
        result = catalog_product(product_type)
    except ValueError as exc:
        _fail(exc)
    else:
        typer.echo(result.model_dump_json(indent=2))


@app.command("search-url")
def search_url(
    url: str,
    refresh: Refresh = False,
    max_pages: Annotated[int, typer.Option(min=1, max=20)] = 1,
    max_items: Annotated[int, typer.Option(min=1, max=1000)] = 250,
) -> None:
    """Read a feed URL, including property and vehicle sections and next_url pagination."""
    _run(
        lambda client: (
            client.search_url(url, refresh=refresh)
            if max_pages == 1 and max_items == 250
            else client.search_url_pages(url, max_pages=max_pages, max_items=max_items, refresh=refresh)
        )
    )


@app.command("get")
def get_listing(id_or_url: str, refresh: Refresh = False) -> None:
    """Get listing details using a numeric market ID or full Yad2 URL."""
    _run(lambda client: client.get_listing(id_or_url, refresh=refresh))


@app.command()
def categories(query: str = "", refresh: Refresh = False) -> None:
    """Discover category slugs; optionally filter by English slug or Hebrew name."""
    _run(lambda client: client.categories(query, refresh=refresh))


@app.command("page")
def page_data(
    url: str,
    path: str = "",
    max_chars: Annotated[int, typer.Option(min=100, max=50000)] = 16000,
    refresh: Refresh = False,
) -> None:
    """Inspect structured page JSON using a dot path; large values return keys for further inspection."""

    async def inspect(client: Yad2Client) -> PageView:
        return inspect_page(await client.page(url, refresh=refresh), path, max_chars)

    _run(inspect)


@app.command("install-browser")
def install_browser() -> None:
    """Install the Chromium version used by Patchright."""
    result = subprocess.run([sys.executable, "-m", "patchright", "install", "chromium"], check=False, stdout=sys.stderr)
    raise typer.Exit(result.returncode)


@app.command()
def session(
    url: str = "https://www.yad2.co.il/market/search?q=RTX",
    wait_seconds: Annotated[float, typer.Option(min=1, max=600)] = 180,
) -> None:
    """Open a visible browser for verification and save the session when a real page loads."""

    async def verify() -> None:
        settings = Settings.from_env()
        browser = SharedBrowserTransport(settings)
        try:
            await browser.interactive(url, wait_seconds)
        finally:
            await browser.close()

    typer.echo("Complete any verification in the opened browser; it closes after a successful page load.", err=True)
    try:
        anyio.run(verify, backend="asyncio")
    except (Yad2Error, ValueError, OSError) as exc:
        _fail(exc)
    typer.echo('{"verified":true}')


@app.command()
def status() -> None:
    """Show local service, browser owner, saved-session metadata and cooldown as JSON."""
    try:
        result = anyio.run(session_status, Settings.from_env(), backend="asyncio")
    except (Yad2Error, ValueError, OSError) as exc:
        _fail(exc)
    else:
        typer.echo(result.model_dump_json(indent=2))


@app.command("browser-stop")
def browser_stop() -> None:
    """Stop an idle shared browser service; the next browser request starts it again."""
    try:
        stopped = anyio.run(stop_service, Settings.from_env(), backend="asyncio")
    except (Yad2Error, ValueError, OSError) as exc:
        _fail(exc)
    else:
        typer.echo(json.dumps({"stopped": stopped}))


@app.command("cache-clear")
def cache_clear() -> None:
    """Delete cached pages (request cooldown remains in effect)."""
    try:
        anyio.run(Cache(Settings.from_env().cache_dir).clear, backend="asyncio")
    except (ValueError, OSError) as exc:
        _fail(exc)
    typer.echo('{"cleared":true}')


app.command()(serve)
