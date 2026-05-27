"""
Mandatory meta tools that make this MCP the single source of truth.

Agents (Hermes, OpenClaw, etc.) should start here.

Documentation Philosophy (as of this version):
- NO native static .MD files are generated or shipped by this MCP.
- All documentation is provided through lightweight native tools:
    - get_polymarket_llms_txt()   → Live, always-fresh official Polymarket documentation
    - get_gamma_docs()            → MCP-specific Gamma guidance (categories, parameters, workflows)
    - get_clob_docs()             → MCP-specific CLOB guidance + routing between layers
- This approach stays up-to-date automatically and is preferred over static Markdown.
"""

from typing import Any, Literal, Optional

import httpx
from fastmcp import FastMCP
from pydantic import BaseModel, Field

from .config import get_auth_status


class ToolManifest(BaseModel):
    name: str
    requires_auth: bool
    description: str
    when_to_use: str
    example: str = ""


class CapabilitiesResponse(BaseModel):
    server: str = "polymarket-alpha"
    version: str = "0.3.1"
    auth_status: dict
    tools: list[ToolManifest]
    recommended_workflows: list[dict]
    important_notes: list[str]


class RouteResponse(BaseModel):
    query: str
    recommended_sequence: list[dict]
    warnings: list[str] = Field(default_factory=list)
    next_step_hint: str = ""


def register_meta_tools(mcp: FastMCP) -> None:

    @mcp.tool
    def get_capabilities() -> CapabilitiesResponse:
        """
        THE SINGLE SOURCE OF TRUTH for this Polymarket MCP.

        Call this first. It tells you exactly what is available, what requires
        authentication, current auth status, and recommended workflows.

        Returns a complete manifest so agents never need to look up external Polymarket documentation.
        """
        status = get_auth_status()

        tools = [
            ToolManifest(name="search_markets", requires_auth=False, description="Search markets by keyword", when_to_use="Discover markets on any topic"),
            ToolManifest(name="get_market_details", requires_auth=False, description="Rich market data + clobTokenIds", when_to_use="Get full details after search"),
            ToolManifest(name="get_orderbook", requires_auth=False, description="Live order book depth", when_to_use="Check liquidity before trading"),
            ToolManifest(name="get_price", requires_auth=False, description="Best bid/ask/midpoint", when_to_use="Quick price check"),
            ToolManifest(name="get_price_history", requires_auth=False, description="Historical candles", when_to_use="Analyze price movement"),
            ToolManifest(name="get_recent_trades", requires_auth=False, description="Recent executed trades", when_to_use="See real trading activity"),
            ToolManifest(name="get_gamma_docs", requires_auth=False, description="MCP-specific Gamma docs (categories, parameters, workflows + routing)", when_to_use="Get structured guidance on how to use Gamma tools effectively"),
            ToolManifest(name="get_clob_docs", requires_auth=False, description="MCP-specific CLOB docs (public + auth endpoints, parameters, Gamma↔CLOB routing)", when_to_use="Understand trading flows and how CLOB relates to Gamma"),
            ToolManifest(name="get_polymarket_llms_txt", requires_auth=False, description="Live fetch of official https://docs.polymarket.com/llms.txt (always fresh). Best for up-to-date official reference.", when_to_use="Get the real official Polymarket documentation (recommended first stop)"),
            ToolManifest(name="calculate_implied_probability", requires_auth=False, description="Price → probability", when_to_use="Think in probabilities not prices"),
            ToolManifest(name="liquidity_analysis", requires_auth=False, description="Slippage estimates for different sizes", when_to_use="Before any meaningful trade"),
            ToolManifest(name="risk_check", requires_auth=False, description="Pre-trade risk assessment", when_to_use="Strongly recommended before placing orders"),
            ToolManifest(name="get_balance", requires_auth=True, description="USDC balance", when_to_use="Check buying power"),
            ToolManifest(name="get_positions", requires_auth=True, description="Rich aggregated positions + value + PnL (redeemable/mergeable flags)", when_to_use="Review portfolio"),
            ToolManifest(name="place_limit_order", requires_auth=True, description="Place resting limit order", when_to_use="Patient entries/exits"),
            ToolManifest(name="place_market_order", requires_auth=True, description="Immediate market order", when_to_use="Urgent execution"),
            ToolManifest(name="gasless_status", requires_auth=True, description="Check gasless relayer readiness + wallet addresses", when_to_use="Before any gasless on-chain action"),
            ToolManifest(name="gasless_wallet_info", requires_auth=True, description="Derived proxy/safe/deposit addresses for your key", when_to_use="Confirm which wallet address you control"),
            ToolManifest(name="gasless_approve_all", requires_auth=True, description="Gasless set all required USDC/CTF approvals (required first step)", when_to_use="Before first gasless redeem/split/merge"),
            ToolManifest(name="gasless_redeem", requires_auth=True, description="Gasless redeem winning positions to pUSD (post-resolution)", when_to_use="Claim winnings without paying gas"),
            ToolManifest(name="gasless_split", requires_auth=True, description="Gasless split pUSD into Yes+No tokens", when_to_use="Create positions on-chain gaslessly"),
            ToolManifest(name="gasless_merge", requires_auth=True, description="Gasless merge Yes+No back to pUSD", when_to_use="Exit positions on-chain gaslessly"),
            ToolManifest(name="gasless_convert_no_tokens", requires_auth=True, description="Gasless convert No tokens in neg-risk events", when_to_use="Capital-efficient conversion"),
            ToolManifest(name="gasless_deploy_safe_wallet", requires_auth=True, description="Deploy Safe proxy via relayer (signature_type=2). Default is now 3 (Deposit)", when_to_use="One-time setup for Safe gasless wallets"),
            ToolManifest(name="gasless_get_balances", requires_auth=True, description="On-chain pUSD + POL balances for your gasless wallet", when_to_use="Check real balances when using gasless mode"),
            ToolManifest(name="gasless_get_pusd_balance", requires_auth=True, description="pUSD balance on active gasless wallet", when_to_use="Quick collateral check"),
            ToolManifest(name="gasless_get_token_balance", requires_auth=True, description="Specific outcome token balance on gasless wallet", when_to_use="Check position size on-chain"),
            ToolManifest(name="gasless_transfer_pusd", requires_auth=True, description="Gasless pUSD transfer from your proxy/safe/deposit wallet", when_to_use="Move collateral gaslessly"),
            ToolManifest(name="gasless_transfer_token", requires_auth=True, description="Gasless transfer of outcome tokens", when_to_use="Send positions to another wallet gaslessly"),
            ToolManifest(name="gasless_execute_custom", requires_auth=True, description="LOW-LEVEL: Send arbitrary gasless transactions (power tool)", when_to_use="Anything not covered by high-level gasless tools — use with extreme caution"),
            ToolManifest(name="gasless_approve_token", requires_auth=True, description="Convenience: Ensure approvals for a specific token", when_to_use="Quick targeted approval before trading/redeeming one market"),
            ToolManifest(name="gasless_batch_approve", requires_auth=True, description="Batch approve multiple tokens", when_to_use="Prepare many markets at once for gasless actions"),
            ToolManifest(name="gasless_redeem_all_redeemable", requires_auth=True, description="Automatically redeem every currently redeemable position (very high value)", when_to_use="One-call post-resolution claim of all winnings"),
        ]

        workflows = [
            {
                "name": "Research then Trade",
                "steps": ["search_markets", "get_market_details", "liquidity_analysis", "risk_check", "place_limit_order"]
            },
            {
                "name": "Gasless Redeem Winnings (post-resolution)",
                "steps": ["get_positions(redeemable_only=True)", "gasless_status", "gasless_approve_all", "gasless_redeem(condition_id, amounts, neg_risk)"]
            }
        ]

        notes = [
            "All tools are visible even in read-only mode.",
            "Tools marked requires_auth=True will fail gracefully with setup instructions if credentials are missing.",
            "HERMES USERS: The most important first action is to call polymarket_alpha_setup_guide(platform=\"hermes\") — it contains the EXACT text you must copy into ~/.hermes/config.yaml.",
            "Recommended docs workflow: Call get_polymarket_llms_txt() first (always fresh official source), then get_gamma_docs() + get_clob_docs() for MCP-specific parameters, categories, and routing. This is better than static .MD files.",
        ]

        return CapabilitiesResponse(
            auth_status=status.__dict__,
            tools=tools,
            recommended_workflows=workflows,
            important_notes=notes,
        )

    @mcp.tool
    def polymarket_route_task(query: str) -> RouteResponse:
        """
        Intelligent router. Give it a goal in natural language and it returns
        the optimal sequence of tool calls with suggested parameters.

        This is the primary way agents should discover how to accomplish tasks
        without guessing.
        """
        q = query.lower()
        sequence = []
        warnings = []

        if any(kw in q for kw in ["find", "search", "markets about"]):
            sequence.append({"tool": "search_markets", "args": {"query": query, "limit": 8}})
            sequence.append({"tool": "get_market_details", "args": {"slug": "<from search result>"}})

        if any(kw in q for kw in ["price", "odds", "trading at"]):
            sequence.append({"tool": "get_price", "args": {"token_id": "<token_id>"}})

        if any(kw in q for kw in ["buy", "bet", "trade", "enter position"]):
            sequence.extend([
                {"tool": "liquidity_analysis", "args": {"token_id": "<token_id>"}},
                {"tool": "risk_check", "args": {"proposed_size_usdc": 300}},
                {"tool": "place_limit_order", "args": {"token_id": "...", "side": "buy", "price": "...", "size": "..." }},
            ])
            warnings.append("Always run liquidity_analysis + risk_check before trading real size.")

        if not sequence:
            sequence.append({"tool": "get_capabilities", "args": {}})

        return RouteResponse(
            query=query,
            recommended_sequence=sequence,
            warnings=warnings,
            next_step_hint="Execute the first step, then re-evaluate if needed.",
        )

    @mcp.tool
    def polymarket_alpha_setup_guide(platform: Literal["hermes", "openclaw", "claude", "cursor", "ide", "manual"] = "hermes") -> str:
        """
        The definitive setup guide for this MCP.

        Call this when you need to enable trading or gasless features.
        Returns platform-specific, copy-paste ready instructions.
        """
        base = """
POLYMARKET ALPHA MCP — SETUP GUIDE

This MCP requires environment variables to enable trading and gasless features.

HERMES USERS: The agent MUST be given the exact configuration for ~/.hermes/config.yaml.
             Call this tool with platform="hermes" to get the precise copy-paste block.

PRIMARY METHOD (Recommended):
1. Get your wallet private key (L1).
2. Create a Relayer API key at https://polymarket.com/settings?tab=builder (for gasless).

FOR GASLESS TRANSACTIONS (Builder Program) - FULLY IMPLEMENTED:
- Join the Polymarket Builder Program
- Create a Relayer API Key (you will receive RELAYER_API_KEY + ADDRESS + SECRET + PASSPHRASE)
- POLY_SIGNATURE_TYPE now defaults to 3 (Deposit wallets)

After adding the keys, call: gasless_status() and gasless_wallet_info()

REQUIRED ENVIRONMENT VARIABLES:
"""

        if platform == "hermes":
            return base + """
═══════════════════════════════════════════════════════════════════
HERMES — EXACT CONFIGURATION INSTRUCTIONS
═══════════════════════════════════════════════════════════════════

When installing this MCP for Hermes, the agent MUST configure it correctly
in the user's Hermes config files.

───────────────────────────────────────────────────────────────────
STEP 1: Edit ~/.hermes/.env   (store the actual secret values here)
───────────────────────────────────────────────────────────────────

Add the following (replace with real values):

POLYMARKET_PRIVATE_KEY=0xYourFullPrivateKeyHere

# === Gasless / Builder Relayer Credentials ===
RELAYER_API_KEY=your_relayer_api_key
RELAYER_API_KEY_ADDRESS=0xAddressAssociatedWithTheRelayerKey

# Recommended for full gasless support (Builder Program)
RELAYER_API_SECRET=your_relayer_or_builder_secret
RELAYER_API_PASSPHRASE=your_relayer_or_builder_passphrase

# POLY_SIGNATURE_TYPE is OPTIONAL (defaults to 3 = Deposit wallets)
# Only set this line if you need to force a different wallet type:
# POLY_SIGNATURE_TYPE=3

# Optional
BUILDER_CODE=your_builder_code_if_any


───────────────────────────────────────────────────────────────────
STEP 2: Edit ~/.hermes/config.yaml   (tell Hermes how to run this MCP)
───────────────────────────────────────────────────────────────────

Add this exact block under `mcp_servers`:

mcp_servers:
  polymarket-alpha:
    command: python
    args: ["-m", "polymarket_alpha"]
    cwd: "C:\\\\Users\\\\YOUR_WINDOWS_USERNAME\\\\Desktop\\\\Alpha MCP"
    env:
      POLYMARKET_PRIVATE_KEY: ${POLYMARKET_PRIVATE_KEY}
      RELAYER_API_KEY: ${RELAYER_API_KEY}
      RELAYER_API_KEY_ADDRESS: ${RELAYER_API_KEY_ADDRESS}
      RELAYER_API_SECRET: ${RELAYER_API_SECRET}
      RELAYER_API_PASSPHRASE: ${RELAYER_API_PASSPHRASE}
      # Only uncomment the next line if you explicitly set POLY_SIGNATURE_TYPE above
      # POLY_SIGNATURE_TYPE: ${POLY_SIGNATURE_TYPE}

CRITICAL REQUIREMENTS:
- The "cwd" value MUST point to the folder containing this MCP on the user's machine.
- On Windows, you must use FOUR backslashes (\\\\\\\\) in the YAML path.
- After changing config.yaml, Hermes must be fully restarted.

───────────────────────────────────────────────────────────────────
STEP 3: Verify the setup worked
───────────────────────────────────────────────────────────────────

After restarting Hermes, the agent should immediately call:

1. get_capabilities()
2. polymarket_alpha_setup_guide(platform="hermes")
3. gasless_status()

If you see "Gasless Mode: ENABLED BY DEFAULT", the keys were loaded successfully.
"""
        elif platform == "claude":
            return base + """
═══════════════════════════════════════════════════════════════════
CLAUDE DESKTOP — LOCAL SETUP INSTRUCTIONS
═══════════════════════════════════════════════════════════════════

Claude Desktop does NOT automatically load .env files like Hermes does.
You must manually provide all environment variables in the config.

STEP 1: Locate your Claude Desktop config file

On Windows:
  %APPDATA%\\Claude\\claude_desktop_config.json

On macOS:
  ~/Library/Application Support/Claude/claude_desktop_config.json

STEP 2: Edit the config file

Add or merge the following:

{
  "mcpServers": {
    "polymarket-alpha": {
      "command": "C:\\\\Users\\\\YOUR_USERNAME\\\\AppData\\\\Local\\\\Programs\\\\Python\\\\Python312\\\\python.exe",
      "args": ["-m", "polymarket_alpha"],
      "cwd": "C:\\\\Users\\\\YOUR_USERNAME\\\\Desktop\\\\Alpha MCP",
      "env": {
        "POLYMARKET_PRIVATE_KEY": "0xYourFullPrivateKeyHere",
        "RELAYER_API_KEY": "your_relayer_key",
        "RELAYER_API_KEY_ADDRESS": "0x...",
        "RELAYER_API_SECRET": "your_secret",
        "RELAYER_API_PASSPHRASE": "your_passphrase",
        "POLY_SIGNATURE_TYPE": "3"
      }
    }
  }
}

IMPORTANT NOTES FOR CLAUDE DESKTOP:
- Replace the "command" with the FULL path to your Python executable (do not use "python").
- The "cwd" must point to the folder containing this MCP.
- All secrets must be written directly in the "env" object (no ${VAR} syntax like Hermes).
- After editing, fully restart Claude Desktop.
- For security, consider using a dedicated wallet for this MCP.

STEP 3: Verify

After restarting Claude, open a new chat and ask the agent to call:
- get_capabilities()
- gasless_status()

If it shows "Gasless Mode: ENABLED BY DEFAULT", it is working.

"""

        elif platform == "cursor":
            return base + """
═══════════════════════════════════════════════════════════════════
CURSOR (CODEX) — LOCAL SETUP INSTRUCTIONS
═══════════════════════════════════════════════════════════════════

Cursor supports MCP via project or global configuration.

Recommended: Create a file at the root of your project:
.cursor/mcp.json

Content:

{
  "mcpServers": {
    "polymarket-alpha": {
      "command": "python",
      "args": ["-m", "polymarket_alpha"],
      "cwd": "${workspaceFolder}",
      "env": {
        "POLYMARKET_PRIVATE_KEY": "0xYourFullPrivateKeyHere",
        "RELAYER_API_KEY": "your_relayer_key",
        "RELAYER_API_KEY_ADDRESS": "0x...",
        "RELAYER_API_SECRET": "your_secret",
        "RELAYER_API_PASSPHRASE": "your_passphrase",
        "POLY_SIGNATURE_TYPE": "3"
      }
    }
  }
}

Notes:
- Cursor can use "${workspaceFolder}" as cwd if the MCP folder is your project root.
- Otherwise use the full absolute path.
- You can also configure it globally in Cursor settings under "MCP".
- Restart Cursor after changing the config.

"""

        elif platform == "ide":
            return base + """
═══════════════════════════════════════════════════════════════════
LOCAL IDE / CODING AGENTS — CLEAN SETUP (Recommended)
═══════════════════════════════════════════════════════════════════

This path is intended for using the MCP inside local coding environments
(Claude Desktop, Cursor, Windsurf, Continue.dev, Claude Code, etc.).

We recommend treating this as a **clean, separate use case** from any experimental "Alpha" agent setups.

RECOMMENDED CLEAN SETUP:

1. Create a dedicated virtual environment:

   cd "C:\\Users\\YourName\\Desktop\\Alpha MCP"
   python -m venv .venv
   .venv\\Scripts\\activate
   pip install -e .

2. Configure your IDE's MCP settings using the full path to the venv Python.

Example for Cursor (.cursor/mcp.json):

{
  "mcpServers": {
    "polymarket": {
      "command": "C:\\\\Users\\\\YourName\\\\Desktop\\\\Alpha MCP\\\\.venv\\\\Scripts\\\\python.exe",
      "args": ["-m", "polymarket_alpha"],
      "cwd": "C:\\\\Users\\\\YourName\\\\Desktop\\\\Alpha MCP",
      "env": {
        "POLYMARKET_PRIVATE_KEY": "0xYourPrivateKey",
        "RELAYER_API_KEY": "...",
        "RELAYER_API_KEY_ADDRESS": "0x...",
        "RELAYER_API_SECRET": "...",
        "RELAYER_API_PASSPHRASE": "...",
        "POLY_SIGNATURE_TYPE": "3"
      }
    }
  }
}

Key points for local IDE use:
- Always use a virtual environment.
- Use the full absolute path to the Python executable inside the venv.
- Paste secrets directly in the config (most IDEs don't support ${VAR} substitution like Hermes).
- This keeps your local coding environment clean and isolated.

After setup, ask your coding agent to run:
- get_capabilities()
- gasless_status()

"""

        else:
            return base + """
For local coding environments (Claude Desktop, Cursor, Windsurf, etc.), use:
  polymarket_alpha_setup_guide(platform="ide")

This gives the cleanest recommended setup for development workflows.
"""

    @mcp.tool
    def get_polymarket_llms_txt(section: Optional[str] = None) -> dict:
        """
        Fetches the latest official Polymarket llms.txt directly from their docs site.

        This is the authoritative, always-fresh, LLM-optimized documentation source
        maintained by Polymarket.

        **This tool replaces the need for static .MD files** (this MCP ships no native .MD documentation):
        - It stays up-to-date automatically (Polymarket updates it regularly)
        - Agents get the real official source
        - Combine with get_gamma_docs() + get_clob_docs() for MCP-specific parameters, categories, and routing

        Use this when you need the most up-to-date official information about any part
        of Polymarket (Gamma, CLOB, gasless, trading, builders, etc.).

        Optionally filter by section (e.g. "gamma", "clob", "gasless", "trading", "relayer").
        """
        url = "https://docs.polymarket.com/llms.txt"

        try:
            with httpx.Client(timeout=15, follow_redirects=True) as client:
                resp = client.get(url)
                resp.raise_for_status()
                content = resp.text
        except Exception as e:
            return {
                "error": f"Failed to fetch llms.txt: {str(e)}",
                "url": url,
                "suggestion": "Call get_gamma_docs() or get_clob_docs() as fallback."
            }

        result = {
            "url": url,
            "fetched_at": "live (fresh on every call)",
            "full_content": content if not section else None,
        }

        if section:
            section_lower = section.lower().strip()
            lines = content.splitlines(keepends=True)
            filtered = []
            capture = False

            for line in lines:
                line_lower = line.lower()
                if line_lower.startswith("#") and section_lower in line_lower:
                    capture = True
                if capture:
                    filtered.append(line)
                    # Stop after a reasonable chunk if we hit another major header
                    if line.startswith("# ") and section_lower not in line_lower and len(filtered) > 8:
                        break

            result["section"] = section
            result["content"] = "".join(filtered) if filtered else content[:8000] + "\n... (truncated - section not found, showing beginning)"
            result["note"] = f"Filtered for section containing '{section}'"
        else:
            result["note"] = "Full official llms.txt returned. Pass section='gamma', 'clob', 'gasless', 'trading' etc. to filter."

        return result
