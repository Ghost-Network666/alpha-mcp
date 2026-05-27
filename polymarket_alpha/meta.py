"""
Mandatory meta tools that make this MCP the single source of truth.

Agents (Hermes, OpenClaw, etc.) should start here.

Documentation Philosophy:
- This MCP uses **no static .md files**.
- Primary source: get_polymarket_llms_txt() → always fetches the fresh official https://docs.polymarket.com/llms.txt
- Supplementary: get_gamma_docs() and get_clob_docs() for MCP-specific parameters, categories, and routing guidance.
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
            ToolManifest(name="search_markets", requires_auth=False, description="Primary discovery tool. Search markets/events by keyword. Returns titles, slugs, ids, and basic pricing/volume data.", when_to_use="Start here for any topic. Always follow with get_market_details (using slug or token_id) to obtain clobTokenIds before any CLOB action."),
            ToolManifest(name="get_market_details", requires_auth=False, description="Get complete market metadata including the critical clobTokenIds needed for all trading. Accepts slug, market_id, or token_id.", when_to_use="Call immediately after search_markets or whenever you have a slug/token. This is the bridge from discovery to execution."),
            ToolManifest(name="get_orderbook", requires_auth=False, description="Full live order book (bids + asks) for a token_id. Essential for real liquidity assessment.", when_to_use="Check depth and realistic prices before placing any meaningful order."),
            ToolManifest(name="get_price", requires_auth=False, description="Best bid, best ask, and midpoint for a token_id.", when_to_use="Fast price snapshot."),
            ToolManifest(name="get_price_history", requires_auth=False, description="Historical OHLCV candles for a token_id (supports multiple intervals).", when_to_use="Analyze trends, volatility, or timing."),
            ToolManifest(name="get_recent_trades", requires_auth=False, description="Recent public trades for a token_id.", when_to_use="Gauge real market activity and typical trade sizes."),
            ToolManifest(name="get_active_markets", requires_auth=False, description="List currently active/tradable markets, filterable by tag.", when_to_use="Browse hot markets or markets in a specific category."),
            ToolManifest(name="get_events", requires_auth=False, description="List events (groups of related markets).", when_to_use="Understand multi-market structures and event-level data."),
            ToolManifest(name="get_gamma_docs", requires_auth=False, description="MCP-native reference: Gamma categories, key endpoints with parameters, recommended workflows, and precise Gamma → CLOB handoff guidance.", when_to_use="Read this when you need structured help on how to use the Gamma tools correctly and the exact sequence to trading."),
            ToolManifest(name="get_clob_docs", requires_auth=False, description="MCP-native reference: full CLOB surface (public + authenticated), auth model, parameter contracts, and strict 'Gamma first' routing.", when_to_use="Read before any trading or when you need to understand authenticated flows, order types, and common sequencing."),
            ToolManifest(name="get_polymarket_llms_txt", requires_auth=False, description="Live, always-fresh fetch of the official Polymarket llms.txt (https://docs.polymarket.com/llms.txt). Supports section filtering and lightweight summarization.", when_to_use="Primary source for the latest official Polymarket documentation. Use for broad reference; combine with the two MCP-specific docs tools for practical usage."),
            ToolManifest(name="get_midpoint", requires_auth=False, description="Midpoint price for a token_id.", when_to_use="Quick fair-value estimate."),
            ToolManifest(name="get_spread", requires_auth=False, description="Current bid-ask spread for a token_id.", when_to_use="Quick liquidity/tightness check."),
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

            # Missing public CLOB tools (added for completeness)
            ToolManifest(name="get_midpoint", requires_auth=False, description="Midpoint price for a token_id.", when_to_use="Quick fair-value estimate."),
            ToolManifest(name="get_spread", requires_auth=False, description="Current bid-ask spread for a token_id.", when_to_use="Quick liquidity/tightness check."),
            ToolManifest(name="get_active_markets", requires_auth=False, description="List currently active/tradable markets, optionally filtered by tag.", when_to_use="Browse hot markets or markets in a specific category."),
            ToolManifest(name="get_events", requires_auth=False, description="List events (groups of related markets).", when_to_use="Understand multi-market structures."),

            # Authenticated CLOB tools
            ToolManifest(name="get_open_orders", requires_auth=True, description="All currently resting limit orders for the wallet.", when_to_use="Monitor your active orders."),
            ToolManifest(name="get_fills", requires_auth=True, description="Recent fills/trades executed by this wallet.", when_to_use="Review your execution history."),
            ToolManifest(name="cancel_order", requires_auth=True, description="Cancel a single open order by ID.", when_to_use="Remove a specific unwanted resting order."),
            ToolManifest(name="cancel_all_orders", requires_auth=True, description="Cancel every open order for this wallet (use with caution).", when_to_use="Full reset of resting orders or emergency cleanup."),

            # Setup
            ToolManifest(name="polymarket_alpha_setup_guide", requires_auth=False, description="Platform-specific setup instructions with exact copy-paste config blocks for Hermes, OpenClaw, IDEs (Claude, Cursor, etc.).", when_to_use="When setting up the MCP in a new host agent."),
        ]

        workflows = [
            {
                "name": "Research then Trade",
                "steps": ["search_markets", "get_market_details", "liquidity_analysis", "risk_check", "place_limit_order"]
            },
            {
                "name": "Gasless Redeem Winnings (post-resolution)",
                "steps": ["get_positions(redeemable_only=True)", "gasless_status", "gasless_approve_all", "gasless_redeem(condition_id, amounts, neg_risk)"]
            },
            {
                "name": "Gasless On-Chain Position Management",
                "steps": ["get_polymarket_llms_txt(section='gasless')", "gasless_wallet_info", "gasless_approve_all", "gasless_split or gasless_merge"]
            }
        ]

        notes = [
            "All tools are visible even in read-only mode.",
            "Tools marked requires_auth=True will fail gracefully with setup instructions if credentials are missing.",
            "HERMES / OPENCLAW / IDE USERS: Call polymarket_alpha_setup_guide(platform=\"hermes\" | \"openclaw\" | \"ide\") for exact copy-paste configuration.",
            "Documentation Strategy (Native):",
            "  • Primary source: get_polymarket_llms_txt() — always-fresh official Polymarket documentation.",
            "  • Practical usage: get_gamma_docs() + get_clob_docs() — MCP-specific parameters, categories, workflows, and routing.",
            "  • This MCP ships no stale .md files. Call the three docs tools instead.",
            "Recommended first call when exploring: get_capabilities() → then the three docs tools as needed.",
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
    def polymarket_alpha_setup_guide(platform: Literal["hermes", "openclaw", "ide", "manual"] = "hermes") -> str:
        """
        Setup instructions for this MCP.

        For documentation, prefer the three native tools:
        - get_polymarket_llms_txt()   → Always-fresh official docs (https://docs.polymarket.com/llms.txt)
        - get_gamma_docs()            → Gamma parameters & categories
        - get_clob_docs()             → CLOB parameters & routing

        Minimal trading only requires POLYMARKET_PRIVATE_KEY.
        """
        base = """POLYMARKET ALPHA MCP — SETUP GUIDE

Call these for up-to-date information:
  get_polymarket_llms_txt()
  get_gamma_docs()
  get_clob_docs()

Minimal CLOB trading requires only POLYMARKET_PRIVATE_KEY.
"""

        if platform == "hermes":
            return base + """
HERMES (Minimal - Only POLYMARKET_PRIVATE_KEY)

Add to ~/.hermes/.env:
  POLYMARKET_PRIVATE_KEY=0xYourPrivateKeyHere

Add to ~/.hermes/config.yaml:
  mcp_servers:
    polymarket-alpha:
      command: python
      args: ["-m", "polymarket_alpha"]
      cwd: "C:\\\\Users\\\\YOUR_USERNAME\\\\Desktop\\\\Alpha MCP"
      env:
        POLYMARKET_PRIVATE_KEY: ${POLYMARKET_PRIVATE_KEY}

Restart Hermes, then call get_capabilities().
"""
        elif platform in ("claude", "cursor", "ide"):
            return base + """
LOCAL IDE SETUP (Claude Desktop, Cursor, Windsurf, etc.)

Use a venv + full path to python:

{
  "mcpServers": {
    "polymarket-alpha": {
      "command": "/full/path/to/.venv/bin/python",
      "args": ["-m", "polymarket_alpha"],
      "cwd": "/full/path/to/Alpha MCP",
      "env": {
        "POLYMARKET_PRIVATE_KEY": "0xYourPrivateKeyHere"
      }
    }
  }
}

Call get_capabilities() after restart.
"""
        else:
            return base + """
Use platform="hermes", "openclaw", or "ide".
Prefer get_polymarket_llms_txt(), get_gamma_docs(), and get_clob_docs() for documentation.
"""

    @mcp.tool
    def get_polymarket_llms_txt(section: Optional[str] = None, summarize: bool = False) -> dict:
        """
        Fetches the latest official Polymarket llms.txt from https://docs.polymarket.com/llms.txt (fresh on every call).

        WHEN TO USE: Primary / first source for any official Polymarket documentation (APIs, trading, gasless, etc.). Always call this before relying on get_gamma_docs / get_clob_docs or prior knowledge.

        Args:
          section: optional filter (e.g. "gamma", "clob", "trading", "gasless")
          summarize: True for lightweight condensed output (headings + key lines)

        RETURNS: dict {url, fetched_at, content|full_content, note, ...}
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

        if section or summarize:
            section_lower = (section or "").lower().strip()
            lines = content.splitlines(keepends=True)
            filtered = []
            capture = False
            heading_count = 0

            for line in lines:
                line_lower = line.lower()

                if section_lower and line_lower.startswith("#") and section_lower in line_lower:
                    capture = True
                    heading_count = 0

                if capture or not section_lower:
                    if summarize:
                        # Lightweight summarization: keep headings + first line after them
                        if line.strip().startswith("#"):
                            filtered.append(line)
                            heading_count = 0
                        elif heading_count < 1:
                            filtered.append(line)
                            heading_count += 1
                    else:
                        filtered.append(line)

                    if section_lower and line.startswith("# ") and section_lower not in line_lower and len(filtered) > 12:
                        break

            final_content = "".join(filtered) if filtered else content[:6000] + "\n... (truncated)"

            result["section"] = section
            result["summarize"] = summarize
            result["content"] = final_content
            result["note"] = f"{'Summarized ' if summarize else ''}content from official llms.txt"
            if section:
                result["note"] += f" (filtered for '{section}')"
        else:
            result["note"] = "Full official llms.txt returned. Use section=... and/or summarize=True for filtered/summarized views."

        return result
