"""Capture authenticated GET metadata sequentially, or regenerate from a complete local capture."""

import argparse
import asyncio
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from pydantic import JsonValue, TypeAdapter

from yad2_client.browser import BrowserTransport
from yad2_client.catalog_codegen import canonical_snapshot, render_enums
from yad2_client.catalog_parsing import CATALOG_URL, parse_catalog, product_ids
from yad2_client.errors import ChallengeRequired, PageUnavailable, ParseError, RateLimited
from yad2_client.models import Model
from yad2_client.settings import Settings

JSON = TypeAdapter(JsonValue)
ROOT = Path(__file__).resolve().parents[1]
DESTINATION = ROOT / "packages/yad2-client/src/yad2_client"


class CatalogResponse(Model):
    status: int
    body: str


async def capture(directory: Path, *, login: bool) -> None:
    settings = Settings.from_env().model_copy(update={"headless": False})
    browser = BrowserTransport(settings)
    await asyncio.to_thread((directory / "attributes").mkdir, parents=True, exist_ok=True)
    try:
        page = await browser._start()
        if login:
            print("Sign in in the browser and leave it open. Waiting up to 10 minutes.", flush=True)
            async with page.context.expect_event(
                "response",
                predicate=lambda response: "/auth/user/details" in response.url and response.status == 200,
                timeout=600000,
            ):
                await page.goto("https://www.yad2.co.il/publish-ad-products/create", wait_until="load")
            await browser._save_state()
        else:
            await page.goto("https://www.yad2.co.il/publish-ad-products/create", wait_until="load")

        async def fetch(path: Path, suffix: str) -> JsonValue:
            if await asyncio.to_thread(path.exists):
                return JSON.validate_json(await asyncio.to_thread(path.read_text, encoding="utf-8"))
            await asyncio.sleep(max(3, settings.min_interval_seconds))
            result = CatalogResponse.model_validate(
                await page.evaluate(
                    """async url => {
                        const response = await fetch(url, {credentials: 'include'});
                        return {status: response.status, body: await response.text()};
                    }""",
                    CATALOG_URL + suffix,
                )
            )
            status, body = result.status, result.body
            if status == 429:
                raise RateLimited("Catalog capture stopped at HTTP 429; resume later from the same directory.")
            if status in {401, 403} or any(
                marker in body.casefold()
                for marker in ("radware bot manager", "shieldsquare", "verifying your browser")
            ):
                raise ChallengeRequired("Catalog capture requires sign-in or browser verification; no retries made.")
            if status != 200:
                raise PageUnavailable(f"Catalog request returned HTTP {status}")
            try:
                payload = JSON.validate_json(body)
            except ValueError as exc:
                raise ParseError("Catalog endpoint returned non-JSON content") from exc
            if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
                raise ParseError("Catalog endpoint did not return a data array")
            await asyncio.to_thread(
                path.write_text, json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            await browser._save_state()
            return payload

        products = await fetch(directory / "catalog.json", "")
        for identifier in product_ids(products):
            await fetch(directory / "attributes" / f"{identifier}.json", f"/{identifier}")
            print(f"Captured attributes: {identifier}", flush=True)
        await browser._save_state()
    finally:
        await browser.close()


def generate(directory: Path) -> None:
    source = directory / "catalog.json"
    products = JSON.validate_json(source.read_text("utf-8"))
    attributes = {
        identifier: JSON.validate_json((directory / "attributes" / f"{identifier}.json").read_text("utf-8"))
        for identifier in product_ids(products)
    }
    snapshot = canonical_snapshot(
        parse_catalog(products, attributes, fetched_at=datetime.fromtimestamp(source.stat().st_mtime, UTC))
    )
    enums, options = render_enums(snapshot)
    # Validate all generated source before changing the checked-in snapshot.
    compile(enums, "enums.py", "exec")
    compile(options, "options.py", "exec")
    sources: dict[str, str] = {}
    for filename, content in (("enums.py", enums), ("options.py", options)):
        formatted = subprocess.run(
            ["uv", "run", "ruff", "format", "--stdin-filename", filename, "-"],
            input=content,
            text=True,
            capture_output=True,
            check=True,
            cwd=ROOT,
        ).stdout
        sources[filename] = formatted
    for filename, content in sources.items():
        (DESTINATION / filename).write_text(content, encoding="utf-8")
    (DESTINATION / "data").mkdir(exist_ok=True)
    (DESTINATION / "data/catalog.json").write_text(snapshot.model_dump_json(indent=2) + "\n", encoding="utf-8")
    print(f"Generated {len(snapshot.products)} product types and their attributes.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture-dir", type=Path, default=Settings.from_env().data_dir / "catalog-capture")
    parser.add_argument("--fetch", action="store_true", help="Fetch missing metadata with a saved browser session")
    parser.add_argument("--login", action="store_true", help="Wait for interactive sign-in before fetching")
    args = parser.parse_args()
    if args.fetch or args.login:
        asyncio.run(capture(args.capture_dir, login=args.login))
    generate(args.capture_dir)


if __name__ == "__main__":
    main()
