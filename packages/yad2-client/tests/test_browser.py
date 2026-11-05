from unittest.mock import AsyncMock, Mock, patch

import pytest
from patchright.async_api import TimeoutError as PlaywrightTimeout

from yad2_client import Settings
from yad2_client.browser import BrowserTransport, retry_after_seconds
from yad2_client.errors import ChallengeRequired, PageUnavailable, RateLimited


@pytest.mark.parametrize(
    "status,error", [(403, ChallengeRequired), (404, PageUnavailable), (429, RateLimited), (503, PageUnavailable)]
)
async def test_http_errors_are_not_parsed(settings: Settings, status: int, error: type[Exception]) -> None:
    page = Mock()
    page.url = "https://www.yad2.co.il/market/search"
    page.goto = AsyncMock(return_value=Mock(status=status, headers={"retry-after": "600"}))
    transport = BrowserTransport(settings)
    with patch.object(transport, "_start", AsyncMock(return_value=page)), pytest.raises(error):
        await transport.fetch(page.url)
    if status == 429:
        assert transport.retry_after == 600


async def test_http_200_challenge_is_not_empty_search(settings: Settings) -> None:
    page = Mock()
    page.url = "https://www.yad2.co.il/market/search"
    page.goto = AsyncMock(return_value=Mock(status=200))
    page.wait_for_selector = AsyncMock(side_effect=PlaywrightTimeout("timeout"))
    page.title = AsyncMock(return_value="Radware Bot Manager Captcha")
    page.locator.return_value.inner_text = AsyncMock(return_value="Are you for real?")
    transport = BrowserTransport(settings)
    with patch.object(transport, "_start", AsyncMock(return_value=page)), pytest.raises(ChallengeRequired):
        await transport.fetch(page.url)


def test_retry_after_invalid_values() -> None:
    assert retry_after_seconds(None) == 0
    assert retry_after_seconds("invalid") == 0
    assert retry_after_seconds("120") == 120
    assert retry_after_seconds("-1") == 0
