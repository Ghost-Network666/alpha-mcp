# Alpha MCP

**Make Polymarket fully native inside Hermes, OpenClaw, Claude Desktop, Cursor, and other agent harnesses.**

Full Gamma discovery + CLOB trading + gasless relayer + managed real-time WebSockets (Market/User/Sports) + paper trading simulation + strategy cookbooks. 100+ high-level tools. Zero static long-form docs in the repo — everything is delivered live through callable tools.

## Installation (recommended for agents)

```bash
# Core (recommended for most users — CLOB trading + most gasless features)
pip install git+https://github.com/Ghost-Network666/alpha-mcp.git

# With advanced gasless/relayer features (Safe deployment, low-level batches)
pip install "git+https://github.com/Ghost-Network666/alpha-mcp.git[gasless]"

# Development
git clone https://github.com/Ghost-Network666/alpha-mcp.git
cd alpha-mcp
pip install -e ".[gasless]"
```

## Hermes Setup (primary target)

Add this to `~/.hermes/config.yaml` (use absolute paths and the official variable names):

```yaml
mcp_servers:
  polymarket:
    command: python
    args: ["-m", "polymarket_alpha"]
    cwd: "/absolute/path/to/Alpha MCP"
    env:
      PK: "${PK}"
      CLOB_API_KEY: "${CLOB_API_KEY}"
      CLOB_SECRET: "${CLOB_SECRET}"
      CLOB_PASS_PHRASE: "${CLOB_PASS_PHRASE}"
      # FUNDER: "0xYourDepositWallet"   # Required for most serious users (signature_type=3)
```

**Best practice**: Put real secrets in `~/.hermes/.env` and reference them with `${VAR}` (Hermes supports this substitution).

**Critical for most users (signature_type=3 / Deposit wallets)**:
- `FUNDER` must be the deposit wallet address shown at polymarket.com → Profile → Wallet (NOT your EOA).
- You **must** first log into the official UI with the owner EOA, fund the deposit wallet with pUSD, and place at least one manual order via the website. This activates the wallet for API use.
- Without the UI step you will hit "maker address not allowed" or signer mismatch errors.

After adding/restarting Hermes, the very first calls inside your agent **must** be:

1. `get_mcp_health_report()`
2. `check_clob_auth(include_raw=true)`   ← mandatory before any trading (it will surface the exact sig=3 warnings)

Then use only the high-level exposed tools.

## First Actions (every session)

Call these native tools immediately:

- `get_mcp_health_report(include_detailed=true)`
- `get_capabilities()`
- `get_polymarket_llms_txt()` ← **primary source for all official Polymarket documentation**
- `list_polymarket_docs()` + `get_polymarket_doc(path="trading/gasless.md")` etc. for full .md content
- `polymarket_alpha_setup_guide(platform="hermes")` for the exact current config block

## Key Capabilities

- **Gamma discovery** — rich search, events, tags, full `get_clob_token_ids` bridge (handles the stringified JSON foot-gun)
- **Managed realtime** — `start_full_realtime_session`, `watch_*`, `listen_for_ws_events`, sports channel, auto-reconnect
- **Gasless** — full relayer support (split/merge/redeem/approvals) + `gasless_prepare_for_trading`
- **Simulation** — safe paper trading + live book impact + WS replay for strategy development
- **Self-documenting** — `get_polymarket_llms_txt`, `list_polymarket_docs`, `get_polymarket_doc`, `get_gamma_docs`, `get_clob_docs`, cookbooks

All documentation for agents lives inside the tools (never stale root .md files).

## Official Docs via Native Tools

```python
# Inside your agent
docs_index = list_polymarket_docs()
content = get_polymarket_doc(path="trading/gasless.md")           # full
neg_risk = get_polymarket_doc(path="advanced/neg-risk.md", summarize=True)
```

## License

MIT

---

**Never commit real credentials.** Use the `.env.example` template + `~/.hermes/.env` + `${VAR}` references.
