import json

from typer.testing import CliRunner

from yad2_agent.cli import app
from yad2_client import Settings
from yad2_client.models import PageSnapshot


def test_search_json_from_cache(seeded_cache: PageSnapshot) -> None:
    result = CliRunner().invoke(app, ["search", "RTX"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["cached"] is True
    assert payload["listings"][0]["title"] == "כרטיס מסך RTX"
    assert result.stderr == ""


def test_validation_errors_are_stderr_json(agent_settings: Settings) -> None:
    result = CliRunner().invoke(app, ["search", "RTX", "--min-price", "100", "--max-price", "10"])
    assert result.exit_code == 2
    assert not result.stdout
    assert json.loads(result.stderr)["error"]["code"] == "invalid_input"


def test_foreign_url_is_rejected_before_browser_start(agent_settings: Settings) -> None:
    result = CliRunner().invoke(app, ["get", "https://example.com/market/item/123"])
    assert result.exit_code == 2
    assert not result.stdout
    assert "www.yad2.co.il" in json.loads(result.stderr)["error"]["message"]


def test_help_lists_agent_commands() -> None:
    result = CliRunner().invoke(app, ["--help"])
    assert result.exit_code == 0
    for command in ("search-url", "get", "categories", "serve", "session", "install-browser"):
        assert command in result.stdout
