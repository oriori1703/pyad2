"""Stable errors shared by the library, CLI and MCP server."""


class Yad2Error(Exception):
    """An expected Yad2 failure with an actionable message."""

    code = "yad2_error"


class ChallengeRequired(Yad2Error):
    """The browser was challenged; stop requests until the user intervenes."""

    code = "challenge_required"


class RateLimited(Yad2Error):
    code = "rate_limited"


class PageUnavailable(Yad2Error):
    code = "page_unavailable"


class ParseError(Yad2Error):
    code = "unsupported_page"


class BrowserError(Yad2Error):
    code = "browser_error"
