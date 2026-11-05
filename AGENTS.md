# Working on Yad2

- Use `uv sync --locked --all-packages` and `uv run`; Python 3.11+.
- `packages/yad2-client` owns URLs, models, pure parsers, browser transport, cache and pacing.
- `packages/yad2-agent` owns CLI and MCP orchestration. Keep dependency direction agent → client.
- Use absolute imports, annotated functions, Pydantic public models, domain exceptions, and Ruff's
  120-column formatting. Avoid `Any` and blanket ignores.
- Distinguish a recognized empty feed from schema drift, missing items, and bot challenges.
- Parse only explicit feed groups; recommendations/filter options are not search results.
- Verify new URL filters/payload shapes against observed pages. Never infer IDs from unrelated sections.
- Keep browser sessions outside the repository. Do not commit research dumps, cookies, or personal data.
- Tests are offline by default. Run live tests only explicitly; no parallel browser navigation or retry loops.
- After changes: `uv run pytest`, `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy .`,
  and `uv build --all-packages`. Add behavioral regression tests where useful.
- Use focused conventional commits. Keep user work intact.
