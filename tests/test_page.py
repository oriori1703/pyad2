import pytest

from yad2_agent.page import inspect_page
from yad2_client.models import PageSnapshot


def test_large_values_are_navigable_not_truncated_json(seeded_cache: PageSnapshot) -> None:
    root = inspect_page(seeded_cache, max_chars=10)
    assert root.omitted and root.value is None and root.keys == ("props",)
    nested = inspect_page(seeded_cache, "props.pageProps.initialFeed.items.0.title")
    assert nested.value == "כרטיס מסך RTX"
    assert not nested.omitted
    with pytest.raises(ValueError, match="No data at path"):
        inspect_page(seeded_cache, "props.pageProps.initialFeed.items.100")
