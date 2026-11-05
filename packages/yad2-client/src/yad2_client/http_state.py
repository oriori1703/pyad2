"""Browser-compatible cookies and HTTP identity, kept in the private session directory."""

from datetime import UTC, datetime
from http.cookiejar import Cookie
from typing import Literal

import httpx2
from pydantic import BaseModel, ConfigDict, Field, JsonValue

from yad2_client.models import Model


class HttpIdentity(Model):
    user_agent: str = Field(min_length=1)
    scroll_session_id: datetime = Field(default_factory=lambda: datetime.now(UTC))


class StoredCookie(BaseModel):
    name: str
    value: str
    domain: str
    path: str
    expires: float
    secure: bool
    httpOnly: bool
    sameSite: Literal["Strict", "Lax", "None"] = "Lax"


class BrowserState(BaseModel):
    model_config = ConfigDict(extra="allow")
    cookies: list[StoredCookie]
    origins: list[dict[str, JsonValue]] = Field(default_factory=list)

    def cookie_jar(self) -> httpx2.Cookies:
        jar = httpx2.Cookies()
        for saved in self.cookies:
            jar.jar.set_cookie(
                Cookie(
                    version=0,
                    name=saved.name,
                    value=saved.value,
                    port=None,
                    port_specified=False,
                    domain=saved.domain,
                    domain_specified=saved.domain.startswith("."),
                    domain_initial_dot=saved.domain.startswith("."),
                    path=saved.path,
                    path_specified=True,
                    secure=saved.secure,
                    expires=int(saved.expires) if saved.expires >= 0 else None,
                    discard=saved.expires < 0,
                    comment=None,
                    comment_url=None,
                    rest={"SameSite": saved.sameSite, **({"HttpOnly": ""} if saved.httpOnly else {})},
                    rfc2109=False,
                )
            )
        jar.jar.clear_expired_cookies()
        return jar

    def with_cookies(self, jar: httpx2.Cookies) -> "BrowserState":
        jar.jar.clear_expired_cookies()
        cookies = [
            StoredCookie(
                name=cookie.name,
                value=cookie.value or "",
                domain=cookie.domain,
                path=cookie.path,
                expires=cookie.expires if cookie.expires is not None else -1,
                secure=cookie.secure,
                httpOnly=cookie.has_nonstandard_attr("HttpOnly"),
                sameSite=(
                    "Strict"
                    if (cookie.get_nonstandard_attr("SameSite", "") or "").lower() == "strict"
                    else "None"
                    if (cookie.get_nonstandard_attr("SameSite", "") or "").lower() == "none"
                    else "Lax"
                ),
            )
            for cookie in jar.jar
        ]
        return self.model_copy(update={"cookies": cookies})
