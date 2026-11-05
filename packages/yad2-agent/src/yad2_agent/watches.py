"""Local saved searches with atomic, conservative observation-based change tracking."""

import sqlite3
from collections.abc import Callable
from contextlib import closing
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import Self, TypeVar
from uuid import uuid4

import anyio
from pydantic import Field, model_validator

from yad2_client import MarketSearch, Yad2Client
from yad2_client.errors import Yad2Error
from yad2_client.models import Model, SearchBatch

T = TypeVar("T")


class WatchError(Yad2Error):
    code = "watch_error"


async def _io(operation: Callable[[], T]) -> T:
    try:
        return await anyio.to_thread.run_sync(operation)
    except (sqlite3.Error, OSError) as exc:
        raise WatchError(f"Cannot access the saved-search database: {exc}") from exc


class WatchDefinition(Model):
    name: str = Field(min_length=1, max_length=80, pattern=r"^\S(?:[^\r\n]*\S)?$")
    search: MarketSearch
    max_pages: int = Field(default=3, ge=1, le=20)
    max_items: int = Field(default=250, ge=1, le=1000)

    @model_validator(mode="after")
    def first_page(self) -> Self:
        if self.search.page != 1:
            raise ValueError("Saved searches must start at page 1")
        return self


class ObservedListing(Model):
    id: str
    url: str
    title: str
    price: float | None
    currency: str
    last_seen: datetime
    present: bool = True


class WatchState(Model):
    checked_at: datetime | None = None
    listings: tuple[ObservedListing, ...] = ()


class WatchInfo(Model):
    definition: WatchDefinition
    last_checked: datetime | None
    tracked_listings: int


class WatchList(Model):
    watches: tuple[WatchInfo, ...]


class PriceChange(Model):
    listing: ObservedListing
    old_price: float
    new_price: float
    is_drop: bool


class WatchChanges(Model):
    name: str
    checked_at: datetime
    baseline_created: bool
    scan: SearchBatch
    new_listings: tuple[ObservedListing, ...]
    returned_listings: tuple[ObservedListing, ...]
    price_changes: tuple[PriceChange, ...]
    no_longer_in_results: tuple[ObservedListing, ...]


class WatchDeleted(Model):
    name: str
    deleted: bool


def compare_watch(name: str, previous: WatchState, scan: SearchBatch) -> tuple[WatchState, WatchChanges]:
    now = datetime.now(UTC)
    tracked = {listing.url: listing for listing in previous.listings}
    seen: set[str] = set()
    new: list[ObservedListing] = []
    returned: list[ObservedListing] = []
    prices: list[PriceChange] = []
    missing: list[ObservedListing] = []
    for listing in scan.listings:
        seen.add(listing.url)
        observation = ObservedListing(
            id=listing.id,
            url=listing.url,
            title=listing.title,
            price=listing.price,
            currency=listing.currency,
            last_seen=now,
        )
        old = tracked.get(listing.url)
        if old is None and previous.checked_at is not None:
            new.append(observation)
        elif old is not None:
            if not old.present:
                returned.append(observation)
            if (
                old.price is not None
                and observation.price is not None
                and old.currency == observation.currency
                and old.price != observation.price
            ):
                prices.append(
                    PriceChange(
                        listing=observation,
                        old_price=old.price,
                        new_price=observation.price,
                        is_drop=observation.price < old.price,
                    )
                )
        tracked[listing.url] = observation
    if scan.complete:
        for url, observation in tuple(tracked.items()):
            if url not in seen and observation.present:
                absent = observation.model_copy(update={"present": False})
                missing.append(absent)
                tracked[url] = absent
    state = WatchState(checked_at=now, listings=tuple(tracked.values()))
    return state, WatchChanges(
        name=name,
        checked_at=now,
        baseline_created=previous.checked_at is None,
        scan=scan,
        new_listings=tuple(new),
        returned_listings=tuple(returned),
        price_changes=tuple(prices),
        no_longer_in_results=tuple(missing),
    )


class WatchStore:
    def __init__(self, directory: Path) -> None:
        self.path = directory / "watches.sqlite3"

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        connection = sqlite3.connect(self.path, timeout=10)
        try:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS watches (name TEXT PRIMARY KEY, definition TEXT NOT NULL, "
                "state TEXT NOT NULL, revision TEXT NOT NULL)"
            )
        except sqlite3.Error:
            connection.close()
            raise
        return connection

    def _read(self, name: str) -> tuple[WatchDefinition, WatchState, str]:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT definition, state, revision FROM watches WHERE name = ?", (name,)
            ).fetchone()
        if row is None:
            raise WatchError(f"Saved search {name!r} does not exist")
        return WatchDefinition.model_validate_json(row[0]), WatchState.model_validate_json(row[1]), row[2]

    def _save(self, definition: WatchDefinition) -> WatchInfo:
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT definition, state FROM watches WHERE name = ?", (definition.name,)
            ).fetchone()
            state = (
                WatchState.model_validate_json(row[1])
                if row and WatchDefinition.model_validate_json(row[0]) == definition
                else WatchState()
            )
            connection.execute(
                "INSERT INTO watches VALUES (?, ?, ?, ?) ON CONFLICT(name) DO UPDATE SET "
                "definition = excluded.definition, state = excluded.state, revision = excluded.revision",
                (definition.name, definition.model_dump_json(), state.model_dump_json(), uuid4().hex),
            )
        return WatchInfo(definition=definition, last_checked=state.checked_at, tracked_listings=len(state.listings))

    async def save(self, definition: WatchDefinition) -> WatchInfo:
        return await _io(partial(self._save, definition))

    def _list(self) -> WatchList:
        if not self.path.exists():
            return WatchList(watches=())
        with closing(self._connect()) as connection:
            rows = connection.execute("SELECT definition, state FROM watches ORDER BY name").fetchall()
        watches: list[WatchInfo] = []
        for definition, state_json in rows:
            state = WatchState.model_validate_json(state_json)
            watches.append(
                WatchInfo(
                    definition=WatchDefinition.model_validate_json(definition),
                    last_checked=state.checked_at,
                    tracked_listings=len(state.listings),
                )
            )
        return WatchList(watches=tuple(watches))

    async def list(self) -> WatchList:
        return await _io(self._list)

    def _delete(self, name: str) -> WatchDeleted:
        with closing(self._connect()) as connection, connection:
            cursor = connection.execute("DELETE FROM watches WHERE name = ?", (name,))
        return WatchDeleted(name=name, deleted=cursor.rowcount > 0)

    async def delete(self, name: str) -> WatchDeleted:
        return await _io(partial(self._delete, name))

    def _commit(self, name: str, revision: str, state: WatchState) -> None:
        with closing(self._connect()) as connection, connection:
            cursor = connection.execute(
                "UPDATE watches SET state = ?, revision = ? WHERE name = ? AND revision = ?",
                (state.model_dump_json(), uuid4().hex, name, revision),
            )
            if cursor.rowcount != 1:
                raise WatchError(
                    "Saved search changed during this check; its previous state was preserved. Run check again."
                )

    async def check(self, name: str, client: Yad2Client) -> WatchChanges:
        definition, state, revision = await _io(partial(self._read, name))
        scan = await client.search_pages(
            definition.search, max_pages=definition.max_pages, max_items=definition.max_items, refresh=True
        )
        updated, changes = compare_watch(name, state, scan)
        await _io(partial(self._commit, name, revision, updated))
        return changes
