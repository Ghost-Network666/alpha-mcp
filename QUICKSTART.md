# Quickstart — polymarket-alpha for Hermes & OpenClaw

## 1. Folder Location
Keep this entire folder at:
`C:\Users\<yourname>\Desktop\Alpha MCP`

## 2. Hermes Setup (Recommended)

Add to `~/.hermes/.env`:
```env
POLYMARKET_PRIVATE_KEY=0x...
# For gasless (Builder Program) - fully supported:
RELAYER_API_KEY=...
RELAYER_API_KEY_ADDRESS=0x...
# Optional builder secrets
RELAYER_API_SECRET=...
RELAYER_API_PASSPHRASE=...
POLY_SIGNATURE_TYPE=3   # optional - defaults to 3 (Deposit). Set only if using 1 or 2.
# Optional
BUILDER_CODE=your_builder_code
```

Add to `~/.hermes/config.yaml`:
```yaml
mcp_servers:
  polymarket-alpha:
    command: python
    args: ["-m", "polymarket_alpha"]
    cwd: "C:\\Users\\<yourname>\\Desktop\\Alpha MCP"
    env:
      POLYMARKET_PRIVATE_KEY: ${POLYMARKET_PRIVATE_KEY}
      RELAYER_API_KEY: ${RELAYER_API_KEY}
      RELAYER_API_KEY_ADDRESS: ${RELAYER_API_KEY_ADDRESS}
      # POLY_SIGNATURE_TYPE optional (defaults to 3 = Deposit wallets)
      POLY_SIGNATURE_TYPE: "3"
```

Then run Hermes as normal. The agent will see all Polymarket tools.

## 3. OpenClaw Setup

Use the `openclaw mcp set` command or equivalent config, pointing `cwd` to the Alpha MCP folder and injecting the same environment variables.

## 4. First Actions for the Agent

1. Call `get_capabilities()`
2. If trading is desired, call `polymarket_alpha_setup_guide(platform="hermes")`
3. Use `polymarket_route_task("I want to bet on the election but check liquidity first")`

The MCP will guide you from there.
