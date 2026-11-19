import json
from pathlib import Path

import anyio
import pytest
from typer.testing import CliRunner

from yad2_agent.cli import app
from yad2_client import AttributeFilter, MarketSearch, Settings
from yad2_client.cache import Cache
from yad2_client.extraction import extract_search
from yad2_client.models import PageSnapshot
from yad2_client.urls import market_url


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


def test_status_does_not_start_a_service_or_browser(agent_settings: Settings) -> None:
    result = CliRunner().invoke(app, ["status"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["service_state"] == "stopped"
    assert payload["browser_lock"] == {"locked": False, "owner": None}
    assert payload["saved_state_exists"] is False
    assert payload["cache"]["cooldown_remaining_seconds"] == 0
    assert not agent_settings.data_dir.exists()
    stopped = CliRunner().invoke(app, ["browser-stop"])
    assert stopped.exit_code == 0
    assert json.loads(stopped.stdout) == {"stopped": False}


def test_save_list_and_delete_watch_are_local_json_commands(agent_settings: Settings) -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["watch", "save", "gpu", "--search-json", '{"query":"RTX"}', "--max-pages", "2"])
    assert result.exit_code == 0, result.output
    result = runner.invoke(app, ["watch", "list"])
    assert result.exit_code == 0
    assert json.loads(result.stdout)["watches"][0]["definition"]["max_pages"] == 2
    result = runner.invoke(app, ["watch", "delete", "gpu"])
    assert json.loads(result.stdout) == {"name": "gpu", "deleted": True}
    assert not (agent_settings.data_dir / "browser-service.json").exists()


def test_bounded_search_from_cache(seeded_cache: PageSnapshot) -> None:
    result = CliRunner().invoke(app, ["search", "RTX", "--max-pages", "3"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["complete"] is True and len(payload["pages"]) == 1
    assert payload["pages"][0]["cached"] is True


def test_offline_catalog_commands_and_unknown_product_errors(agent_settings: Settings) -> None:
    products = CliRunner().invoke(app, ["product-types", "--query", "computer"])
    assert products.exit_code == 0, products.output
    assert "285" in {product["id"] for product in json.loads(products.stdout)["products"]}
    attributes = CliRunner().invoke(app, ["attributes", "COMPUTER_COMPONENTS"])
    assert attributes.exit_code == 0, attributes.output
    assert "155" in {attribute["id"] for attribute in json.loads(attributes.stdout)["attributes"]}
    unknown = CliRunner().invoke(app, ["attributes", "unknown"])
    assert unknown.exit_code == 2
    assert json.loads(unknown.stderr)["error"]["code"] == "invalid_input"
    assert not agent_settings.data_dir.exists()


def test_named_product_and_attribute_filters_hit_the_correct_cached_url(
    seeded_cache: PageSnapshot, agent_settings: Settings
) -> None:
    url = market_url(
        MarketSearch(
            query="RTX",
            product_types=("285",),
            attributes=(AttributeFilter(attribute_id="155", values=("13287",)),),
        )
    )
    anyio.run(
        Cache(agent_settings.cache_dir).put_payload,
        url,
        extract_search(seeded_cache.model_copy(update={"url": url})),
        backend="asyncio",
    )
    result = CliRunner().invoke(
        app, ["search", "RTX", "--product-type", "COMPUTER_COMPONENTS", "--attribute", "155=13287"]
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["cached"] is True
    invalid = CliRunner().invoke(app, ["search", "RTX", "--attribute", "155"])
    assert invalid.exit_code == 2
    assert json.loads(invalid.stderr)["error"]["code"] == "invalid_input"


def test_session_export_and_import_move_a_verified_session_between_data_dirs(
    agent_settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = CliRunner()
    bundle_path = tmp_path / "session.json"

    missing = runner.invoke(app, ["session", "export", str(bundle_path)])
    assert missing.exit_code == 3 and not bundle_path.exists()
    assert json.loads(missing.stderr)["error"]["code"] == "challenge_required"

    state = {
        "cookies": [
            {"name": "__uzma", "value": "bot-check", "domain": "www.yad2.co.il", "path": "/", "expires": -1}
            | {"secure": True, "httpOnly": True, "sameSite": "Lax"},
            {"name": "SID", "value": "google-login", "domain": ".google.com", "path": "/", "expires": -1}
            | {"secure": True, "httpOnly": True, "sameSite": "Lax"},
        ],
        "origins": [],
    }
    (agent_settings.data_dir / "browser-state.json").write_text(json.dumps(state))
    (agent_settings.data_dir / "http-identity.json").write_text(
        json.dumps({"user_agent": "test-browser", "scroll_session_id": "2026-11-05T10:00:00Z"})
    )

    exported = runner.invoke(app, ["session", "export", str(bundle_path)])
    assert exported.exit_code == 0, exported.output
    assert json.loads(exported.stdout) == {"exported": True, "path": str(bundle_path), "cookies": 1}
    assert "bot-check" not in exported.output and "google-login" not in bundle_path.read_text()
    assert bundle_path.stat().st_mode & 0o777 == 0o600

    piped = runner.invoke(app, ["session", "export", "-"])
    assert piped.exit_code == 0 and json.loads(piped.stdout) == json.loads(bundle_path.read_text()) | {
        "exported_at": json.loads(piped.stdout)["exported_at"]
    }

    target = tmp_path / "other-machine"
    monkeypatch.setenv("YAD2_DATA_DIR", str(target))
    imported = runner.invoke(app, ["session", "import", "-"], input=bundle_path.read_text())
    assert imported.exit_code == 0, imported.output
    assert json.loads(imported.stdout)["imported"] is True and "bot-check" not in imported.output
    assert json.loads((target / "browser-state.json").read_text())["cookies"][0]["value"] == "bot-check"
    status = json.loads(runner.invoke(app, ["status"]).stdout)
    assert status["saved_state_exists"] is True and status["http_identity_exists"] is True

    again = runner.invoke(app, ["session", "import", str(bundle_path)])
    assert again.exit_code == 2 and "--replace" in json.loads(again.stderr)["error"]["message"]
    assert runner.invoke(app, ["session", "import", str(bundle_path), "--replace"]).exit_code == 0

    bundle_path.write_text('{"cookies": "leak-me"}')
    invalid = runner.invoke(app, ["session", "import", str(bundle_path), "--replace"])
    assert invalid.exit_code == 2 and "leak-me" not in invalid.output
    assert json.loads(invalid.stderr)["error"]["code"] == "invalid_input"
