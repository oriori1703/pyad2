import json
from datetime import UTC, datetime
from pathlib import Path

import httpx2
import pytest
from filelock import FileLock

from yad2_client import MarketSearch, Settings
from yad2_client.errors import BrowserError, ChallengeRequired
from yad2_client.http import HttpTransport
from yad2_client.http_state import BrowserState, HttpIdentity, StoredCookie
from yad2_client.session import write_private
from yad2_client.session_bundle import SessionBundle, export_session, import_session, parse_bundle
from yad2_client.urls import market_url


def _cookie(name: str, domain: str, *, value: str = "secret-value", expires: float = -1) -> StoredCookie:
    return StoredCookie(name=name, value=value, domain=domain, path="/", expires=expires, secure=True, httpOnly=True)


async def _save(settings: Settings, cookies: list[StoredCookie]) -> None:
    await write_private(
        settings.data_dir / "browser-state.json",
        BrowserState(
            cookies=cookies,
            origins=[{"origin": "https://www.yad2.co.il", "localStorage": [{"name": "token", "value": "local"}]}],
        ),
    )


def _target(tmp_path: Path) -> Settings:
    return Settings(data_dir=tmp_path / "target", cache_dir=tmp_path / "target-cache", min_interval_seconds=0)


async def test_export_keeps_only_current_yad2_cookies_without_account_tokens(http_settings: Settings) -> None:
    await _save(
        http_settings,
        [
            _cookie("__uzma", "www.yad2.co.il"),
            _cookie("__uzmb", "gw.yad2.co.il"),
            _cookie("guest_token", ".yad2.co.il"),
            _cookie("refresh_token", ".yad2.co.il"),
            _cookie("access_token", ".yad2.co.il"),
            _cookie("expired", ".yad2.co.il", expires=1),
            _cookie("SID", ".google.com"),
            _cookie("LSID", "accounts.google.com"),
            _cookie("lookalike", "notyad2.co.il"),
            _cookie("suffix", "yad2.co.il.example.com"),
        ],
    )

    bundle = await export_session(http_settings)

    assert [(cookie.name, cookie.domain) for cookie in bundle.cookies] == [
        ("__uzma", "www.yad2.co.il"),
        ("__uzmb", "gw.yad2.co.il"),
        ("guest_token", ".yad2.co.il"),
    ]
    assert bundle.identity.user_agent == "test-browser"
    assert "local" not in bundle.model_dump_json() and "origins" not in bundle.model_dump_json()
    assert not (http_settings.data_dir / "browser-owner.json").exists()


async def test_export_requires_a_complete_session_with_yad2_cookies(http_settings: Settings, tmp_path: Path) -> None:
    with pytest.raises(ChallengeRequired, match="yad2 session"):
        await export_session(_target(tmp_path))

    await _save(http_settings, [_cookie("SID", ".google.com")])
    with pytest.raises(ChallengeRequired, match="no current Yad2 cookies"):
        await export_session(http_settings)

    (http_settings.data_dir / "http-identity.json").unlink()
    with pytest.raises(ChallengeRequired, match="No complete saved session"):
        await export_session(http_settings)


async def test_imported_session_is_private_and_used_by_http_reads(http_settings: Settings, tmp_path: Path) -> None:
    target = _target(tmp_path)
    bundle = parse_bundle((await export_session(http_settings)).model_dump_json())

    await import_session(target, bundle)

    for name in ("browser-state.json", "http-identity.json"):
        assert (target.data_dir / name).stat().st_mode & 0o777 == 0o600
    imported = HttpIdentity.model_validate_json((target.data_dir / "http-identity.json").read_text())
    assert imported == bundle.identity

    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(200, json={"data": {"items": [], "totalItems": 0, "totalPages": 0, "currentPage": 1}})

    transport = HttpTransport(target, transport=httpx2.MockTransport(handler))
    await transport.fetch_search(market_url(MarketSearch(query="RTX")))
    assert seen[0].headers["user-agent"] == "test-browser"
    assert "verified=original" in seen[0].headers["cookie"]


async def test_import_keeps_an_existing_session_unless_replace_is_set(http_settings: Settings, tmp_path: Path) -> None:
    bundle = SessionBundle(
        exported_at=datetime.now(UTC),
        identity=HttpIdentity(user_agent="imported-browser"),
        cookies=(_cookie("imported", ".yad2.co.il"),),
    )
    before = (http_settings.data_dir / "browser-state.json").read_text()

    with pytest.raises(BrowserError, match="--replace"):
        await import_session(http_settings, bundle)
    assert (http_settings.data_dir / "browser-state.json").read_text() == before

    await import_session(http_settings, bundle, replace=True)
    state = BrowserState.model_validate_json((http_settings.data_dir / "browser-state.json").read_text())
    assert [cookie.name for cookie in state.cookies] == ["imported"] and state.origins == []
    identity = HttpIdentity.model_validate_json((http_settings.data_dir / "http-identity.json").read_text())
    assert identity.user_agent == "imported-browser"


async def test_busy_session_is_neither_exported_nor_replaced(http_settings: Settings) -> None:
    bundle = await export_session(http_settings)
    before = (http_settings.data_dir / "browser-state.json").read_text()

    with FileLock(http_settings.data_dir / "browser.lock"):
        with pytest.raises(BrowserError, match="in use"):
            await export_session(http_settings)
        with pytest.raises(BrowserError, match="in use"):
            await import_session(http_settings, bundle, replace=True)

    assert (http_settings.data_dir / "browser-state.json").read_text() == before


async def test_invalid_bundles_are_rejected_without_echoing_cookie_values(http_settings: Settings) -> None:
    exported = json.loads((await export_session(http_settings)).model_dump_json())

    foreign = {**exported, "cookies": [{**exported["cookies"][0], "domain": ".google.com", "value": "do-not-print"}]}
    with pytest.raises(ValueError, match="Not a valid Yad2 session bundle") as rejected:
        parse_bundle(json.dumps(foreign))
    assert "do-not-print" not in str(rejected.value)

    for broken in ({**exported, "version": 2}, {**exported, "cookies": []}, {**exported, "unexpected": True}):
        with pytest.raises(ValueError, match="Not a valid Yad2 session bundle"):
            parse_bundle(json.dumps(broken))
    with pytest.raises(ValueError, match="Not a valid Yad2 session bundle") as malformed:
        parse_bundle('{"cookies": "do-not-print"')
    assert "do-not-print" not in str(malformed.value)
