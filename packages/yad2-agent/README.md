# yad2-agent

Typer CLI (`yad2`) and MCP server (`yad2-mcp`) backed by `yad2-client`.
MCP defaults to stdio. Use `yad2 serve --transport http` or `yad2-mcp --transport http` for Streamable HTTP
at `http://127.0.0.1:8000/mcp`; `--host` and `--port` configure the listener. Both transports expose the same tools.
The CLI emits JSON; MCP tools expose structured results and output schemas. Both share the client's
cache, HTTP/browser session, validation, and cooldown behavior. Marketplace reads use HTTPX2 by default;
`yad2 session` initializes or repairs verification, and `YAD2_TRANSPORT=browser` forces browser reads.
`yad2 status` reports local service ownership and cooldown; `yad2 browser-stop` stops an idle service.

See the workspace README for commands and the MCP configuration example.
