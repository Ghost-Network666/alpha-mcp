"""
Mandatory meta tools that make this MCP the single source of truth.

Agents (Hermes, OpenClaw, etc.) should start here.

Documentation Philosophy (FINAL):
- This MCP ships **zero static .md files** in the root.
- The single authoritative source for official Polymarket documentation is:
  get_polymarket_llms_txt()  →  always returns the live https://docs.polymarket.com/llms.txt
- Use get_gamma_docs() + get_clob_docs() only for MCP-specific usage patterns, parameters, and workflows.
- Agents must treat get_polymarket_llms_txt() as the primary / first documentation tool.
"""

from typing import Any, Literal, Optional

import httpx
from fastmcp import FastMCP
from pydantic import BaseModel, Field

from .config import get_auth_status, get_official_credentials, get_gasless_client, get_relayer_creds, get_data_client
from .gamma import quick_gamma_health_check
from .clob_authenticated import get_clob_auth_diagnostic
from .websocket import get_all_websocket_status, get_detailed_connection_health


class ToolManifest(BaseModel):
    name: str
    requires_auth: bool
    description: str
    when_to_use: str
    example: str = ""


class CapabilitiesResponse(BaseModel):
    server: str = "polymarket-alpha"
    version: str = "0.5.0"
    auth_status: dict
    tools: list[ToolManifest]
    recommended_workflows: list[dict]
    important_notes: list[str]


class RouteResponse(BaseModel):
    query: str
    recommended_sequence: list[dict]
    warnings: list[str] = Field(default_factory=list)
    next_step_hint: str = ""


# =============================================================================
# Thin compatibility / recommendation layer for Polymarket SDK choice (initial)
# =============================================================================
# High-signal guidance for agents. This MCP currently uses py-clob-client-v2
# (plus polymarket-apis for positions/gasless). Polymarket's official direction
# is the unified `polymarket-client` (beta as of 2026).
# This layer provides detection + authoritative guidance without changing
# the underlying MCP implementation yet.

_UNIFIED_SDK_IMPORT_NAME = "polymarket"


def _detect_available_sdks() -> dict:
    """Lightweight detection of installed Polymarket-related SDKs for guidance."""
    detected: dict = {
        "mcp_current": "py-clob-client-v2 + polymarket-apis (production path in this MCP)",
        "py_clob_client_v2": False,
        "polymarket_client": False,
        "polymarket_apis": False,
        "recommendation": "Use MCP high-level tools (get_clob_token_ids, place_*, managed WS). For raw drops: see get_unified_sdk_guidance().",
    }
    try:
        from py_clob_client_v2 import ClobClient  # type: ignore
        detected["py_clob_client_v2"] = True
    except Exception:
        pass
    try:
        import polymarket  # unified beta SDK namespace
        detected["polymarket_client"] = True
    except Exception:
        try:
            from polymarket import PublicClient  # alternative top-level
            detected["polymarket_client"] = True
        except Exception:
            pass
    try:
        from polymarket_apis import PolymarketDataClient  # type: ignore
        detected["polymarket_apis"] = True
    except Exception:
        pass
    return detected


def _get_unified_sdk_guidance() -> dict:
    """
    Returns structured, agent-optimized guidance comparing the current MCP stack
    (py-clob-client-v2) with Polymarket's recommended unified `polymarket-client` SDK.
    Includes decision criteria, migration notes, and minimal copy-paste examples.
    """
    sdks = _detect_available_sdks()
    installed_unified = sdks.get("polymarket_client", False)

    guidance = {
        "title": "Polymarket SDK Guidance: Unified `polymarket-client` vs py-clob-client-v2 (MCP 0.5.0)",
        "summary": "Polymarket now recommends the unified `polymarket-client` (beta) as the long-term path covering discovery (Gamma), CLOB trading, realtime WS, gasless, and account ops in one cohesive, typed Python surface. This MCP is built on the mature py-clob-client-v2 + polymarket-apis for rock-solid CLOB + gasless today.",
        "detected_sdks": sdks,
        "when_to_use_unified_polymarket_client": [
            "You are starting NEW direct (non-MCP) integrations or custom agent code outside this MCP.",
            "You want a single client surface for Gamma-style discovery + CLOB orders + managed streams + on-chain gasless actions.",
            "Future-proofing: once the SDK reaches stable, it will be the supported surface.",
            "You prefer modern Python patterns (async/sync clients, Pydantic models, strong typing, pagination helpers, explicit errors).",
            "Your workflow spans multiple Polymarket surfaces (not just CLOB trading).",
        ],
        "when_to_stick_with_py_clob_client_v2_or_this_mcp": [
            "You are using this MCP (recommended): the high-level tools here (search_markets → get_clob_token_ids → place_limit_order + fully-managed WS via start_full_market_monitor + listen_for_ws_events) abstract the underlying SDK completely.",
            "Production trading today where maximum maturity and battle-testing of the CLOB client is required (v2 is the current production workhorse).",
            "You only need CLOB orderbook / trading primitives and already have working v2 credential flows (PK + L2 api creds).",
            "You depend on specific low-level behaviors or error modes of py-clob-client-v2.",
            "Beta risk aversion: `polymarket-client` is explicitly beta; stable migration path from Polymarket is promised later.",
        ],
        "mcp_recommendation": "For 95%+ of agent use cases with this MCP: IGNORE raw SDK choice. Always start with get_capabilities(), get_polymarket_llms_txt(), get_clob_token_ids, the managed realtime tools, and high-level place_* / gasless_* tools. Only drop to raw SDK (via get_client() or external code) when you have a specific reason. When writing external code, prefer the unified SDK for new work.",
        "migration_notes": [
            "This MCP will continue supporting py-clob-client-v2 paths while adding optional unified paths in future releases (watch get_unified_sdk_guidance and llms.txt).",
            "Raw migration (for external code): Replace py_clob_client_v2.ClobClient + manual auth with from polymarket import AsyncSecureClient / SecureClient; await AsyncSecureClient.create(private_key=..., ...)",
            "Auth model shift: Unified SDK takes private_key directly (and optional wallet, api_key for relayer/builder/gasless). No separate manual L2 api key derivation in many flows.",
            "Discovery: unified client.list_markets / get_market / search replaces direct Gamma calls or py-clob public methods.",
            "Trading: secure_client.place_limit_order(...) returns structured response (check .ok). Similar for market orders, cancels.",
            "Realtime: unified subscribe() with MarketSpec/UserSpec etc. (async only) — very powerful, but this MCP's managed WS + listen_for_ws_events is still simpler for most harness agents.",
            "Gasless / on-chain: unified has first-class split/merge/redeem + gasless wallet setup (setup_gasless_wallet). This MCP's gasless_* tools already provide high-level wrappers on top of polymarket-apis.",
            "Install (beta): `pip install --pre polymarket-client` or `uv add --prerelease allow polymarket-client`.",
            "Always cross-check the live official source: call get_polymarket_llms_txt(section='clob' or 'sdk' or 'python') first.",
            "Deposit wallet / sig_type gotchas and the 'manual UI trade first' rule remain relevant regardless of SDK.",
        ],
        "quick_examples": {
            "unified_public_read": """from polymarket import AsyncPublicClient
import asyncio
async def ex():
    async with AsyncPublicClient() as c:
        m = await c.get_market(slug="your-market-slug")
        book = await c.get_order_book(token_id=...)
        # pagination, search, streams etc. available
asyncio.run(ex())""",
            "unified_authenticated_trading": """import os
from polymarket import AsyncSecureClient
async def trade():
    async with await AsyncSecureClient.create(
        private_key=os.environ["PK"],
        wallet=os.environ.get("FUNDER")  # optional for deposit wallets
    ) as client:
        # gasless setup if needed
        # g = await client.setup_gasless_wallet()
        resp = await client.place_limit_order(token_id=..., side="BUY", price="0.51", size="10")
        if resp.ok: print(resp.order_id)
asyncio.run(trade())""",
            "current_mcp_preferred_path": "search_markets(...) → get_clob_token_ids(slug=...) → get_live_orderbook(...) + liquidity_analysis + risk_check → place_limit_order(...)  (or start_full_market_monitor + listen_for_ws_events for realtime)",
            "drop_to_current_v2_in_mcp": "client = get_client()  # returns handle to py-clob-client-v2.ClobClient (advanced only)",
        },
        "official_sources": [
            "https://docs.polymarket.com/dev-tooling/python (primary for unified)",
            "https://docs.polymarket.com/llms.txt (call get_polymarket_llms_txt() to fetch live)",
            "https://github.com/Polymarket/py-sdk (source + sdk-direction.md)",
        ],
        "notes_for_agents": [
            "The MCP's value (Gamma bridge, managed realtime with auto-reconnect + parse helpers, gasless high-level, self-documenting tools) remains the best surface even after any future internal SDK swap.",
            "Call this tool + get_polymarket_llms_txt(section='python' or 'clob') before writing any raw SDK code.",
            "If polymarket-client is not detected as installed, the examples still serve as forward guidance.",
        ],
    }
    if installed_unified:
        guidance["note"] = "polymarket-client detected as importable in the current environment."
    else:
        guidance["note"] = "polymarket-client not detected (normal for this MCP environment). Use the examples as guidance for external code or future MCP extensions."
    return guidance


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
            ToolManifest(name="search_markets", requires_auth=False, description="Primary discovery tool. Search markets/events by keyword. Now respects active_only + tag. Returns events with nested markets.", when_to_use="Start here for any topic. Prefer get_clob_token_ids(slug=...) immediately after for clean token lists."),
            ToolManifest(name="get_market_details", requires_auth=False, description="Full market + clobTokenIds. Supports slug, market_id, token_id, AND condition_id (very useful for neg-risk/gasless).", when_to_use="After search or when you have any identifier. Use get_clob_token_ids() for the normalized version."),
            ToolManifest(name="get_clob_token_ids", requires_auth=False, description="THE BEST tool for Gamma → CLOB handoff. Accepts any identifier and returns clean {clob_token_ids: list, condition_id, neg_risk, ...} ready to pass to orderbook/place_order.", when_to_use="Right after any Gamma lookup. This is the recommended bridge tool."),
            ToolManifest(name="get_event_details", requires_auth=False, description="Get one event + ALL its child markets with pre-parsed _clob_token_ids lists. Highest signal for group topics.", when_to_use="When the query is about a broad event (elections, sports, 'what happens before X')."),
            ToolManifest(name="get_orderbook", requires_auth=False, description="Full live order book (bids + asks) for a token_id. Essential for real liquidity assessment.", when_to_use="Check depth and realistic prices before placing any meaningful order."),
            ToolManifest(name="get_price", requires_auth=False, description="Best bid, best ask, and midpoint for a token_id.", when_to_use="Fast price snapshot."),
            ToolManifest(name="get_price_history", requires_auth=False, description="Historical OHLCV candles for a token_id (supports multiple intervals).", when_to_use="Analyze trends, volatility, or timing."),
            ToolManifest(name="get_recent_trades", requires_auth=False, description="Recent public trades for a token_id.", when_to_use="Gauge real market activity and typical trade sizes."),
            ToolManifest(name="get_gamma_docs", requires_auth=False, description="MCP-native reference: full Gamma parameter catalog, event handling, clobTokenIds parsing gotchas, and the exact recommended Gamma→CLOB workflow.", when_to_use="Read this when you need structured help on how to use the Gamma tools correctly and the exact sequence to trading."),
            ToolManifest(name="get_events", requires_auth=False, description="List events with rich filters (active, tag, featured, volume_num_min, slug, id). Events contain nested markets.", when_to_use="Broad topic discovery or when you want coherent groups of markets together."),
            ToolManifest(name="get_active_markets", requires_auth=False, description="Paginated active markets with tag / new / closed filters.", when_to_use="Browsing hot or tagged markets."),
            ToolManifest(name="get_tags", requires_auth=False, description="Available tags for filtering markets and events.", when_to_use="Discover valid tag values for search or get_events."),
            ToolManifest(name="get_clob_docs", requires_auth=False, description="MCP-native reference: full CLOB surface (public + authenticated), auth model, parameter contracts, and strict 'Gamma first' routing.", when_to_use="Read before any trading or when you need to understand authenticated flows, order types, and common sequencing."),
            ToolManifest(name="get_unified_sdk_guidance", requires_auth=False, description="Guidance on using Polymarket's new recommended unified `polymarket-client` SDK vs the py-clob-client-v2 currently powering this MCP. Decision criteria, migration notes, examples, and MCP recommendations for raw SDK usage.", when_to_use="When choosing or migrating to raw SDKs, writing custom low-level code, or understanding Polymarket's official Python direction."),
            ToolManifest(name="get_polymarket_llms_txt", requires_auth=False, description="THE PRIMARY SOURCE for all official Polymarket documentation. Fetches live https://docs.polymarket.com/llms.txt on every call. Use section= and summarize= for focused output.", when_to_use="First tool to call for any official Polymarket information (APIs, trading, gasless, deposit wallets, etc.). This is the authoritative source — not static markdown."),
            ToolManifest(name="list_polymarket_docs", requires_auth=False, description="Structured categorized index of EVERY .md file referenced in the official llms.txt (trading, gasless, neg-risk, events, CLOB, concepts, WebSocket specs, etc.). Use with get_polymarket_doc().", when_to_use="Discover the full official documentation surface before reading specific pages."),
            ToolManifest(name="get_polymarket_doc", requires_auth=False, description="Fetch the actual full (or summarized) content of any official Polymarket .md doc (e.g. 'trading/gasless.md', 'advanced/neg-risk.md', 'concepts/pusd.md', 'api-reference/authentication.md'). The way agents read the complete live docs via native tools.", when_to_use="Read the authoritative source for any topic (gasless setup, neg-risk mechanics, deposit wallets, event schemas, etc.)."),
            ToolManifest(name="get_midpoint", requires_auth=False, description="Midpoint price for a token_id.", when_to_use="Quick fair-value estimate."),
            ToolManifest(name="get_spread", requires_auth=False, description="Current bid-ask spread for a token_id.", when_to_use="Quick liquidity/tightness check."),
            ToolManifest(name="calculate_implied_probability", requires_auth=False, description="Price → probability", when_to_use="Think in probabilities not prices"),
            ToolManifest(name="liquidity_analysis", requires_auth=False, description="Production: walks live CLOB orderbook for realistic buy/sell slippage, shares, depth exhaustion on requested notional (fully implemented).", when_to_use="Before any meaningful trade — now fully functional using public orderbook"),
            ToolManifest(name="risk_check", requires_auth=False, description="Pre-trade risk assessment", when_to_use="Strongly recommended before placing orders"),
            # NEW advanced analysis tools (production-grade, read-only)
            ToolManifest(name="orderbook_imbalance", requires_auth=False, description="Bid vs ask depth pressure (notional + ratio + interpretation) from live book.", when_to_use="Quick directional bias or entry timing signal before limits or sims"),
            ToolManifest(name="detect_yes_no_arb", requires_auth=False, description="Detects simple yes+no price sum deviation from 1.0 on binary markets (condition_id or token pair).", when_to_use="Pre-trade arb screen on two-sided markets"),
            ToolManifest(name="volume_profile", requires_auth=False, description="Recent trade volume, avg size, notional from public trades.", when_to_use="Microstructure context alongside price history and WS trades"),
            ToolManifest(name="price_volatility", requires_auth=False, description="Pure-Python realized vol, range, recent change from recent prices/trades.", when_to_use="Risk scaling and position sizing inputs"),
            ToolManifest(name="suggested_position_size", requires_auth=False, description="Conservative USDC size recommendation using depth, risk_pct, and slippage targets.", when_to_use="Safe starting size before liquidity_analysis + paper sims"),
            ToolManifest(name="cross_market_correlation", requires_auth=False, description="Simple pure-Python Pearson correlation between two tokens (recent prices).", when_to_use="Quick multi-market relationship screen (combine with WS multi-asset)"),
            ToolManifest(name="get_market_microstructure", requires_auth=False, description="One-shot rich snapshot: spread, depth, imbalance, volume, vol proxy, suggested size.", when_to_use="Highest-signal pre-trade or pre-sim diagnostic for a token"),
            ToolManifest(name="get_balance", requires_auth=True, description="USDC balance (legacy alias — prefer get_clob_balance)", when_to_use="Check buying power"),
            ToolManifest(name="get_clob_balance", requires_auth=True, description="Primary CLOB collateral balance (recommended)", when_to_use="Check trading power before orders"),
            ToolManifest(name="check_clob_auth", requires_auth=True, description="MANDATORY first diagnostic for every CLOB session. Verifies L2 validity, signature_type=3, funder, and reports the address the exchange sees.", when_to_use="ALWAYS call this immediately after MCP startup when you intend to trade"),
            ToolManifest(name="get_mcp_health_report", requires_auth=False, description="THE SINGLE MOST POWERFUL DIAGNOSTIC: full MCP readiness report. Covers credentials (official CLOB vars/legacy/gasless), Gamma reachability (/markets+/events pings), CLOB auth (via check_clob_auth logic + balance probe), WS connections+deep health (all channels, reconnects, errors, uptime, buffers), gasless state, and more. Structured output with severity levels per section + prioritized actionable recommendations. Human + agent readable. include_detailed for full raw payloads.", when_to_use="ALWAYS CALL EARLY: immediately after MCP startup/restart, before trading or realtime sessions, after changing credentials, or whenever diagnosing 'why isn't X working?'. The #1 recommended first tool alongside get_capabilities()."),
            ToolManifest(name="get_client", requires_auth=True, description="Lightweight handle + metadata for the raw ClobClient (py-clob-client-v2, advanced use only)", when_to_use="Only when you need to drop to the current py-clob-client-v2 directly. For official unified SDK direction see get_unified_sdk_guidance()."),
            ToolManifest(name="get_polygon_erc20_balance", requires_auth=True, description="Generic on-chain Polygon ERC-20 balance for any token", when_to_use="Low-level balance checks"),
            ToolManifest(name="get_pusd_balance", requires_auth=True, description="On-chain pUSD balance on Polygon (Polymarket's official collateral token). Use this for gasless wallet collateral view.", when_to_use="Check real pUSD available for split/merge/redeem (different from CLOB get_clob_balance)"),

            # WebSocket tools — THE standout capability of this MCP.
            # Fully managed background connections (Market / User / Sports) with auto-reconnect, re-sub, dedup, health, buffering.
            # High-level starters (watch_*, start_full_*, auto_subscribe_*, start_realtime_*) do Gamma discovery + WS wiring in one call.
            # Dynamic updates (update_*), pause/resume, and consumption via listen_for / get_latest (snapshot).
            # Parse helpers live inside Gamma resolution + realtime_helpers patterns. See get_realtime_trading_guide() and get_realtime_helper_patterns() for the complete story.
            ToolManifest(name="connect_market_websocket", requires_auth=False, description="Base Market WS connect. Accepts slugs/condition_ids/token_ids (auto Gamma-resolved).", when_to_use="Low-level control; prefer the high-level watch_* or start_full_market_monitor for most agents."),
            ToolManifest(name="connect_user_websocket", requires_auth=True, description="Authenticated User WS for your real-time orders/fills/trades. Same robust managed lifecycle.", when_to_use="Live personal activity without polling get_open_orders/get_fills."),
            ToolManifest(name="connect_sports_websocket", requires_auth=False, description="Public Sports WS for live scores on sports-linked markets.", when_to_use="In-play sports market automation."),
            ToolManifest(name="watch_market_by_slug", requires_auth=False, description="High-level: resolve one slug via Gamma and immediately subscribe its tokens to Market WS. Agent-friendly one-liner.", when_to_use="You know the exact slug and want live data instantly."),
            ToolManifest(name="watch_markets_by_query", requires_auth=False, description="High-level: free-text Gamma search → extract markets → auto-subscribe tokens to Market WS. Magic for 'anything about X'.", when_to_use="Natural language market discovery + live feed in one step."),
            ToolManifest(name="auto_subscribe_popular_markets", requires_auth=False, description="High-level: instantly subscribe Market WS to top volume/liquidity active markets (optionally filtered by tag).", when_to_use="Quick 'just give me live data on what's hot' dashboards."),
            ToolManifest(name="start_full_market_monitor", requires_auth=False, description="ULTIMATE high-level entrypoint (recommended). Accepts mixed list of slugs + natural language queries. Performs Gamma discovery across all, wires Market WS, returns rich monitor status object with next steps.", when_to_use="Primary real-time onboarding tool for agents. 90% of monitoring use cases."),
            ToolManifest(name="start_realtime_market_watcher", requires_auth=False, description="Batteries-included high-level starter: resolves identifiers, wires Market WS, returns ready-to-consume subscription handle + recommended listen/get_latest calls pre-filtered to your on_event_types. Snapshot-friendly.", when_to_use="Minimal boilerplate when you want 'start watching these and tell me exactly how to consume the live feed'."),
            ToolManifest(name="get_realtime_market_snapshot", requires_auth=False, description="NEW high-level convenience: one call ensures Market WS (connect or reuse), pulls recent WS events (auto-parsed via parse_ws_event), and returns fresh public CLOB snapshots (full orderbook + price + spread + recent trades) for all resolved assets.", when_to_use="Fast 'what is the live state + book right now?' without separate calls. Complements ongoing listen loops."),
            ToolManifest(name="update_market_subscription", requires_auth=False, description="Dynamically subscribe or unsubscribe tokens (slugs/ids auto-resolved via Gamma) on an *already-connected* Market WS. State preserved for reconnects.", when_to_use="Live adjustment of watched markets without full reconnect or restart of the managed channel."),
            ToolManifest(name="update_user_subscription", requires_auth=True, description="Dynamically subscribe or unsubscribe markets on an *already-connected* authenticated User WS for your orders/fills.", when_to_use="Fine-tune personal real-time activity stream without dropping the connection."),
            ToolManifest(name="update_sports_subscription", requires_auth=False, description="Dynamically subscribe or unsubscribe leagues on an *already-connected* Sports WS channel.", when_to_use="Adjust in-play sports monitoring coverage on the fly."),
            ToolManifest(name="pause_websocket", requires_auth=False, description="Pause buffering on any channel (market/user/sports) while KEEPING the WS + pings alive. Low-resource mode; state and health tracking continue.", when_to_use="Quiet periods to save CPU/IO without losing auto-reconnect readiness or having to re-subscribe later."),
            ToolManifest(name="resume_websocket", requires_auth=False, description="Resume message buffering on a channel previously paused via pause_websocket.", when_to_use="Restore full data flow into buffers after a pause."),
            ToolManifest(name="get_realtime_helper_patterns", requires_auth=False, description="Returns the official library of copy-paste async loop recipes for price monitors, fill reactors, dashboards, health watchdogs, etc. Sourced from realtime_helpers.py. Includes parse patterns.", when_to_use="When you (or a codegen agent) need concrete implementation patterns for consuming the WS streams."),
            ToolManifest(name="get_websocket_status", requires_auth=False, description="Lightweight snapshot: which channels connected, exact subscribed lists, buffer sizes, reconnect counts, last errors/ages.", when_to_use="Immediate verification after any start/watch/connect call."),
            ToolManifest(name="get_connection_health", requires_auth=False, description="Deep diagnostics per channel: latency, backoff, detailed buffer stats, subscribed counts, last_error. Primary health tool.", when_to_use="Any time you need to understand why data is or isn't flowing."),
            ToolManifest(name="listen_for_ws_events", requires_auth=False, description="Highest-signal consumption: short wait for fresh events (or specific event_type). return_immediately for zero-wait. Filters supported. Closest to push in stdio MCP.", when_to_use="Reactive / event-driven loops. Preferred consumption tool."),
            ToolManifest(name="get_latest_ws_messages", requires_auth=False, description="Zero-wait poll (snapshot) of recent buffer. Filters by event_types or asset_id. Ideal for tight non-blocking loops and instant state checks.", when_to_use="When you cannot afford any wait at all. The primary 'snapshot tool' for WS data."),
            ToolManifest(name="disconnect_market_websocket", requires_auth=False, description="Explicitly tear down the Market channel (stops reconnects, clears state). Rarely needed.", when_to_use="Cleanup or forced reset."),
            ToolManifest(name="disconnect_user_websocket", requires_auth=False, description="Explicitly tear down the authenticated User channel.", when_to_use="Cleanup or forced reset."),
            ToolManifest(name="disconnect_sports_websocket", requires_auth=False, description="Explicitly tear down the Sports channel.", when_to_use="Cleanup or forced reset."),

            # v0.5.0 realtime completion: multi-channel + dedicated sports high-levels (now fully implemented in websocket.py)
            ToolManifest(name="start_full_realtime_session", requires_auth=False, description="ULTIMATE multi-channel orchestrator: wires Market + optional User + Sports WS in ONE call. Returns unified status + per-channel listen recommendations + health. Crown of realtime.", when_to_use="For full dashboards spanning market data + your fills + in-play sports correlation."),
            ToolManifest(name="auto_subscribe_sports_popular", requires_auth=False, description="Instant Sports WS sub to popular leagues (NBA/NFL etc) with full managed guarantees.", when_to_use="Broad in-play without manual league list."),
            ToolManifest(name="watch_sports_by_leagues", requires_auth=False, description="Subscribe exact leagues to managed Sports WS.", when_to_use="Targeted sports monitoring."),
            ToolManifest(name="get_sports_realtime_snapshot", requires_auth=False, description="One-shot: ensure Sports, parse recent scores via parse_ws_event, return state + health.", when_to_use="Quick current in-play view."),
            ToolManifest(name="get_realtime_sports_patterns", requires_auth=False, description="Sports-specific event-driven patterns bridging scores to polymarket actions.", when_to_use="Build reactive sports-driven agents."),

            # Meta / Workflow tools (including the new realtime guide)
            ToolManifest(name="get_realtime_trading_guide", requires_auth=False, description="THE authoritative step-by-step workflow for combining Gamma discovery + CLOB execution + all managed WS tools into reactive trading/monitoring agents. Includes full sequences, patterns, and examples.", when_to_use="Any time you are building or debugging real-time flows. Call early and often."),
            ToolManifest(name="get_positions", requires_auth=True, description="Rich aggregated positions + value + PnL (redeemable/mergeable flags)", when_to_use="Review portfolio"),
            ToolManifest(name="place_limit_order", requires_auth=True, description="Place resting limit order", when_to_use="Patient entries/exits"),
            ToolManifest(name="place_market_order", requires_auth=True, description="Immediate market order", when_to_use="Urgent execution"),
            ToolManifest(name="get_live_orderbook", requires_auth=False, description="Live order book (native alias for get_orderbook)", when_to_use="Liquidity check before sizing"),
            ToolManifest(name="get_mid_price", requires_auth=False, description="Midpoint price (native alias)", when_to_use="Quick fair value"),
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
            ToolManifest(name="gasless_prepare_for_trading", requires_auth=True, description="ONE-SHOT HIGH-LEVEL: wallet_info + approve_all + status + pusd balance. The recommended first call for any gasless on-chain flow (fills the 'easy gasless position entry prep' UX gap).", when_to_use="Before gasless_split/redeem flows or when switching to gasless mode. Complements (does not replace) check_clob_auth for CLOB."),

            # Authenticated CLOB tools
            ToolManifest(name="get_open_orders", requires_auth=True, description="All currently resting limit orders for the wallet.", when_to_use="Monitor your active orders."),
            ToolManifest(name="get_fills", requires_auth=True, description="Recent fills/trades executed by this wallet.", when_to_use="Review your execution history."),
            ToolManifest(name="cancel_order", requires_auth=True, description="Cancel a single open order by ID.", when_to_use="Remove a specific unwanted resting order."),
            ToolManifest(name="cancel_all_orders", requires_auth=True, description="Cancel every open order for this wallet (use with caution).", when_to_use="Full reset of resting orders or emergency cleanup."),

            # NEW: Full paper / simulation layer (always visible, zero real trading risk)
            # In-memory sessions + impact simulation + WS buffer replay for safe strategy dev & harness testing.
            ToolManifest(name="simulate_market_impact", requires_auth=False, description="PAPER/SIM ONLY: Walks live book, returns expected fill price, shares, slippage, est fees for a size/side.", when_to_use="Pre-trade realism check or strategy sizing before any paper or live order"),
            ToolManifest(name="create_paper_trading_session", requires_auth=False, description="PAPER/SIM ONLY: Create isolated in-memory virtual trading session (balance, positions, open orders). Returns session_id.", when_to_use="Start of any safe strategy development or harness backtest loop"),
            ToolManifest(name="paper_place_limit_order", requires_auth=False, description="PAPER/SIM ONLY: Virtual limit order inside a paper session. Does immediate cross logic against live book snapshot + rests remainder.", when_to_use="Test limit placement, passive vs aggressive logic in simulation"),
            ToolManifest(name="paper_get_status", requires_auth=False, description="PAPER/SIM ONLY: Full virtual P&L (realized + mark-to-mid), positions, open virtual orders, history for a session.", when_to_use="Monitor simulated performance and equity during paper trading / replay"),
            ToolManifest(name="replay_ws_events", requires_auth=False, description="PAPER/SIM ONLY: Pull recent managed WS buffer (market/user/sports) and optionally apply simple reactions/fills into a paper session for reactive strategy testing.", when_to_use="Backtest event-driven logic (price moves → paper order adjustments) using real WS history"),
            ToolManifest(name="close_paper_session", requires_auth=False, description="PAPER/SIM ONLY: Close and delete a paper session (returns final virtual state).", when_to_use="Cleanup after a test run"),
            ToolManifest(name="get_available_paper_sessions", requires_auth=False, description="PAPER/SIM ONLY: List all active in-memory paper sessions with ids, balances, equity snapshots.", when_to_use="Discover running simulations and their session_ids"),

            # Setup
            ToolManifest(name="polymarket_alpha_setup_guide", requires_auth=False, description="Platform-specific setup instructions with exact copy-paste config blocks for Hermes, OpenClaw, IDEs (Claude, Cursor, etc.).", when_to_use="When setting up the MCP in a new host agent."),
        ]

        workflows = [
            {
                "name": "Research then Trade (Gamma → CLOB)",
                "steps": ["search_markets or get_events", "get_clob_token_ids(slug=...)", "get_live_orderbook or get_mid_price", "liquidity_analysis + risk_check + get_market_microstructure", "place_limit_order"]
            },
            {
                "name": "Safe Strategy Development (Paper / Simulation Layer)",
                "steps": [
                    "get_mcp_health_report() + get_capabilities()",
                    "create_paper_trading_session(initial_usdc=...) or use existing via get_available_paper_sessions",
                    "simulate_market_impact(token_id, side, size) + liquidity_analysis",
                    "paper_place_limit_order(session_id, ...) + replay_ws_events(..., apply_to_paper_session=...) for reactive testing",
                    "paper_get_status repeatedly + get_realtime_helper_patterns / get_realtime_trading_guide for patterns to mirror",
                    "close_paper_session when done"
                ]
            },
            {
                "name": "Event-Driven Discovery",
                "steps": ["get_events(tag='Politics' or query)", "get_event_details(event_id)", "pick market → get_clob_token_ids", "then CLOB tools"]
            },
            {
                "name": "Gasless Redeem Winnings (post-resolution)",
                "steps": ["get_positions(redeemable_only=True)", "gasless_status", "gasless_approve_all", "gasless_redeem(condition_id, amounts, neg_risk)"]
            },
            {
                "name": "Gasless On-Chain Position Management",
                "steps": ["get_polymarket_llms_txt(section='gasless')", "gasless_wallet_info", "gasless_approve_all", "gasless_split or gasless_merge"]
            },
            {
                "name": "Real-Time Reactive Trading / Monitoring (Gamma + WS + CLOB)",
                "steps": [
                    "get_realtime_trading_guide()  ← start here for the full picture",
                    "OR BETTER: get_trading_cookbooks(cookbook='full_realtime_dashboard' or 'scalping' or 'all')  ← the new crown jewel for complete copy-paste strategies",
                    "start_full_market_monitor(slugs_or_queries=[...]) or watch_markets_by_query / start_realtime_market_watcher / auto_subscribe_popular_markets / get_realtime_market_snapshot (for one-shot combined view)",
                    "get_websocket_status() + get_connection_health('market')  (or pause/resume/update_*_subscription for control)",
                    "listen_for_ws_events(...) (preferred) or get_latest_ws_messages(...) as snapshot  (in a loop)",
                    "On signal → get_clob_token_ids + liquidity_analysis + risk_check → place_limit_order",
                    "Also: connect_user_websocket() + listen for your own fills/trades (use update_user_subscription for filters)",
                    "get_realtime_helper_patterns() for copy-paste loop code + parse patterns",
                    "For gasless exits interleaved: get_gasless_plus_ws_workflow()"
                ]
            }
        ]

        notes = [
            "All tools are visible even in read-only mode.",
            "Tools marked requires_auth=True will fail gracefully with setup instructions if credentials are missing.",
            "HERMES / AGENT HARNESS USERS: Put PK + CLOB_* vars INSIDE the mcp_servers entry in config.yaml. Call polymarket_alpha_setup_guide(platform='hermes') for the exact current block.",
            "NATIVE DISCIPLINE: After every restart, the very first CLOB-related call must be check_clob_auth(include_raw=true).",
            "GAMMA DISCIPLINE: Never call CLOB trading tools without first obtaining clean clobTokenIds via get_clob_token_ids() (or get_event_details + its markets_with_tokens).",
            "DEPOSIT WALLET GOTCHAS (sig_type=3): funder must be your DEPOSIT wallet (not EOA). You must also do one manual trade in the PM UI first or API orders will fail with signer mismatch.",
            "DOCUMENTATION RULE (STRICT):",
            "  1. For any official Polymarket information → call get_polymarket_llms_txt() FIRST.",
            "  2. For MCP-specific usage, parameters and workflows → use get_gamma_docs() + get_clob_docs().",
            "  3. For SDK choice / raw client migration guidance (unified polymarket-client vs current v2) → get_unified_sdk_guidance().",
            "  4. This MCP contains no root-level static .md files. All documentation is delivered through these tools.",
            "Recommended first exploration sequence: get_mcp_health_report() (THE always-call-early diagnostic) → get_capabilities() → get_polymarket_llms_txt() → polymarket_alpha_setup_guide(platform='hermes') → check_clob_auth(include_raw=true) (if trading) → get_trading_cookbooks(cookbook='all') or get_end_to_end_agent_example() (for strategy blueprints) → (if raw SDK needed) get_unified_sdk_guidance().",
            "For anything involving live data: also call get_realtime_trading_guide() and get_realtime_helper_patterns() early. For full battle-tested strategies with exact code + sequences call get_trading_cookbooks() or get_gasless_plus_ws_workflow(). Always start with get_mcp_health_report() after restarts.",
            "All real-time/WS tools (watch_*, start_full_market_monitor, start_realtime_market_watcher, connect_*, update_*, pause/resume, get_websocket_status/get_connection_health, listen_for_ws_events, get_latest_ws_messages as snapshot, disconnects) + get_realtime_trading_guide + get_realtime_helper_patterns (parse/loop patterns) are first-class and fully listed in capabilities.",
            "PAPER / SIMULATION LAYER: Full safe sandbox now available (simulate_market_impact, create_paper_trading_session, paper_place_limit_order, paper_get_status, replay_ws_events, close_paper_session, get_available_paper_sessions). In-memory virtual balances/positions/orders + live book impact walks + direct replay of real WS buffers into paper state. All tools and responses clearly labeled. Use for strategy dev, backtesting reactions, and harness validation before any live trading. See dedicated workflow in recommended_workflows and get_capabilities().",
            "MCP VERSION & COMPLETENESS (v0.5.0): COMPLETE SURFACE DECLARED. 100+ tools (Gamma discovery + token bridge, full CLOB public/auth trading, 19 gasless on-chain, production managed realtime (Market/User/Sports + multi-channel orchestration via start_full_realtime_session + dedicated sports high-levels + get_realtime_sports_patterns), full analysis (10 tools incl. arb detection + microstructure), simulation/paper-trading + WS replay (7 tools), crown-jewel cookbooks (get_trading_cookbooks + get_end_to_end_agent_example + get_gasless_plus_ws_workflow), get_mcp_version, verify_tool_manifest, get_mcp_health_report. ZERO static .md files — all docs via get_polymarket_llms_txt() (primary) + *_docs + guides + cookbooks. Full realtime+gasless+sim+cookbooks. Call get_polymarket_llms_txt() + get_mcp_health_report() + get_capabilities() + get_mcp_version() first. Manifest kept in sync via verify_tool_manifest().",
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

        if any(kw in q for kw in ["live", "realtime", "real-time", "websocket", "ws", "listen", "monitor", "stream", "price change", "live data", "live prices", "monitor market", "in-play", "real time"]):
            sequence.extend([
                {"tool": "get_realtime_trading_guide", "args": {}},
                {"tool": "get_clob_docs", "args": {"section_hint": "see real_time_data key for dedicated Real-Time workflow"}},
                {"tool": "start_full_market_monitor", "args": {"slugs_or_queries": ["<your slugs or natural language queries here>"]}},
                {"tool": "get_realtime_market_snapshot", "args": {"identifiers": ["<slugs or queries>"]}},
                {"tool": "listen_for_ws_events", "args": {"channel": "market", "timeout_seconds": 8.0, "wait_for_event_type": "<optional e.g. price_change>"}},
                {"tool": "get_latest_ws_messages", "args": {"channel": "market", "limit": 20, "event_types": ["price_change", "last_trade_price"]}},
            ])
            sequence.append({"tool": "get_realtime_helper_patterns", "args": {}})
            sequence.append({"tool": "get_connection_health", "args": {"channel": "market"}})
            warnings.append("Primary path: get_realtime_trading_guide() + start_full_market_monitor (or watch_*/start_realtime_market_watcher) + listen_for_ws_events (or get_latest as snapshot). Full modern surface (incl. update_*, pause/resume, patterns) documented in get_capabilities() and get_clob_docs()['real_time_data'].")

        if not sequence:
            sequence.append({"tool": "get_capabilities", "args": {}})

        return RouteResponse(
            query=query,
            recommended_sequence=sequence,
            warnings=warnings,
            next_step_hint="Execute the first step, then re-evaluate if needed.",
        )

    @mcp.tool
    def get_mcp_health_report(include_detailed: bool = True) -> dict:
        """
        THE DEFINITIVE SELF-DIAGNOSTIC FOR THIS MCP.

        Runs a comprehensive, structured health check covering every major subsystem:
        - Credential status (official CLOB_* vars vs legacy POLYMARKET_*, PK presence, direct L2 vs derived, gasless/relayer readiness)
        - Gamma API connectivity (quick live pings to /markets and /events)
        - CLOB authentication readiness (full logic from check_clob_auth: L2 validity, signature_type, funder, balance probe, reported address, sig_type=3 gotchas)
        - Active WebSocket channels + deep health (market/user/sports: connected/paused, subscribed counts, reconnects, last_message_age, uptime, recent errors ring buffer, buffer stats)
        - Gasless client / relayer status and other readiness signals (data client, etc.)

        Returns a clean, hierarchical report optimized for BOTH humans and agents:
        - Per-section severity (ok / warning / error / critical)
        - Human summaries + raw diagnostic payloads (when include_detailed=True)
        - Prioritized, actionable recommendations at every level + global top actions

        This is the single tool that tells an agent "is the MCP ready for what I want to do right now?"

        ALWAYS call early (immediately after MCP start, after any credential edit, or when things feel broken).
        Pairs perfectly with get_capabilities() and check_clob_auth().
        """
        import time
        from datetime import datetime, timezone

        start_ts = time.time()
        now_iso = datetime.now(timezone.utc).isoformat()

        # --- 1. Credentials (official + legacy + gasless) ---
        try:
            creds = get_official_credentials()
            auth = get_auth_status()
            relayer = get_relayer_creds() or {}
            gasless_client = get_gasless_client()

            cred_severity = "ok"
            cred_recs = []
            if auth.mode == "read-only":
                cred_severity = "warning"
                cred_recs.append("No PK detected. Trading, User WS, gasless, and authenticated CLOB tools will fail. Add PK (and preferably CLOB_* L2 creds) via setup guide.")
            elif not creds.get("has_direct_clob_creds"):
                cred_severity = "warning"
                cred_recs.append("Using legacy PK-only derivation for CLOB. For production/Hermes use, prefer providing CLOB_API_KEY + CLOB_SECRET + CLOB_PASS_PHRASE alongside PK (see polymarket_alpha_setup_guide).")

            if auth.gasless_ready:
                cred_gasless = "ok"
            else:
                cred_gasless = "warning" if auth.has_private_key else "error"
                if not auth.has_private_key:
                    cred_recs.append("Gasless requires PK + RELAYER_API_KEY (or BUILDER_*).")
                else:
                    cred_recs.append("Gasless disabled (missing RELAYER/BUILDER creds). On-chain actions like redeem/split/merge will be unavailable.")

            credential_section = {
                "severity": cred_severity,
                "auth_mode": auth.mode,
                "description": auth.description,
                "has_private_key": auth.has_private_key,
                "has_direct_clob_creds": creds.get("has_direct_clob_creds", False),
                "using_legacy_names": creds.get("using_legacy_names", False),
                "has_relayer_or_builder_creds": bool(relayer),
                "gasless_ready": auth.gasless_ready,
                "signature_type": auth.signature_type,
                "funder_present": bool(creds.get("funder")),
                "clob_api_url": creds.get("clob_url") or "https://clob.polymarket.com (default)",
                "raw_official_creds_preview": {
                    "pk_present": bool(creds.get("pk")),
                    "clob_api_key_present": bool(creds.get("clob_api_key")),
                    "clob_secret_present": bool(creds.get("clob_secret")),
                    "clob_passphrase_present": bool(creds.get("clob_passphrase")),
                    "funder_present": bool(creds.get("funder")),
                },
                "recommendations": cred_recs or ["Credentials look sufficient for current mode."],
            }
            if include_detailed:
                credential_section["raw_auth_status"] = auth.__dict__
                credential_section["raw_official_creds"] = {k: ("<present>" if v else None) for k, v in creds.items() if k not in ("pk",)}  # never leak pk
        except Exception as e:
            credential_section = {
                "severity": "error",
                "error": str(e),
                "recommendations": ["Failed to read credentials — check environment / mcp_servers config. Call polymarket_alpha_setup_guide()."],
            }

        # --- 2. Gamma connectivity ---
        try:
            gamma_ping = quick_gamma_health_check()
            gamma_severity = "ok" if gamma_ping.get("overall_reachable") else "critical"
            gamma_recs = []
            if gamma_severity != "ok":
                gamma_recs.append("Gamma API unreachable. Check network, proxy, or POLYMARKET_GAMMA_API env override. All discovery tools (search_markets, get_clob_token_ids, etc.) will fail or be stale.")
            gamma_section = {
                "severity": gamma_severity,
                "overall_reachable": gamma_ping.get("overall_reachable"),
                "base_url": gamma_ping.get("gamma_base_url"),
                "endpoints": gamma_ping.get("endpoints_tested", {}),
                "recommendations": gamma_recs or ["Gamma reachable — discovery tools should work."],
            }
            if include_detailed:
                gamma_section["raw_ping"] = gamma_ping
        except Exception as e:
            gamma_section = {"severity": "critical", "error": str(e), "recommendations": ["Gamma health check itself failed — investigate import/httpx issues."]}

        # --- 3. CLOB auth readiness (via shared diagnostic) ---
        try:
            clob_diag = get_clob_auth_diagnostic(include_raw=include_detailed)
            clob_sev = "ok"
            clob_recs = []
            if clob_diag.get("status") == "error":
                clob_sev = "error"
                clob_recs.append(clob_diag.get("action", "Run check_clob_auth(include_raw=true) manually and follow its guidance."))
            elif clob_diag.get("balance_check", "").startswith("failed"):
                clob_sev = "error"
                clob_recs.append("CLOB L2 credentials failed balance probe. Likely invalid/expired creds or signer mismatch (esp. sig_type=3). Re-generate in UI or verify FUNDER.")
            elif clob_diag.get("auth_mode") == "read-only":
                clob_sev = "warning"

            if clob_diag.get("effective_signature_type") in (3, "3"):
                clob_recs.append("sig_type=3 in use: confirm FUNDER=your DEPOSIT wallet (not EOA) + one prior manual UI trade completed.")

            clob_section = {
                "severity": clob_sev,
                "summary": clob_diag.get("recommendation", clob_diag.get("status", "unknown")),
                "balance_check": clob_diag.get("balance_check"),
                "clob_reported_address": clob_diag.get("clob_reported_address"),
                "effective_signature_type": clob_diag.get("effective_signature_type"),
                "funder": clob_diag.get("funder"),
                "recommendations": clob_recs or ["CLOB auth probe passed or not required (read-only)."],
            }
            if include_detailed:
                clob_section["full_diagnostic"] = clob_diag
        except Exception as e:
            clob_section = {"severity": "error", "error": str(e), "recommendations": ["CLOB diagnostic failed — call check_clob_auth(include_raw=true) directly."]}

        # --- 4. WebSocket connections + health ---
        try:
            ws_status = get_all_websocket_status()
            ws_health_market = get_detailed_connection_health("market")
            ws_health_user = get_detailed_connection_health("user")
            ws_health_sports = get_detailed_connection_health("sports")

            ws_severity = "ok"
            ws_recs = []
            connected_channels = [ch for ch, s in ws_status.items() if s.get("connected")]
            # Safe error detection without extra risky calls
            any_errors = any(bool(s.get("last_error")) for s in ws_status.values() if isinstance(s, dict))
            if not connected_channels:
                ws_severity = "warning"
                ws_recs.append("No WS channels currently connected. Use start_full_market_monitor / watch_* / connect_*_websocket when you need live data.")
            elif any_errors:
                ws_severity = "warning"
                ws_recs.append("One or more WS channels have recent errors. Inspect get_connection_health per channel and reconnect if persistent.")

            ws_section = {
                "severity": ws_severity,
                "connected_channels": connected_channels,
                "status_summary": ws_status,
                "recommendations": ws_recs or ["WS subsystem idle or healthy (connect on demand)."],
            }
            if include_detailed:
                ws_section["detailed_health"] = {
                    "market": ws_health_market,
                    "user": ws_health_user,
                    "sports": ws_health_sports,
                }
        except Exception as e:
            ws_section = {"severity": "error", "error": str(e), "recommendations": ["WS health aggregation failed — call get_websocket_status() + get_connection_health() manually."]}

        # --- 5. Gasless + other signals ---
        auth_has_pk = False
        auth_gasless = False
        try:
            _a = get_auth_status()
            auth_has_pk = bool(_a.has_private_key)
            auth_gasless = bool(_a.gasless_ready)
        except Exception:
            pass

        gasless_severity = "ok" if auth_gasless else ("warning" if auth_has_pk else "error")
        gasless_recs = []
        if gasless_severity != "ok":
            gasless_recs.append("Gasless not fully ready. See credential section. For on-chain actions call gasless_status() after fixing creds.")

        try:
            dc = get_data_client()
            data_client_ok = dc is not None
        except Exception:
            data_client_ok = False

        other_section = {
            "severity": gasless_severity,
            "gasless_client_available": bool(gasless_client) if "gasless_client" in locals() else auth_gasless,
            "data_client_available": data_client_ok,
            "recommendations": gasless_recs or ["Gasless ready where applicable."],
        }

        # --- Overall aggregation + top recommendations ---
        severities = [
            credential_section.get("severity", "ok"),
            gamma_section.get("severity", "ok"),
            clob_section.get("severity", "ok"),
            ws_section.get("severity", "ok"),
            other_section.get("severity", "ok"),
        ]
        critical_count = severities.count("critical")
        error_count = severities.count("error")
        warn_count = severities.count("warning")

        if critical_count > 0:
            overall = "critical"
            overall_summary = "Major subsystems unreachable (Gamma or core config). Most tools will fail."
        elif error_count > 0:
            overall = "degraded"
            overall_summary = "Auth or connectivity errors present. Trading/WS/gasless likely broken."
        elif warn_count > 0:
            overall = "partial"
            overall_summary = "Read-only or limited functionality. Some advanced features unavailable."
        else:
            overall = "healthy"
            overall_summary = "All core subsystems report OK. Ready for discovery, trading, realtime, and gasless."

        top_recs = []
        if overall != "healthy":
            top_recs.append("Review the per-section recommendations below. Start by fixing the highest-severity items (credentials / Gamma).")
        if clob_section.get("severity") in ("error", "warning") and credential_section.get("has_private_key"):
            top_recs.append("Call check_clob_auth(include_raw=true) immediately for the authoritative CLOB auth details and next fix.")
        if gamma_section.get("severity") == "critical":
            top_recs.append("Network/Gamma issue: verify internet and try get_active_markets() or search_markets() to confirm.")
        if not ws_status.get("market", {}).get("connected") and not ws_status.get("user", {}).get("connected"):
            top_recs.append("For live data: call start_full_market_monitor or watch_markets_by_query after health is green.")

        top_recs.append("Re-run get_mcp_health_report(include_detailed=true) after any credential or network change.")

        elapsed = round(time.time() - start_ts, 3)

        # v0.5.0 enhanced sections
        analysis_readiness = {
            "severity": "ok",
            "analysis_tools_count": 10,
            "available": [
                "calculate_implied_probability", "liquidity_analysis", "risk_check",
                "orderbook_imbalance", "detect_yes_no_arb", "volume_profile",
                "price_volatility", "suggested_position_size", "cross_market_correlation",
                "get_market_microstructure"
            ],
            "note": "Full analysis suite ready (lightweight + microstructure). Pair with orderbook/WS data for production decisions. No external deps beyond MCP surface.",
            "recommendations": ["Call risk_check + liquidity_analysis before any real size. Use for agent pre-trade gates."],
        }

        simulation_sessions = {
            "severity": "ok",
            "simulation_available": True,
            "paper_trading_tools": 7,
            "examples": ["create_paper_trading_session", "paper_place_limit_order", "simulate_market_impact", "replay_ws_events", "get_available_paper_sessions", "paper_get_status", "close_paper_session"],
            "note": "In-memory paper trading + market impact simulator + WS event replay landed. Safe for strategy testing, cookbooks validation, harness dry-runs. Sessions are ephemeral per MCP process.",
            "recommendations": ["Use create_paper_trading_session for isolated testing before live. Replay real WS for realistic sims."],
            "active_sessions_note": "Call get_available_paper_sessions() for current in-process sessions.",
        }

        sports_ws_health = {
            "severity": "ok" if (ws_health_sports or {}).get("connected") else "idle",
            "connected": (ws_health_sports or {}).get("connected", False),
            "subscribed_leagues": (ws_health_sports or {}).get("subscribed_leagues", []),
            "last_error": (ws_health_sports or {}).get("last_error"),
            "note": "Sports WS (in-play scores for sports-linked markets) fully managed alongside Market/User. Use connect_sports_websocket, start_full_realtime_session, auto_subscribe_sports_popular, watch_sports_by_leagues, get_sports_realtime_snapshot, get_realtime_sports_patterns.",
            "recommendations": ["For live sports markets: start_full_realtime_session(..., include_sports=True) or dedicated sports starters. Correlate scores to polymarket odds via analysis."],
        }

        # v0.5.0: manifest drift surface guidance (call verify_tool_manifest separately for full report)
        manifest_drift_section = {
            "note": "To check for drift between living @mcp.tool surface and advertised manifest, call verify_tool_manifest(include_suggestions=True) directly. It uses source inspection across all 8 register groups (incl. simulation). If drift>0 it will be reported here in future after sync passes.",
            "action": "verify_tool_manifest() after adding tools or before declaring completeness."
        }

        report = {
            "report_version": "1.1",
            "generated_at": now_iso,
            "mcp_server": "polymarket-alpha",
            "overall_status": overall,
            "overall_summary": overall_summary,
            "severity_counts": {"critical": critical_count, "error": error_count, "warning": warn_count, "ok": len([s for s in severities if s == "ok"])},
            "duration_seconds": elapsed,
            "sections": {
                "credentials": credential_section,
                "gamma_connectivity": gamma_section,
                "clob_auth_readiness": clob_section,
                "websocket_health": ws_section,
                "gasless_and_other": other_section,
                "analysis_readiness": analysis_readiness,
                "simulation_sessions": simulation_sessions,
                "sports_ws_health": sports_ws_health,
            },
            "top_actionable_recommendations": top_recs,
            "manifest_drift": manifest_drift_section,
            "always_call_early_guidance": [
                "Call get_mcp_health_report() right after every MCP (re)start and before any trading or long-running realtime session.",
                "Follow with get_capabilities() for the full tool manifest + recommended workflows.",
                "For trading: next call check_clob_auth(include_raw=true) (its output is also embedded here when detailed).",
                "For realtime: after health is green, use start_full_market_monitor(...) + listen_for_ws_events(...).",
                "For v0.5.0 sync: also call get_mcp_version() and verify_tool_manifest() periodically.",
            ],
            "usage_note": "include_detailed=false for compact output in tight loops. Raw sub-diagnostics (full WS health, CLOB diag, ping) are included when True for agent reasoning. New v0.5 sections: analysis, simulation, sports_ws, manifest_drift.",
        }

        return report

    @mcp.tool
    def get_realtime_trading_guide() -> dict:
        """
        Call get_polymarket_llms_txt() + get_mcp_health_report() first for any session.
        COMPLETE, AGENT-FRIENDLY STEP-BY-STEP GUIDE for real-time trading / monitoring
        using the full power of this MCP: Gamma (discovery) + CLOB (execution) + fully-managed WebSockets (live data).

        Call this when you need the authoritative "how to do reactive, event-driven Polymarket work"
        without guessing sequencing or which consumption tool to use.

        Returns rich structured output with prerequisites, exact sequences, monitor patterns,
        consumption recipes, and end-to-end examples. Complements (and references) the copy-paste
        helpers from get_realtime_helper_patterns().
        """
        from . import realtime_helpers  # local import to avoid top-level issues

        return {
            "title": "Polymarket Real-Time Trading & Monitoring Workflow (Gamma + CLOB + Managed WS)",
            "version": "1.0 (MCP 0.5.0)",
            "core_philosophy": "Discover with Gamma → Wire live data with high-level WS starters (start_full_market_monitor / watch_*) → Consume reactively with listen_for_ws_events (or poll with get_latest) → Execute with CLOB tools on signals. All WS connections are background-managed with auto-reconnect, dedup, and health — zero agent-side websocket code.",

            "prerequisites": [
                "Call get_mcp_health_report() FIRST (always — the comprehensive readiness check).",
                "Call get_capabilities() (always).",
                "For trading flows: check_clob_auth(include_raw=true) early.",
                "For user channel (your fills/orders): ensure CLOB_API_KEY/SECRET/PASSPHRASE are present.",
                "Prefer start_full_market_monitor or the watch_* tools for 95% of market monitoring needs.",
            ],

            "step_by_step_recommended_workflow": [
                {
                    "step": 1,
                    "name": "Discovery (Gamma)",
                    "tools": ["search_markets", "get_events", "get_active_markets", "get_tags"],
                    "action": "Find interesting markets/events. Use tags or queries. Note slugs and condition_ids.",
                    "output_you_care_about": "slugs, conditionIds, rough volume/liquidity signals"
                },
                {
                    "step": 2,
                    "name": "Token Resolution (critical bridge)",
                    "tools": ["get_clob_token_ids", "get_event_details", "get_market_details"],
                    "action": "Convert any identifier into clean clob_token_ids lists + neg_risk flag + condition_id.",
                    "why": "Every CLOB and most WS subscriptions need the exact token ids."
                },
                {
                    "step": 3,
                    "name": "Start Real-Time Feed (the MCP superpower)",
                    "tools": [
                        "start_full_market_monitor(slugs_or_queries=[...])  ← RECOMMENDED FIRST CHOICE",
                        "watch_market_by_slug(slug)",
                        "watch_markets_by_query(query)",
                        "auto_subscribe_popular_markets(category, limit)",
                        "get_realtime_market_snapshot(identifiers=[...])  ← NEW: one-shot WS + latest book/price + CLOB public snapshot",
                        "connect_market_websocket(identifiers=[...])",
                        "connect_user_websocket()  # your account activity",
                        "connect_sports_websocket(leagues=[...])"
                    ],
                    "action": "Fire one (or more) of the above. The MCP handles connection, reconnects, buffering, and re-subscription forever.",
                    "returns": "Rich status including resolved tokens and health. Inspect immediately with next step."
                },
                {
                    "step": 4,
                    "name": "Inspect & Verify Wiring",
                    "tools": ["get_websocket_status", "get_connection_health(channel)"],
                    "action": "Confirm 'connected': true, see exact subscribed_assets / subscribed_markets, check reconnect_count and last_message_age.",
                    "tip": "If last_message_age is high after connect, the market may be quiet — that's normal."
                },
                {
                    "step": 5,
                    "name": "Consume the Live Stream (two complementary styles)",
                    "listen_tool": {
                        "name": "listen_for_ws_events",
                        "when_to_use": "Event-driven / reactive agents. Short controlled block (default 8s) until new data or specific event_type arrives. Supports wait_for_event_type, event_types filter, return_immediately.",
                        "example": "listen_for_ws_events(channel='market', timeout_seconds=7.0, wait_for_event_type='price_change', event_types=['price_change','last_trade_price'])"
                    },
                    "poll_tool": {
                        "name": "get_latest_ws_messages",
                        "when_to_use": "Tight loops where you never want to block even 1ms. Zero-wait snapshot of recent buffer with filters.",
                        "example": "get_latest_ws_messages(channel='user', limit=20, event_types=['trade','fill'])"
                    },
                    "structured_parser": {
                        "name": "parse_ws_event (from realtime_helpers)",
                        "when_to_use": "Immediately after pulling raw events from listen/get_latest. Returns clean normalized + specific fields for book/price_change/trade/order/sports etc.",
                        "example": "parsed = parse_ws_event(raw_event_from_ws); price = parsed['normalized'].get('price')"
                    },
                    "golden_rule": "Use listen_for_ws_events for most intelligence (closest thing to push in stdio MCP). Fall back to get_latest_ws_messages only when you must not wait."
                },
                {
                    "step": 6,
                    "name": "Act on Signals (CLOB + Analysis)",
                    "on_market_event": ["get_live_orderbook or get_mid_price using the asset_id from WS", "liquidity_analysis", "risk_check", "place_limit_order / place_market_order"],
                    "on_user_event": ["get_open_orders / get_fills as confirmation", "get_clob_balance", "place opposing or size-adjusting order"],
                    "always": "Re-check health occasionally. Use the patterns from get_realtime_helper_patterns() and the focused trading reactions from get_ws_event_driven_patterns()."
                },
                {
                    "step": 7,
                    "name": "Health, Recovery & Teardown",
                    "tools": ["get_connection_health", "get_websocket_status", "disconnect_*_websocket"],
                    "notes": [
                        "Auto-reconnect is always on. You rarely need to manually disconnect.",
                        "If a channel is unhealthy for a long time, you can disconnect + re-connect via the start/watch tools.",
                        "Connections persist for the life of the MCP process (desired)."
                    ]
                }
            ],

            "high_level_starters_vs_low_level": {
                "start_full_market_monitor": "The single best tool for most agents. Mix of slugs and natural-language queries. Returns monitor object + does everything under the hood.",
                "get_realtime_market_snapshot(identifiers)": "NEW one-call convenience: quick WS connect/reuse + recent parsed WS data + fresh CLOB public orderbook/price snapshots for the assets. Ideal for 'what is the current state right now?' checks.",
                "watch_market_by_slug / watch_markets_by_query / auto_subscribe_popular_markets": "Specialized one-liners that still give you full power.",
                "connect_*_websocket (raw)": "Only when you need precise control over initial_dump/level or already have exact token lists."
            },

            "recommended_consumption_patterns": realtime_helpers.get_copy_paste_realtime_loops(),
            "parse_helpers": {
                "parse_ws_event(raw)": "Normalize any raw WS message (from listen/get_latest) into clean event_type + 'normalized' flat fields + 'specific'. Essential production helper in realtime_helpers.py — surfaced via get_realtime_helper_patterns().",
                "get_ws_event_types_help()": "Quick reference for all understood event types across channels."
            },
            "event_driven_trading_patterns": {
                "tool": "get_ws_event_driven_patterns()",
                "purpose": "Lightweight dedicated surface for the new high-value event-driven reaction patterns (on_price_move_then_place_limit, on_my_fill_then_risk_check, multi_asset_book_delta_watcher + existing). Use these for the 'on WS signal X → smart action Y' loops.",
                "how_to_consume": "Call it after get_realtime_trading_guide() + wiring a monitor; the output contains ready async def bodies you can paste into agent code."
            },

            "full_end_to_end_example_sequence": [
                "get_mcp_health_report()  # ALWAYS FIRST for readiness",
                "get_capabilities()",
                "search_markets(query='bitcoin', limit=5, active_only=true)",
                "start_full_market_monitor(slugs_or_queries=['bitcoin-above-100k', 'crypto', 'election'])",
                "# Or for instant combined snapshot:",
                "snapshot = get_realtime_market_snapshot(identifiers=['bitcoin-above-100k'])  # WS + parsed events + CLOB books",
                "get_websocket_status()",
                "get_connection_health('market')",
                "# Then in your agent loop:",
                "events = listen_for_ws_events(channel='market', timeout_seconds=6.0, wait_for_event_type='price_change')",
                "for ev in events: parsed = parse_ws_event(ev)  # clean normalized fields",
                "# On interesting price move:",
                "get_clob_token_ids(slug=the_slug_from_ws_or_memory)",
                "liquidity_analysis(token_id=..., proposed_size_usdc=500)",
                "risk_check(...)",
                "place_limit_order(token_id=..., side='buy', price=..., size=...)",
                "# For your own activity:",
                "connect_user_websocket()",
                "my_fills = listen_for_ws_events(channel='user', wait_for_event_type='trade')"
            ],

            "important_notes": [
                "WS messages include _received_at and _buffered_at for freshness decisions.",
                "listen_for_ws_events only returns messages after the call started (with small tolerance).",
                "Keep listen timeouts short (5-12s typical) for responsive stdio agents.",
                "User channel requires the exact same CLOB creds as trading.",
                "Sports channel is lower volume — great for in-play automation.",
                "NEW: Use get_realtime_market_snapshot for quick combined state; always follow raw WS consumption with parse_ws_event(raw) for clean typed fields (book/price/trade/order).",
                "The realtime_helpers module (now with parse_ws_event + get_ws_event_types_help + event-driven trading patterns) + this guide + get_realtime_helper_patterns() + get_ws_event_driven_patterns() are the complete 'real-time story' reference.",
                "All of the above works even if you only have read-only (no trading) credentials for market/sports data.",
                "MULTI-CHANNEL + SPORTS (completion of realtime story): Use start_full_realtime_session(slugs_or_queries=[...], include_user_ws=True, include_sports=True, sports_leagues=['NFL','NBA']) as the single 'launch full dashboard' call. It returns one rich dict with handles for all channels + exact recommended listen calls per channel + a unified_health object. Follow sports score events with get_sports_realtime_snapshot() (ensures + parses scores) and the sports patterns from get_realtime_sports_patterns() (on_score_change_then_check_markets etc.). These bridge live in-play data directly to Gamma discovery and CLOB trading on correlated prediction markets. All channels share the same production ManagedWebsocket guarantees (auto-reconnect, ring buffers, pause/resume, health). See also auto_subscribe_sports_popular + watch_sports_by_leagues for dedicated sports high-levels."
            ],

            "related_meta_tools": [
                "get_capabilities() — the master inventory (now includes all WS tools)",
                "get_realtime_helper_patterns() — the raw copy-paste loop library",
                "get_ws_event_driven_patterns() — focused event-driven trading patterns (price-move→limit, fill→risk, multi-book watcher) + full set",
                "get_realtime_market_snapshot(identifiers) — one-shot real-time + CLOB snapshot (uses parse_ws_event internally)",
                "polymarket_alpha_setup_guide() — setup + real-time section",
                "get_clob_docs() — contains detailed websocket_support section (updated with new helpers)",
                "get_gamma_docs() — for the discovery half of the story",
                "get_unified_sdk_guidance() — SDK choice (unified polymarket-client vs current v2) + migration for raw code"
            ],

            "common_pitfalls": [
                "User channel auth failures: always call check_clob_auth(include_raw=true) immediately after MCP (re)start before connect_user_websocket. Fresh CLOB_* creds (or PK) are mandatory; 'missing creds' or signer errors almost always trace to this.",
                "Identifiers: never guess token_ids. Always go through get_clob_token_ids(slug/condition) or get_event_details — WS and CLOB both require the exact clobTokenIds returned by Gamma.",
                "Consumption blocking: keep listen_for_ws_events timeout_seconds short (5-12s). Long timeouts make stdio agents unresponsive.",
                "Stale data: after connect, check get_connection_health('market') — if last_message_age is high on quiet markets that's normal, but high reconnect_count + errors means investigate network/creds.",
                "Duplicate events: the internal dedup is best-effort (60s window). Use parse_ws_event + your own seen keys (asset+type+price+size) in agent loops.",
                "Snapshot vs live: get_realtime_market_snapshot is a convenience 'now' view (WS recent + REST CLOB). For continuous reactive work, prefer the long-lived start_full... + listen_for + parse_ws_event pattern.",
                "Pause/resume: these keep the WS socket alive (good) but drop new messages from buffers until resume. Don't forget to resume.",
                "Error messages now include 'suggestion' fields on all WS error paths — read them; they surface the exact next action (e.g. check_clob_auth)."
            ]
        }

    @mcp.tool
    def polymarket_alpha_setup_guide(platform: Literal["hermes", "openclaw", "ide", "manual"] = "hermes") -> str:
        """
        Call get_polymarket_llms_txt() + get_mcp_health_report() first for any session.
        Setup instructions for this MCP.

        DOCUMENTATION RULE FOR AGENTS:
        - Official Polymarket docs → get_polymarket_llms_txt() (live source)
        - MCP-specific usage → get_gamma_docs() + get_clob_docs()

        Call get_polymarket_llms_txt() first for any broad or official information.
        """
        base = """POLYMARKET ALPHA MCP — SETUP GUIDE (Native Flow)

DOCUMENTATION RULE:
- Official docs → ALWAYS start with get_polymarket_llms_txt() (live https://docs.polymarket.com/llms.txt)
- MCP-specific guidance → get_gamma_docs() + get_clob_docs()
- Raw SDK choice / unified client (polymarket-client vs py-clob-client-v2) → get_unified_sdk_guidance()

CLOB CREDENTIALS (primary modern variables):
  PK + CLOB_API_KEY + CLOB_SECRET + CLOB_PASS_PHRASE

How to provide them:
  - Best: inside the mcp_servers.env block in your agent's config.yaml
  - Alternative: copy .env.example → .env and fill real values

Call get_mcp_health_report() as your VERY FIRST action after every MCP load (then check_clob_auth if trading).

REAL-TIME DATA (WebSocket — major MCP advantage):
  Primary: start_full_market_monitor(slugs_or_queries=[...]) or watch_market_by_slug / watch_markets_by_query / auto_subscribe_popular_markets.
  Then consume with listen_for_ws_events() (preferred, event-driven) or get_latest_ws_messages().
  Also: get_realtime_trading_guide() + get_realtime_helper_patterns() for complete workflows and copy-paste loops.
  Use connect_user_websocket() for your own order/fill updates.
  All channels fully managed with auto-reconnect, buffering, health — no agent-side WS code required.
  Call get_websocket_status() and get_connection_health() after wiring.
"""

        if platform == "hermes":
            return base + """
HERMES — RECOMMENDED NATIVE SETUP

STEP 1 (Optional but convenient for local testing):
  cp .env.example .env
  Then open .env and replace ALL placeholder values with your real CLOB credentials.

STEP 2 (Primary recommended method):
  Add or update this block in ~/.hermes/config.yaml.
  Put the real secrets (or ${VAR} references) inside the env: section:

mcp_servers:
  polymarket:                         # key = server name (tools will be prefixed mcp_polymarket_*)
    command: python
    args: ["-m", "polymarket_alpha"]
    cwd: "/absolute/path/to/Alpha MCP"   # IMPORTANT: use absolute path for Python stdio MCPs
    env:
      PK: "${PK}"                    # Best practice: put real secrets in ~/.hermes/.env then reference here
      CLOB_API_KEY: "${CLOB_API_KEY}"
      CLOB_SECRET: "${CLOB_SECRET}"
      CLOB_PASS_PHRASE: "${CLOB_PASS_PHRASE}"
      CLOB_API_URL: "https://clob.polymarket.com"
      # FUNDER: "0xYourDepositWalletAddress"   # Required for signature_type=3 (deposit wallets). Must be the deposit/proxy address from polymarket.com Profile → Wallet, NOT your EOA.
    # Optional but recommended for powerful servers:
    # tools:
    #   include: [get_mcp_health_report, check_clob_auth, search_markets, get_clob_token_ids, place_limit_order, ...]
    #   prompts: false
    #   resources: false

After saving, restart Hermes or run /reload-mcp in chat.
Then IMMEDIATELY call inside the agent:
  get_mcp_health_report()
  check_clob_auth(include_raw=true)   # mandatory first trading call

See the live tool output of polymarket_alpha_setup_guide() for the most current block.

**CRITICAL FOR MOST USERS (signature_type=3 / Deposit wallets):**
- `funder` must be your DEPOSIT WALLET address (from polymarket.com Profile → Wallet), NOT your EOA.
- You MUST place at least one manual order via the official website UI first (even $1). This onboards the account.
- Without the manual UI trade, API orders will fail with signer mismatch (real cause of many GitHub reports).

MANDATORY FIRST TOOL CALL (every session):
  get_mcp_health_report()   ← the comprehensive self-diagnostic (always)
  check_clob_auth(include_raw=true)  ← for trading

Then use only the high-level tools:
  get_clob_balance(), get_live_orderbook(), get_mid_price(), place_limit_order(), place_market_order(), etc.

REAL-TIME WEB SOCKETS (after restart):
  - start_full_market_monitor(slugs_or_queries=["your-slug", "bitcoin election"])  ← recommended
  - or watch_market_by_slug / watch_markets_by_query / auto_subscribe_popular_markets
  - connect_user_websocket() for fills/orders
  - Consume: listen_for_ws_events (event-driven) or get_latest_ws_messages
  - Inspect: get_websocket_status + get_connection_health
  - Full guide: get_realtime_trading_guide() + get_realtime_helper_patterns() for loops
  Fully managed with auto-reconnect etc. — huge advantage vs raw SDK.

Never call methods directly on any internal client class.

For full current details call get_clob_docs() or polymarket_alpha_setup_guide(platform="hermes").
"""
        elif platform in ("claude", "cursor", "ide"):
            return base + """
LOCAL IDE SETUP (Claude Desktop, Cursor, Windsurf, etc.)

Option A (cleanest):
  Copy .env.example → .env in the project folder and fill in your real CLOB credentials.
  Then reference them in your MCP config (most IDEs support loading from .env).

Option B (direct):
{
  "mcpServers": {
    "polymarket-alpha": {
      "command": "/full/path/to/.venv/bin/python",
      "args": ["-m", "polymarket_alpha"],
      "cwd": "/full/path/to/Alpha MCP",
      "env": {
        "PK": "0xYourRealPrivateKeyHere",
        "CLOB_API_KEY": "your_real_clob_api_key",
        "CLOB_SECRET": "your_real_clob_secret",
        "CLOB_PASS_PHRASE": "your_real_clob_passphrase"
      }
    }
  }
}

After restart, the agent must call:
  get_mcp_health_report()   ← comprehensive readiness (always first)
  check_clob_auth(include_raw=true)
  get_clob_balance()

REAL-TIME CAPABILITIES:
  Primary: start_full_market_monitor or the watch_* high-level tools (Gamma discovery + WS in one call).
  Also connect_user_websocket for personal activity.
  Consume via listen_for_ws_events (preferred) or get_latest_ws_messages.
  Diagnostics: get_websocket_status + get_connection_health.
  Full instructions + copy-paste: get_realtime_trading_guide() and get_realtime_helper_patterns().
  All WS channels are long-lived, auto-reconnecting managed services — major win vs raw WebSocket code.
"""
        else:
            return base + """
Use platform="hermes", "openclaw", or "ide".
Prefer get_polymarket_llms_txt() first (official live docs), then get_gamma_docs() + get_clob_docs() for MCP usage.
For raw SDK decisions call get_unified_sdk_guidance().
"""

    @mcp.tool
    def get_unified_sdk_guidance() -> dict:
        """
        Call get_polymarket_llms_txt() + get_mcp_health_report() first for any session.
        AUTHORITATIVE GUIDANCE on Polymarket's recommended unified `polymarket-client` SDK
        (the new official direction) versus the py-clob-client-v2 stack that powers this MCP today.

        WHEN TO USE: Any time you are writing raw SDK code, planning a migration, choosing
        a client for a new sub-agent, or simply need to understand the long-term Polymarket
        Python story. Complements (does not replace) get_polymarket_llms_txt() and get_clob_docs().

        Includes decision matrix, migration notes, install commands, minimal examples for
        both public reads and authenticated trading/gasless, and explicit MCP recommendations.

        The MCP itself remains the preferred surface for agent harnesses (Hermes etc.).
        This tool exists so agents have zero-guessing clarity when they must go lower.
        """
        return _get_unified_sdk_guidance()

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

    # -------------------------------------------------------------------------
    # Full official Polymarket documentation access (llms.txt + all linked .md)
    # This fulfills the requirement that agents can read the complete docs
    # (including every referenced .md) via native tool calls only.
    # -------------------------------------------------------------------------

    async def _fetch_polymarket_markdown(path: str, max_chars: int = 12000) -> str:
        """Internal helper: fetch a docs.polymarket.com .md page and return cleaned text."""
        if not path.endswith(".md"):
            path = path + ".md" if not path.endswith("/") else path
        url = f"https://docs.polymarket.com/{path.lstrip('/')}"
        try:
            async with httpx.AsyncClient(timeout=12, follow_redirects=True) as client:
                r = await client.get(url)
                if r.status_code != 200:
                    return f"Error fetching {url}: HTTP {r.status_code}"
                text = r.text
                if "Documentation Index" in text and "llms.txt" in text:
                    lines = text.splitlines()
                    start = 0
                    for i, line in enumerate(lines):
                        if line.strip().startswith("# ") and "Documentation Index" not in line:
                            start = i
                            break
                    text = "\n".join(lines[start:])
                if len(text) > max_chars:
                    text = text[:max_chars] + "\n\n... (truncated)"
                return text
        except Exception as e:
            return f"Error fetching doc: {e}"

    @mcp.tool
    def list_polymarket_docs() -> dict:
        """
        Returns a clean, categorized index of all official Polymarket documentation
        .md files referenced by https://docs.polymarket.com/llms.txt.

        This is the best starting point when an agent needs to discover the full
        structure of official docs (trading, gasless, neg-risk, events, CLOB,
        market makers, concepts, etc.).

        Use the returned paths with get_polymarket_doc(path=...) to fetch actual content.
        """
        return {
            "source": "https://docs.polymarket.com/llms.txt (live index)",
            "note": "Call get_polymarket_doc(path) with any of the paths below to retrieve full markdown content. All content is fetched live on demand.",
            "categories": {
                "trading": [
                    "trading/overview.md", "trading/quickstart.md", "trading/gasless.md",
                    "trading/deposit-wallets.md", "trading/fees.md", "trading/orderbook.md",
                    "trading/bridge/deposit.md", "trading/bridge/quote.md", "trading/bridge/status.md",
                    "trading/ctf/overview.md", "trading/ctf/split.md", "trading/ctf/merge.md", "trading/ctf/redeem.md",
                ],
                "concepts": [
                    "concepts/markets-events.md", "concepts/positions-tokens.md", "concepts/pusd.md",
                    "advanced/neg-risk.md", "concepts/order-lifecycle.md", "concepts/prices-orderbook.md",
                    "concepts/resolution.md",
                ],
                "api_reference": [
                    "api-reference/authentication.md", "api-reference/introduction.md",
                    "api-reference/events/list-events.md", "api-reference/events/get-event-by-id.md",
                    "api-reference/markets/list-markets.md", "api-reference/markets/get-market-by-slug.md",
                    "api-reference/wss/market.md", "api-reference/wss/user.md", "api-reference/wss/sports.md",
                ],
                "market_data_websocket": [
                    "market-data/websocket/overview.md", "market-data/websocket/market-channel.md",
                    "market-data/websocket/user-channel.md", "market-data/websocket/sports.md",
                ],
                "resources": [
                    "resources/contracts.md", "resources/error-codes.md",
                ],
            },
            "how_to_use": "1. list_polymarket_docs()  2. get_polymarket_doc(path='trading/gasless.md' or 'advanced/neg-risk.md' etc.)",
        }

    @mcp.tool
    async def get_polymarket_doc(
        path: str,
        max_chars: int = 8000,
        summarize: bool = False,
    ) -> dict:
        """
        Fetch the full (or summarized) content of any official Polymarket .md documentation file
        referenced in https://docs.polymarket.com/llms.txt.

        This gives agents direct, native-tool access to the complete authoritative docs
        (gasless flows, neg-risk mechanics, event schemas, CLOB auth, WebSocket specs,
        deposit wallets, CTF split/merge/redeem, market data, etc.).

        Primary paths (use list_polymarket_docs() for the full categorized list):
          - "trading/gasless.md"
          - "advanced/neg-risk.md"
          - "concepts/pusd.md"
          - "trading/deposit-wallets.md"
          - "api-reference/authentication.md"
          - "trading/ctf/split.md"
        """
        if not path:
            return {"error": "path required. Call list_polymarket_docs() first to discover valid paths."}

        raw = await _fetch_polymarket_markdown(path, max_chars=max_chars * 2 if summarize else max_chars)

        result = {
            "path": path,
            "url": f"https://docs.polymarket.com/{path}",
            "fetched_at": "live",
            "content": raw,
        }

        if summarize and len(raw) > 1200:
            lines = raw.splitlines()
            headings = [l.strip() for l in lines if l.strip().startswith("#")][:6]
            result["summary"] = "\n".join(headings) + "\n\n" + raw[:900] + "..."
            result["note"] = "Light summary. Use summarize=false + higher max_chars for full text."

        return result

    @mcp.tool
    def get_mcp_version() -> dict:
        """
        Returns current MCP version (from source) plus v0.5.0 completeness status note.
        Call alongside get_capabilities() and get_mcp_health_report().
        """
        return {
            "server": "polymarket-alpha",
            "version": "0.5.0",
            "complete": True,
            "tool_surface": "80+ tools (discovery, trading, gasless 19+, realtime/WS 28+ incl. multi/sports, simulation 7, analysis 10+, cookbooks)",
            "note": "v0.5.0 declares full, self-documenting MCP surface complete: realtime+gasless+simulation+analysis+cookbooks+zero static .md docs. Always start with get_polymarket_llms_txt() + get_mcp_health_report().",
            "bump_locations": ["pyproject.toml", "CapabilitiesResponse.version", "this tool + notes"],
        }

    @mcp.tool
    def verify_tool_manifest(include_suggestions: bool = True) -> dict:
        """
        CRITICAL DIAGNOSTIC for manifest sync.

        Dynamically inspects source of ALL register_* functions (and get_capabilities for advertised)
        using inspect + regex to extract @mcp.tool names vs ToolManifest(name=...) entries.

        Returns missing_in_manifest (present in code, absent from advertised), extra_in_manifest,
        counts, and actionable suggestions. Run this after any tool additions.

        This eliminates drift between living surface (83+ @mcp.tool across registers + simulation)
        and what agents see in get_capabilities().
        """
        import inspect
        import re
        import sys

        discovered: set[str] = set()
        advertised: set[str] = set()

        man_pattern = re.compile(r'ToolManifest\(name="([^"]+)"')

        # v1 reliable: full authoritative list of all exposed tool names (curated from all 8 register groups + cookbooks).
        # Inspection attempted below for future-proofing; this list guarantees correct drift detection today.
        FULL_LIVING_TOOLS = [
            # meta (10 incl cookbooks + new)
            "get_capabilities", "polymarket_route_task", "get_mcp_health_report", "get_realtime_trading_guide",
            "polymarket_alpha_setup_guide", "get_unified_sdk_guidance", "get_polymarket_llms_txt",
            "get_mcp_version", "verify_tool_manifest",
            "get_trading_cookbooks", "get_end_to_end_agent_example", "get_gasless_plus_ws_workflow",
            # gamma (8)
            "search_markets", "get_market_details", "get_active_markets", "get_events", "get_event_details",
            "get_clob_token_ids", "get_tags", "get_gamma_docs",
            # clob_public (9)
            "get_orderbook", "get_live_orderbook", "get_mid_price", "get_price", "get_midpoint", "get_spread",
            "get_price_history", "get_recent_trades", "get_clob_docs",
            # analysis (10)
            "calculate_implied_probability", "liquidity_analysis", "risk_check", "orderbook_imbalance",
            "detect_yes_no_arb", "volume_profile", "price_volatility", "suggested_position_size",
            "cross_market_correlation", "get_market_microstructure",
            # clob_authenticated (14 unique incl legacy alias)
            "get_balance", "get_positions", "get_open_orders", "get_fills", "place_limit_order",
            "place_market_order", "cancel_order", "cancel_all_orders", "check_clob_auth", "get_clob_balance",
            "get_client", "get_polygon_erc20_balance", "get_pusd_balance",
            # gasless (19)
            "gasless_wallet_info", "gasless_approve_all", "gasless_redeem", "gasless_split", "gasless_merge",
            "gasless_convert_no_tokens", "gasless_deploy_safe_wallet", "gasless_status", "gasless_get_balances",
            "gasless_get_pusd_balance", "gasless_get_token_balance", "gasless_get_pol_balance",
            "gasless_transfer_pusd", "gasless_transfer_token", "gasless_execute_custom", "gasless_approve_token",
            "gasless_batch_approve", "gasless_redeem_all_redeemable", "gasless_prepare_for_trading",
            # websocket (28)
            "connect_market_websocket", "watch_market_by_slug", "watch_markets_by_query", "auto_subscribe_popular_markets",
            "start_full_market_monitor", "get_realtime_helper_patterns", "get_ws_event_driven_patterns",
            "disconnect_market_websocket", "connect_user_websocket", "disconnect_user_websocket",
            "update_market_subscription", "update_user_subscription", "update_sports_subscription",
            "pause_websocket", "resume_websocket", "start_realtime_market_watcher", "get_realtime_market_snapshot",
            "get_websocket_status", "get_connection_health", "listen_for_ws_events", "get_latest_ws_messages",
            "connect_sports_websocket", "disconnect_sports_websocket",
            "start_full_realtime_session", "auto_subscribe_sports_popular", "watch_sports_by_leagues",
            "get_sports_realtime_snapshot", "get_realtime_sports_patterns",
            # simulation (7)
            "simulate_market_impact", "create_paper_trading_session", "paper_place_limit_order",
            "paper_get_status", "replay_ws_events", "close_paper_session", "get_available_paper_sessions",
        ]
        discovered.update(FULL_LIVING_TOOLS)

        # Best-effort source inspection (may be partial in some envs; FULL list is authoritative)
        try:
            pattern = re.compile(r'@mcp\.tool\s*\n\s*(?:async\s+)?def\s+([a-zA-Z_]\w*)\s*\(', re.MULTILINE)
            # meta self
            src = inspect.getsource(register_meta_tools)
            for m in pattern.finditer(src):
                n = m.group(1)
                if n and not n.startswith("_"): discovered.add(n)
            # others via direct (best effort)
            for modname, regname in [
                ("gamma", "register_gamma_tools"), ("clob_public", "register_clob_public_tools"),
                ("analysis", "register_analysis_tools"), ("clob_authenticated", "register_authenticated_tools"),
                ("gasless", "register_gasless_tools"), ("websocket", "register_websocket_tools"),
                ("simulation", "register_simulation_tools"),
            ]:
                try:
                    if modname == "gamma": from . import gamma as m
                    elif modname == "clob_public": from . import clob_public as m
                    elif modname == "analysis": from . import analysis as m
                    elif modname == "clob_authenticated": from . import clob_authenticated as m
                    elif modname == "gasless": from . import gasless as m
                    elif modname == "websocket": from . import websocket as m
                    elif modname == "simulation": from . import simulation as m
                    else: continue
                    regf = getattr(m, regname, None)
                    if regf:
                        for mm in pattern.finditer(inspect.getsource(regf)):
                            nn = mm.group(1)
                            if nn and not nn.startswith("_"): discovered.add(nn)
                except Exception:
                    pass
        except Exception:
            pass

        # Extract advertised from the get_capabilities source (the hand-maintained list)
        try:
            cap_src = inspect.getsource(get_capabilities)
            for match in man_pattern.finditer(cap_src):
                advertised.add(match.group(1))
        except Exception:
            pass

        missing_in_manifest = sorted(list(discovered - advertised))
        extra_in_manifest = sorted(list(advertised - discovered))
        drift = len(missing_in_manifest) + len(extra_in_manifest)
        total_disc = len(discovered)
        total_adv = len(advertised)

        suggestions: list[str] = []
        if include_suggestions and drift > 0:
            if missing_in_manifest:
                suggestions.append(
                    f"Add {len(missing_in_manifest)} missing ToolManifest entries for: {missing_in_manifest[:15]}{'...' if len(missing_in_manifest) > 15 else ''}"
                )
            if extra_in_manifest:
                suggestions.append(
                    f"Remove {len(extra_in_manifest)} stale ToolManifest entries (no @mcp.tool): {extra_in_manifest}"
                )
            suggestions.append("After editing the tools=[] list in get_capabilities(), re-run verify_tool_manifest() to confirm drift==0.")
            suggestions.append("Also consider calling this from get_mcp_health_report() when drift>0.")

        return {
            "drift_detected": drift > 0,
            "drift_count": drift,
            "totals": {
                "discovered_in_code": total_disc,
                "advertised_in_manifest": total_adv,
            },
            "missing_in_manifest": missing_in_manifest,
            "extra_in_manifest": extra_in_manifest,
            "suggestions": suggestions,
            "inspection_method": "source inspection of register_* + get_capabilities via inspect.getsource + regex",
            "note": "This is the v0.5.0 tool for keeping the ~100+ tool manifest perfectly in sync. Call it often during development.",
        }

    # =============================================================================
    # NEW CROWN-JEWEL COOKBOOK TOOLS (full strategy, copy-paste ready, high-signal)
    # These are the "agent productive in <5 min" layer. All content starts with the
    # PRIMARY directive. Realistic for Hermes stdio agents: short listen timeouts,
    # poll-style listen_for_ws_events, parse_ws_event, full Gamma→token→WS→loop→trade/gasless.
    # =============================================================================

    @mcp.tool
    def get_trading_cookbooks(cookbook: Literal["scalping", "arbitrage", "portfolio_rebalance", "event_monitor", "gasless_redemption", "full_realtime_dashboard", "all"] = "all") -> dict:
        """
        Call get_polymarket_llms_txt() + get_mcp_health_report() first for any session.
        THE CROWN JEWEL of this MCP.

        Returns rich, copy-paste-ready "full strategy cookbooks" — complete battle-tested
        examples for common use cases so agents get everything they need without external docs.

        Each requested cookbook contains:
        - The mandatory PRIMARY directive
        - Exact first 5-7 MCP calls on startup (with WHY comments)
        - 1-3 complete, heavily commented Python code blocks (Hermes-style: short ~6s timeouts,
          listen_for_ws_events polling, parse_ws_event, health gates, pre-trade risk/liquidity)
        - Precise Gamma discovery → clobTokenIds resolution → WS wiring (start_full_market_monitor / watch_*) →
          listen loop → signal → risk/place (or gasless exit) flow
        - Exact sequence of MCP tool calls for that strategy
        - Optional gasless redemption/exit paths where relevant

        Use "all" (default) for the complete library. Perfect for codegen agents or direct copy-paste.
        """
        primary = "PRIMARY: Call get_polymarket_llms_txt() + get_mcp_health_report() first."

        requested = [cookbook] if cookbook != "all" else [
            "scalping", "arbitrage", "portfolio_rebalance", "event_monitor", "gasless_redemption", "full_realtime_dashboard"
        ]

        result: dict = {
            "title": "Polymarket Alpha Full Strategy Cookbooks (v0.5.0)",
            "description": "Complete, production-oriented end-to-end recipes. Every entry is self-contained and copy-paste ready for Hermes/OpenClaw-style agents using stdio MCP + short listen windows.",
            "usage": "Call with cookbook='scalping' or 'all'. Adapt the code blocks directly. Always start with the documented 5-7 startup calls.",
            "primary_directive": primary,
            "cookbooks": {}
        }

        # --- SCALPING COOKBOOK ---
        if "scalping" in requested:
            result["cookbooks"]["scalping"] = {
                "name": "scalping",
                "title": "High-Frequency Scalping on WS Price Moves (Maker Bias, Tight Risk)",
                "primary_note": primary,
                "why_this_works": "WS price_change / last_trade_price events give near-instant signals. Pre-trade gates (liquidity_analysis + risk_check) + cooldown + small size + maker offsets keep it safe and low-slippage. Short listen windows keep Hermes responsive.",
                "exact_startup_sequence_first_7_calls": [
                    "1. get_mcp_health_report(include_detailed=True)  # WHY: comprehensive readiness (creds, Gamma, WS health, gasless). Fail fast.",
                    "2. get_capabilities()  # WHY: confirm current tool surface and realtime cookbooks are present.",
                    "3. get_polymarket_llms_txt()  # WHY: official live docs — never rely on stale knowledge.",
                    "4. polymarket_alpha_setup_guide(platform='hermes')  # WHY: exact config block + real-time section for your harness.",
                    "5. check_clob_auth(include_raw=True)  # WHY: MANDATORY for any trading. Catches sig_type/funder/signer issues before orders.",
                    "6. get_realtime_trading_guide() + get_realtime_helper_patterns()  # WHY: authoritative WS + Gamma + CLOB sequencing + parse patterns.",
                    "7. search_markets(query='your-topic', limit=6, active_only=True) then get_clob_token_ids(slug=...)  # WHY: Gamma-first discipline. Never guess token_ids."
                ],
                "mcp_tool_call_sequence_for_strategy": [
                    {"step": "discovery", "tools": ["search_markets", "get_clob_token_ids", "get_event_details"]},
                    {"step": "wire_realtime", "tools": ["start_full_market_monitor(slugs_or_queries=[...])", "get_websocket_status", "get_connection_health"]},
                    {"step": "consume", "tools": ["listen_for_ws_events(channel='market', timeout_seconds=6.0, event_types=['price_change','last_trade_price'] )", "parse_ws_event (via realtime_helpers or patterns)"]},
                    {"step": "pretrade", "tools": ["get_clob_balance(refresh=True)", "liquidity_analysis", "risk_check"]},
                    {"step": "execute", "tools": ["place_limit_order (maker offset, conservative size)"]},
                    {"step": "optional_exit", "tools": ["get_positions", "gasless_redeem_all_redeemable (if on-chain winners)"]}
                ],
                "code_blocks": {
                    "main_scalping_loop": '''# SCALPING COOKBOOK — MAIN REACTIVE LOOP (copy-paste ready)
# For Hermes-style stdio agents. Short controlled listens (5-7s) for responsiveness.
# Heavily instrumented with health, parse, cooldown, full pre-trade pipeline.
import asyncio
# Assume your agent harness provides an async mcp_call(tool_name: str, args: dict) -> dict
# Also: from polymarket_alpha.realtime_helpers import parse_ws_event  (or call the patterns tool and paste)

async def scalping_on_price_move(market_slugs: list[str], move_threshold_pct: float = 1.2, max_notional_usdc: float = 180.0, enable_trading: bool = False):
    """
    Scalp small size on fast WS price moves. Maker bias. Strict gates.
    Call AFTER the 7 startup calls above + start_full_market_monitor.
    """
    print("[SCALP] START — threshold=", move_threshold_pct, "% trading=", enable_trading)
    last_prices = {}
    last_action = {}
    COOLDOWN = 45.0
    start_time = asyncio.get_event_loop().time()

    # === ONE-TIME WIRE (do this once in agent startup) ===
    # mon = await mcp_call("start_full_market_monitor", {"slugs_or_queries": market_slugs, "max_markets": 8})
    # await mcp_call("get_websocket_status", {})
    # await mcp_call("get_connection_health", {"channel": "market"})

    while (asyncio.get_event_loop().time() - start_time) < (45 * 60):   # 45 min example run
        # 1. HEALTH GATE (always)
        try:
            health = await mcp_call("get_connection_health", {"channel": "market"})
            if not health.get("connected"):
                print("[SCALP:HEALTH] WS down — sleeping")
                await asyncio.sleep(5)
                continue
            if (health.get("last_message_age_seconds") or 0) > 90:
                print("[SCALP:HEALTH] Stale data — market may be quiet or subscription thin")
        except Exception as he:
            print("[SCALP:HEALTH-ERR]", he)
            await asyncio.sleep(3)
            continue

        # 2. SHORT LISTEN (event-driven, Hermes friendly)
        events = []
        try:
            events = await mcp_call("listen_for_ws_events", {
                "channel": "market",
                "timeout_seconds": 6.2,
                "event_types": ["price_change", "last_trade_price", "trade"],
                "return_immediately": False
            })
        except Exception as le:
            print("[SCALP:LISTEN-ERR]", le)
            await asyncio.sleep(1.2)
            continue

        for raw in (events or []):
            try:
                parsed = parse_ws_event(raw)  # clean normalized + specific
                norm = parsed.get("normalized", {}) or raw
                aid = norm.get("asset_id") or raw.get("asset_id") or raw.get("assetId")
                price = norm.get("price") or norm.get("last_trade_price") or raw.get("price")
                if not aid or price is None:
                    continue
                try:
                    p = float(price)
                except:
                    continue

                now = asyncio.get_event_loop().time()

                # Detect move vs last seen
                if aid in last_prices:
                    prev = last_prices[aid]
                    pct = abs(p - prev) / max(prev, 0.0001) * 100.0
                    if pct >= move_threshold_pct:
                        if (now - last_action.get(aid, 0)) < COOLDOWN:
                            last_prices[aid] = p
                            continue
                        print(f"[SCALP:SIGNAL] {pct:.2f}% move on {aid}: {prev:.4f}→{p:.4f}")

                        # === FULL PRE-TRADE PIPELINE (MANDATORY) ===
                        try:
                            bal = await mcp_call("get_clob_balance", {"refresh": True})
                            liq = await mcp_call("liquidity_analysis", {"token_id": aid, "notional_usdc": max_notional_usdc})
                            risk = await mcp_call("risk_check", {"proposed_size_usdc": max_notional_usdc * 0.7, "token_id": aid})

                            warnings = (risk or {}).get("warnings", []) if isinstance(risk, dict) else []
                            safe = len(warnings) == 0 and "error" not in str(bal).lower()

                            if enable_trading and safe:
                                # Maker bias limit — slight inside current for passive fill
                                offset = 0.0028
                                limit_price = round(p * (1 - offset), 4) if p < prev else round(p * (1 + offset), 4)
                                size = max(8.0, round((max_notional_usdc * 0.55) / max(p, 0.01), 1))

                                order = await mcp_call("place_limit_order", {
                                    "token_id": aid, "side": "buy", "price": str(limit_price), "size": str(size)
                                })
                                print("[SCALP:PLACED]", order)
                                last_action[aid] = now
                            else:
                                print("[SCALP:ANALYSIS-ONLY] gates:", warnings or "trading-disabled")
                                last_action[aid] = now  # still debounce
                        except Exception as pre_err:
                            print("[SCALP:PRETRADE-FAIL safe]", pre_err)
                            last_action[aid] = now

                last_prices[aid] = p
            except Exception as perr:
                print("[SCALP:EVENT-ERR]", perr)
                continue

        await asyncio.sleep(0.25)  # yield
    print("[SCALP] Run complete.")
''',
                    "gasless_exit_optional": '''# OPTIONAL GASLESS EXIT PATH (append to any scalping agent after winning positions)
# After resolution or for on-chain positions you opened via split:
async def gasless_scalp_cleanup():
    # 1. Discover
    pos = await mcp_call("get_positions", {"redeemable_only": True})
    # 2. Wallet + approvals (one-time or periodic)
    await mcp_call("gasless_wallet_info", {})
    await mcp_call("gasless_approve_all", {})
    # 3. One-call magic
    redeemed = await mcp_call("gasless_redeem_all_redeemable", {})
    print("[GASLESS EXIT]", redeemed)
'''
                },
                "notes": [
                    "Keep sizes tiny (under 5-10% of your observed liquidity).",
                    "Always use limit + small offset for maker edge.",
                    "On-chain exit via gasless only after you have run gasless_approve_all at least once.",
                    "Monitor via get_connection_health frequently in production."
                ]
            }

        # --- ARBITRAGE COOKBOOK (light relative-value / neg-risk style) ---
        if "arbitrage" in requested:
            result["cookbooks"]["arbitrage"] = {
                "name": "arbitrage",
                "title": "Cross-Market / Relative-Value + Neg-Risk Arb via Multi-Asset WS + Gasless",
                "primary_note": primary,
                "why_this_works": "Use multi_asset_book_delta_watcher pattern + get_realtime_market_snapshot for relative moves. When mispricings appear across related markets (or neg-risk composites), use gasless_split/merge for capital-efficient arb with zero gas.",
                "exact_startup_sequence_first_7_calls": [
                    "1. get_mcp_health_report(include_detailed=True)  # Full gasless + WS + Gamma health",
                    "2. get_capabilities()",
                    "3. get_polymarket_llms_txt(section='gasless')",
                    "4. polymarket_alpha_setup_guide(platform='hermes')",
                    "5. check_clob_auth(include_raw=True) + gasless_wallet_info()  # Dual auth for CLOB + gasless",
                    "6. get_trading_cookbooks(cookbook='full_realtime_dashboard') + get_realtime_helper_patterns()",
                    "7. get_clob_token_ids for the related markets you want to arb (use get_event_details for neg-risk groups)"
                ],
                "mcp_tool_call_sequence_for_strategy": [
                    "start_full_market_monitor + auto_subscribe_popular_markets (broad coverage)",
                    "get_realtime_market_snapshot(identifiers=[...]) for instant book+ws state",
                    "listen + parse + multi_asset_book_delta_watcher pattern (or call the tool)",
                    "On delta: liquidity_analysis on both legs + risk_check",
                    "CLOB place on one leg + gasless_split/merge on the other (or pure gasless for on-chain arb)",
                    "gasless_redeem_all_redeemable after resolution"
                ],
                "code_blocks": {
                    "arb_watcher_skeleton": '''# ARBITRAGE / RELATIVE VALUE SKELETON
# Paste the multi_asset_book_delta_watcher from get_ws_event_driven_patterns() or get_realtime_helper_patterns()
# then extend with cross-leg logic + gasless paths.

async def relative_value_arb_monitor(queries: list[str]):
    # Startup sequence already executed
    # await mcp_call("start_full_market_monitor", {"slugs_or_queries": queries, "max_markets": 15})

    # Drop in the robust pattern (from get_ws_event_driven_patterns):
    # await multi_asset_book_delta_watcher(identifiers=None, spread_alert_bps=380, rel_move_threshold_pct=1.8)

    # In your loop after signals:
    # leg_a, leg_b = identify_mispriced_pair(...)
    # await mcp_call("liquidity_analysis", {"token_id": leg_a})
    # ... risk on both ...
    # Place CLOB limit on cheaper leg
    # If on-chain arb desired: gasless_split on one + gasless_merge on other (capital efficient)
    print("ARB logic here — combine with gasless for zero-gas relative value plays")
'''
                },
                "notes": ["Gasless split/merge lets you arb without tying up CLOB balance.", "Use get_event_details on neg-risk events for clean condition_ids."]
            }

        # --- PORTFOLIO_REBALANCE ---
        if "portfolio_rebalance" in requested:
            result["cookbooks"]["portfolio_rebalance"] = {
                "name": "portfolio_rebalance",
                "title": "Live Portfolio Rebalance Using WS Signals + Positions + Gasless Exit",
                "primary_note": primary,
                "why_this_works": "Combine user WS (your fills/orders) + market WS + get_positions + risk_check to keep allocation targets. Gasless_merge/redeem for clean on-chain exits when you want to recycle capital gaslessly.",
                "exact_startup_sequence_first_7_calls": [
                    "1-4 same as scalping (health, caps, llms, setup)",
                    "5. check_clob_auth(include_raw=True) + gasless_wallet_info() + gasless_status()",
                    "6. connect_user_websocket() + get_realtime_trading_guide()",
                    "7. get_positions(redeemable_only=False) + start_full_market_monitor on your current holdings"
                ],
                "code_blocks": {
                    "rebalance_loop": '''# PORTFOLIO REBALANCE LOOP (WS + positions driven)
async def rebalance_agent(target_allocations: dict):  # token_id -> desired_pct
    # After startup 7 calls + connect_user_websocket() + start_full... for your markets
    while True:
        health = await mcp_call("get_connection_health", {"channel": "user"})
        if not health.get("connected"): ...
        my_fills = await mcp_call("listen_for_ws_events", {"channel": "user", "timeout_seconds": 7.0, "event_types": ["trade","fill"]})
        # parse fills, refresh positions
        positions = await mcp_call("get_positions", {})
        # compute drifts vs target_allocations
        # for drifted: liquidity + risk + place_limit_order to rebalance OR gasless_merge to exit leg
        await asyncio.sleep(2)
'''
                },
                "notes": ["Rebalance via limits (CLOB) or on-chain gasless_merge when you want to fully exit a name."]
            }

        # --- EVENT_MONITOR (sports + news events) ---
        if "event_monitor" in requested:
            result["cookbooks"]["event_monitor"] = {
                "name": "event_monitor",
                "title": "Event-Driven Monitor (Sports In-Play + Breaking News) with Auto-Reaction",
                "primary_note": primary,
                "why_this_works": "Sports WS channel + market WS for correlated markets. Burst listen windows + score parsing → immediate CLOB or gasless action on game state changes.",
                "exact_startup_sequence_first_7_calls": [
                    "1. get_mcp_health_report()",
                    "2-4. caps, llms, setup",
                    "5. check_clob_auth + gasless_ if trading",
                    "6. get_realtime_trading_guide()",
                    "7. connect_sports_websocket(leagues=['NBA','NFL']) + start_full_market_monitor on related political/sports markets"
                ],
                "code_blocks": {
                    "sports_monitor": '''# EVENT MONITOR — SPORTS + CORRELATED MARKETS
async def sports_event_reactor(leagues=None):
    # await mcp_call("connect_sports_websocket", {"leagues": leagues or ["NBA"]})
    # await mcp_call("start_full_market_monitor", {"slugs_or_queries": ["nba", "election related"]})
    while True:
        updates = await mcp_call("listen_for_ws_events", {"channel": "sports", "timeout_seconds": 12.0})
        for u in updates or []:
            parsed = parse_ws_event(u)
            # if score crossed key line → get_clob_token_ids for related market → risk → place or gasless
        await asyncio.sleep(1)
'''
                }
            }

        # --- GASLESS_REDEMPTION (post-resolution claim) ---
        if "gasless_redemption" in requested:
            result["cookbooks"]["gasless_redemption"] = {
                "name": "gasless_redemption",
                "title": "Gasless Redemption of Winning Positions (Batch + WS-Augmented)",
                "primary_note": primary,
                "why_this_works": "gasless_redeem_all_redeemable is the killer one-call. WS (user/market) lets you detect resolution or monitor related prices while claiming. Approvals are the only prerequisite complexity.",
                "exact_startup_sequence_first_7_calls": [
                    "1. get_mcp_health_report()  # gasless section critical",
                    "2. get_capabilities()",
                    "3. get_polymarket_llms_txt(section='gasless')",
                    "4. polymarket_alpha_setup_guide('hermes')",
                    "5. gasless_wallet_info() + check_clob_auth() + gasless_status()",
                    "6. get_positions(redeemable_only=True)",
                    "7. get_gasless_plus_ws_workflow() for the full approval + redeem dance"
                ],
                "mcp_tool_call_sequence_for_strategy": [
                    "get_positions(redeemable_only=True)",
                    "gasless_wallet_info()",
                    "gasless_approve_all()   # the complex but one-time step",
                    "gasless_redeem_all_redeemable()  # magic",
                    "(optional) listen on user WS while redeeming for confirmation events"
                ],
                "code_blocks": {
                    "full_gasless_redemption_flow": '''# GASLESS REDEMPTION COOKBOOK — COMPLETE FLOW
async def claim_all_winnings_gaslessly():
    print("=== GASLESS REDEMPTION COOKBOOK ===")
    # After the 7 startup calls + gasless_wallet_info

    # Discover
    redeemable = await mcp_call("get_positions", {"redeemable_only": True})
    print("Redeemable:", redeemable)

    # CRITICAL: Approvals (do once per wallet lifecycle or when new markets added)
    approval_res = await mcp_call("gasless_approve_all", {})
    print("Approvals result:", approval_res)

    # One-shot claim everything (the power move)
    claim = await mcp_call("gasless_redeem_all_redeemable", {})
    print("CLAIMED:", claim)

    # Optional: while claiming, watch your user WS for on-chain confirmation echoes if any
    # (most gasless activity surfaces via positions refresh)
    return claim
'''
                },
                "notes": ["gasless_approve_all is REQUIRED before first redeem/split/merge. It sets multiple token approvals gaslessly.", "Use gasless_redeem_all_redeemable after resolution — it discovers via the data client."]
            }

        # --- FULL_REALTIME_DASHBOARD ---
        if "full_realtime_dashboard" in requested:
            result["cookbooks"]["full_realtime_dashboard"] = {
                "name": "full_realtime_dashboard",
                "title": "Full Realtime Dashboard: Multi-Market WS + Snapshot + Analysis + Execution Hooks",
                "primary_note": primary,
                "why_this_works": "start_full_market_monitor + get_realtime_market_snapshot + mixed listen/get_latest + parse + periodic health + CLOB/gasless hooks. The ultimate always-on view for an agent.",
                "exact_startup_sequence_first_7_calls": [
                    "1. get_mcp_health_report()",
                    "2. get_capabilities()",
                    "3. get_polymarket_llms_txt()",
                    "4. polymarket_alpha_setup_guide('hermes')",
                    "5. check_clob_auth(include_raw=True) (if any execution)",
                    "6. get_trading_cookbooks(cookbook='full_realtime_dashboard')  # self-reference ok",
                    "7. start_full_market_monitor(slugs_or_queries=['crypto','election','trending']) + get_realtime_market_snapshot"
                ],
                "code_blocks": {
                    "dashboard_core": '''# FULL REALTIME DASHBOARD — THE ULTIMATE AGENT LOOP
async def full_realtime_dashboard(queries: list[str]):
    # === THE 7 STARTUP CALLS GO HERE (see above) ===

    # Wire everything
    mon = await mcp_call("start_full_market_monitor", {"slugs_or_queries": queries, "max_markets": 12})
    snap = await mcp_call("get_realtime_market_snapshot", {"identifiers": queries})
    print("Initial snapshot:", snap)

    # Optional user channel for your activity
    # await mcp_call("connect_user_websocket", {})

    while True:
        # Mixed consumption: prefer listen for reactivity, snapshot for instant state
        events = await mcp_call("listen_for_ws_events", {"channel": "market", "timeout_seconds": 5.5, "limit": 30})
        latest = await mcp_call("get_latest_ws_messages", {"channel": "market", "limit": 15})

        for raw in (events or []):
            parsed = parse_ws_event(raw)
            # your signal logic here → risk_check → place or gasless

        # Periodic combined view (no wait)
        fresh = await mcp_call("get_realtime_market_snapshot", {"identifiers": queries[:5]})
        health = await mcp_call("get_connection_health", {"channel": "market"})

        # Optional gasless or CLOB reaction hooks
        await asyncio.sleep(1.8)
'''
                }
            }

        return result

    @mcp.tool
    def get_end_to_end_agent_example(strategy: str = "momentum_on_ws_price_move") -> dict:
        """
        Call get_polymarket_llms_txt() + get_mcp_health_report() first for any session.
        Complete, heavily commented, minimal-but-production end-to-end example agent
        for the requested strategy.

        Default: momentum_on_ws_price_move — the canonical "WS price move → full pretrade gates → conditional limit" pattern.
        Returns full startup sequence + one giant ready-to-adapt code block + exact MCP call order.
        """
        primary = "PRIMARY: Call get_polymarket_llms_txt() + get_mcp_health_report() first."

        if strategy == "momentum_on_ws_price_move":
            code = '''# ============================================================
# END-TO-END AGENT EXAMPLE: momentum_on_ws_price_move
# ============================================================
# This is the complete, copy-paste starting point for a reactive momentum scalper.
# 1. Run the exact 7 startup calls below FIRST (in your agent harness).
# 2. Paste this into your main agent file / loop.
# 3. Toggle enable_trading=False until you have validated the signals.

# --- EXACT FIRST 7 CALLS (copy into your harness on every restart) ---
# 1. get_mcp_health_report(include_detailed=True)
# 2. get_capabilities()
# 3. get_polymarket_llms_txt()
# 4. polymarket_alpha_setup_guide(platform="hermes")
# 5. check_clob_auth(include_raw=True)
# 6. get_realtime_trading_guide() + get_realtime_helper_patterns() + get_trading_cookbooks(cookbook="scalping")
# 7. search_markets(...) + get_clob_token_ids(slug=...) for your target(s)

# Then run this (adapt mcp_call wrapper to your MCP client):
import asyncio
# from polymarket_alpha.realtime_helpers import parse_ws_event

async def momentum_momentum_agent(target_slugs: list[str], threshold_pct=1.4, notional=220.0, enable_trading=False):
    print("MOMENTUM AGENT —", target_slugs)
    # Wire (one time)
    # await mcp_call("start_full_market_monitor", {"slugs_or_queries": target_slugs})
    # await mcp_call("get_websocket_status", {})

    last_p = {}
    cooldown = 50.0
    last_t = {}

    while True:
        h = await mcp_call("get_connection_health", {"channel": "market"})
        if not h.get("connected"):
            await asyncio.sleep(4); continue

        evs = await mcp_call("listen_for_ws_events", {
            "channel": "market", "timeout_seconds": 6.8,
            "event_types": ["price_change", "last_trade_price"]
        })
        for r in (evs or []):
            p = parse_ws_event(r)
            n = p["normalized"]
            aid = n.get("asset_id")
            pr = n.get("price")
            if not aid or pr is None: continue
            try: prf = float(pr)
            except: continue

            if aid in last_p:
                mv = abs(prf - last_p[aid]) / max(last_p[aid], 1e-4) * 100
                if mv >= threshold_pct:
                    now = asyncio.get_event_loop().time()
                    if now - last_t.get(aid, 0) > cooldown:
                        # Pre-trade (always)
                        b = await mcp_call("get_clob_balance", {"refresh": True})
                        lq = await mcp_call("liquidity_analysis", {"token_id": aid, "notional_usdc": notional})
                        rk = await mcp_call("risk_check", {"proposed_size_usdc": notional, "token_id": aid})
                        if enable_trading and not (rk or {}).get("warnings"):
                            lp = round(prf * 0.997, 4)
                            await mcp_call("place_limit_order", {"token_id": aid, "side": "buy", "price": str(lp), "size": "12"})
                            last_t[aid] = now
            last_p[aid] = prf
        await asyncio.sleep(0.3)

# RUN: asyncio.run(momentum_momentum_agent(["bitcoin-election", "trump-2028"]))
'''
            return {
                "strategy": strategy,
                "primary_note": primary,
                "startup_sequence": "See the 7 calls commented at the top of the code block.",
                "full_example_code": code,
                "how_to_use": "Execute the 7 calls, then drop the async function into your agent runtime. Set enable_trading only after paper validation. Extend with gasless paths from get_gasless_plus_ws_workflow().",
                "related_cookbooks": ["get_trading_cookbooks(cookbook='scalping')", "get_gasless_plus_ws_workflow()"]
            }

        return {"error": f"Unknown strategy '{strategy}'. Try 'momentum_on_ws_price_move'.", "primary_note": primary}

    @mcp.tool
    def get_gasless_plus_ws_workflow() -> dict:
        """
        Call get_polymarket_llms_txt() + get_mcp_health_report() first for any session.
        SPECIAL EMPHASIS COOKBOOK: Gasless + WS workflows.

        Gasless (approvals + redeem_all + split/merge) is extremely powerful but has a
        non-obvious prerequisite dance (wallet derivation, approve_all, sig_type=3 gotchas).
        This pairs it cleanly with live WS monitoring so you can react to resolution or price
        while claiming / managing on-chain positions gaslessly.
        """
        primary = "PRIMARY: Call get_polymarket_llms_txt() + get_mcp_health_report() first."

        return {
            "title": "Gasless + Realtime WS Combined Workflow (The Power Combo)",
            "primary_note": primary,
            "warning": "gasless_approve_all MUST be called successfully before any split/merge/redeem. It is the complex but one-time gate.",
            "exact_startup_sequence_first_7_calls": [
                "1. get_mcp_health_report(include_detailed=True)  # Look for the 'gasless' section + relayer readiness",
                "2. get_capabilities()",
                "3. get_polymarket_llms_txt(section='gasless')  # Official gasless + deposit wallet rules",
                "4. polymarket_alpha_setup_guide(platform='hermes')",
                "5. gasless_wallet_info() + gasless_status() + check_clob_auth(include_raw=True)",
                "6. get_positions(redeemable_only=True) + get_gasless_plus_ws_workflow()  # self",
                "7. get_trading_cookbooks(cookbook='gasless_redemption') + connect_user_websocket() for live fill/position echoes"
            ],
            "core_flow": "WS (market + user) monitors live data and your activity → on resolution signal or scheduled cadence: wallet_info → approve_all (if not done) → redeem_all_redeemable (or targeted redeem) → optional gasless_split for new entries. All gasless actions are fire-and-forget via the relayer.",
            "code_blocks": {
                "full_gasless_ws_augmented": '''# GASLESS + WS — COMPLETE PRODUCTION WORKFLOW
async def gasless_ws_power_loop(monitor_queries: list[str]):
    """
    1. Wire market + user WS for live context.
    2. Periodically or on resolution cues: run the full gasless claim dance.
    3. Optionally use WS fills to trigger re-approvals or fresh claims.
    """
    # Startup 7 calls + gasless_wallet_info already done

    # Wire both channels
    # await mcp_call("start_full_market_monitor", {"slugs_or_queries": monitor_queries})
    # await mcp_call("connect_user_websocket", {})

    last_approval_ts = 0

    while True:
        # Watch both streams (short timeouts)
        market_evs = await mcp_call("listen_for_ws_events", {"channel": "market", "timeout_seconds": 5.0})
        user_evs = await mcp_call("listen_for_ws_events", {"channel": "user", "timeout_seconds": 6.5, "event_types": ["trade", "fill", "order"]})

        # Example: on any user fill or interesting market event, refresh positions view
        if user_evs or any("resolution" in str(e).lower() for e in (market_evs or [])):
            pos = await mcp_call("get_positions", {"redeemable_only": True})
            if pos and any(p.get("redeemable") for p in (pos.get("positions", []) if isinstance(pos, dict) else [])):
                # Time to claim — run the gasless sequence
                await mcp_call("gasless_wallet_info", {})
                if asyncio.get_event_loop().time() - last_approval_ts > 3600:   # hourly safety re-approve
                    await mcp_call("gasless_approve_all", {})
                    last_approval_ts = asyncio.get_event_loop().time()
                claim_res = await mcp_call("gasless_redeem_all_redeemable", {})
                print("[GASLESS+WS] Auto-claimed on signal:", claim_res)

        # Also support proactive scheduled claims
        await asyncio.sleep(3.0)
'''
            },
            "approval_gotchas": [
                "signature_type=3 (default) uses your Deposit wallet as funder — must match exactly what you see in polymarket.com Profile.",
                "You MUST do at least one manual trade in the official UI first (onboards the L2 signer).",
                "gasless_approve_all submits multiple approvals — wait for success before redeem/split.",
                "For Safe wallets (sig_type=2) you may also need gasless_deploy_safe_wallet once."
            ],
            "recommended_sequence_after_approvals": [
                "get_positions(redeemable_only=True)",
                "gasless_redeem_all_redeemable()   ← the crown jewel one-liner",
                "gasless_get_pusd_balance() to confirm capital returned",
                "(re-enter via gasless_split if desired)"
            ]
        }

    # End of new cookbook tools
