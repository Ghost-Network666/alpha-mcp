# polymarket-alpha

**Polymarket, completely native inside Hermes, OpenClaw, and other agents.**

Full discovery, live pricing, portfolio, trading, and gasless transactions — with zero guessing.

This MCP is the single source of truth. Agents should rely on native tools instead of external or stale documentation:

- `get_polymarket_llms_txt()` — Fetches the latest official https://docs.polymarket.com/llms.txt (always fresh)
- `get_gamma_docs()` — MCP-specific Gamma guidance (parameters, categories, routing)
- `get_clob_docs()` — MCP-specific CLOB guidance + Gamma ↔ CLOB workflows

## Quick Start (for Agents & Users)

1. Place this folder at `C:\Users\<you>\Desktop\Alpha MCP`
2. Add a reference in your agent's config pointing to this folder with `cwd`.
3. Inject credentials via your agent's environment.

For platform-specific setup instructions, call:
`polymarket_alpha_setup_guide(platform="ide")`   # for Claude, Cursor, etc.
`polymarket_alpha_setup_guide(platform="hermes")`

For local coding environments (Claude Desktop, Cursor, Windsurf, etc.), run:
`polymarket_alpha_setup_guide(platform="ide")`

## Mandatory Tools (Start Here)

- `get_capabilities()` — Complete manifest + auth status
- `get_polymarket_llms_txt()` — Live official Polymarket documentation (recommended primary docs source)
- `get_gamma_docs()` + `get_clob_docs()` — Structured MCP-specific guidance
- `polymarket_route_task(query)` — "How do I do X?" — returns optimal tool sequence
- `polymarket_alpha_setup_guide(platform="hermes" | "ide" | "claude" | "cursor")` — Platform-specific setup

## Authentication Modes (Reported on Startup)

- **read-only** — All discovery & analysis tools work
- **clob-trading** — Private key present (basic trading)
- **gasless-enabled** — Relayer + Private key (recommended for most users)

All tools are visible at all times. Tools that require credentials will give clear guidance when called without them.

## Gasless / Builder Program Support

This version has **fully implemented** gasless relayer support (via polymarket-apis + builder-relayer-client).

Dedicated tools now exist:
- gasless_approve_all, gasless_redeem, gasless_split, gasless_merge, gasless_convert_no_tokens
- gasless_status + gasless_wallet_info (for Safe/proxy/deposit derivation)

Requires POLY_SIGNATURE_TYPE + RELAYER_API_KEY (see polymarket_alpha_setup_guide() + gasless_status()).
