"""
Public CLOB market data tools (current production path via py-clob-client-v2).

This MCP uses the mature py-clob-client-v2 for CLOB operations.
For Polymarket's official recommended unified direction (`polymarket-client` beta),
see the get_unified_sdk_guidance() meta tool. The high-level MCP tools remain the
preferred surface regardless of underlying SDK.
All of these are visible and usable in read-only mode.
"""

from fastmcp import FastMCP
from py_clob_client_v2 import ClobClient

from .config import get_clob_host, get_chain_id


def register_clob_public_tools(mcp: FastMCP) -> None:
    client = ClobClient(host=get_clob_host(), chain_id=get_chain_id())

    @mcp.tool
    def get_orderbook(token_id: str) -> dict:
        """
        Get current order book for a specific outcome token.

        Always available (public data). Use before placing large orders.
        """
        return client.get_order_book(token_id)

    # Friendly aliases for "simple native" naming
    @mcp.tool
    def get_live_orderbook(token_id: str) -> dict:
        """Alias for get_orderbook. Preferred name in new native flows."""
        return get_orderbook(token_id)  # type: ignore

    @mcp.tool
    def get_mid_price(token_id: str) -> float:
        """Preferred alias for get_midpoint in native trading flows."""
        return client.get_midpoint(token_id)

    @mcp.tool
    def get_price(token_id: str) -> dict:
        """Best bid, best ask, and midpoint for a token. Public data."""
        return {
            "best_bid": client.get_price(token_id, "SELL"),
            "best_ask": client.get_price(token_id, "BUY"),
            "midpoint": client.get_midpoint(token_id),
        }

    @mcp.tool
    def get_midpoint(token_id: str) -> float:
        return client.get_midpoint(token_id)

    @mcp.tool
    def get_spread(token_id: str) -> dict:
        return {"spread": client.get_spread(token_id)}

    @mcp.tool
    def get_price_history(token_id: str, interval: str = "1h", limit: int = 100) -> dict:
        """Historical price data. Public."""
        # Simplified - real implementation would map interval properly
        return client.get_prices_history(token_id, interval=interval)  # type: ignore

    @mcp.tool
    def get_recent_trades(token_id: str, limit: int = 30) -> list:
        """Recent trades for a token. Public data."""
        return client.get_trades(token_id, limit=limit) or []

    @mcp.tool
    def get_clob_docs() -> dict:
        """
        Call get_polymarket_llms_txt() + get_mcp_health_report() first for any session.
        MCP-structured documentation for the Polymarket CLOB (public data + authenticated trading via py-clob-client-v2).

        WHEN TO USE: After get_polymarket_llms_txt() when preparing CLOB calls, clarifying auth requirements (only PRIVATE_KEY needed), order parameters, or confirming the mandatory "Gamma first → CLOB second" routing. Also use for WebSocket real-time patterns.

        RETURNS: dict with api_name, categories, public_endpoints, authenticated_endpoints, how_to_use steps, authentication_notes, routing_notes, real_time_data (dedicated high-level Real-Time Data section + workflow), websocket_support (detailed managed WS section).
        """
        return {
            "api_name": "CLOB (via py-clob-client-v2 in this MCP)",
            "base_url": "https://clob.polymarket.com",
            "description": "Execution layer for order books, limit/market orders, and authenticated portfolio (current production implementation). Orders are signed off-chain. Gasless relayer is NOT needed for CLOB trading. Polymarket's long-term recommended surface is the unified `polymarket-client` SDK (see get_unified_sdk_guidance()).",
            "categories": [
                "Public Market Data (no auth)",
                "Authenticated Portfolio & Orders",
                "Order Placement",
                "Order Cancellation",
                "Real-time Data (fully managed WebSocket: Market + User + Sports with high-level starters, dynamic control, consumption primitives, and parse/loop patterns)"
            ],
            "public_endpoints": [
                {
                    "name": "get_orderbook",
                    "required_parameters": ["token_id"],
                    "description": "Live order book depth"
                },
                {
                    "name": "get_price",
                    "required_parameters": ["token_id"],
                    "description": "Best bid/ask/midpoint"
                },
                {
                    "name": "get_price_history",
                    "required_parameters": ["token_id"],
                    "optional_parameters": ["interval", "limit"],
                    "description": "Historical price candles"
                }
            ],
            "authenticated_endpoints": [
                {
                    "name": "check_clob_auth",
                    "required_parameters": [],
                    "optional": ["include_raw"],
                    "description": "MANDATORY first call for CLOB sessions. Verifies PK + L2 creds, signature_type=3, funder, and L2 validity."
                },
                {
                    "name": "get_clob_balance",
                    "optional": ["refresh"],
                    "description": "Primary balance tool for trading power (USDC on CLOB)"
                },
                {
                    "name": "get_client",
                    "description": "Get raw ClobClient handle + metadata (advanced only)"
                },
                {
                    "name": "get_polygon_erc20_balance",
                    "required_parameters": ["token_address"],
                    "description": "On-chain Polygon ERC-20 balance (different from CLOB collateral)"
                },
                {
                    "name": "place_limit_order",
                    "required_parameters": ["token_id", "side", "price", "size"],
                    "auth_required": "PK (+ optional CLOB_* L2 creds)"
                },
                {
                    "name": "place_market_order",
                    "required_parameters": ["token_id", "side", "amount_usdc"]
                },
                {
                    "name": "get_positions",
                    "description": "Rich portfolio view (recommended over raw balance)"
                },
                {
                    "name": "get_open_orders",
                    "description": "Resting orders"
                },
                {
                    "name": "get_live_orderbook",
                    "description": "Alias for get_orderbook (native naming)"
                },
                {
                    "name": "get_mid_price",
                    "description": "Alias for midpoint price (native naming)"
                }
            ],
            "how_to_use": {
                "step_1": "Get identifiers from Gamma: search_markets / get_events / get_active_markets",
                "step_2": "CRITICAL: Call get_clob_token_ids(slug=...) or get_event_details() to obtain clean parsed clobTokenIds + conditionId",
                "step_3": "Use public CLOB tools for prices/liquidity (get_live_orderbook, get_mid_price, get_orderbook)",
                "step_4": "For trading: check_clob_auth(include_raw=true) FIRST → get_clob_balance() → place_limit_order / place_market_order",
                "step_5": "Monitor + on-chain: get_open_orders, get_fills, get_polygon_erc20_balance"
            },
            "authentication_notes": [
                "Preferred native flow (Hermes / harnesses): provide PK + CLOB_API_KEY / CLOB_SECRET / CLOB_PASS_PHRASE inside the mcp_servers env block.",
                "signature_type defaults to 3 inside the MCP (Deposit wallets / POLY_1271). FUNDER optional via FUNDER env var.",
                "No RELAYER keys needed for pure CLOB trading (orders are off-chain signed).",
                "Gasless tools are only for on-chain CTF actions (redeem, split, etc.).",
                "Always call check_clob_auth(include_raw=true) as your first tool after startup."
            ],
            "critical_gotchas_for_deposit_wallets": [
                "For signature_type=3 (the common new-user flow): the `funder` address MUST be your DEPOSIT WALLET address, NOT your EOA/MetaMask address.",
                "You can find your deposit wallet at polymarket.com → Profile → Wallet (it is a different 0x address than your signer EOA).",
                "CRITICAL UNDOCUMENTED STEP: For sig_type=3 accounts, you MUST place at least one manual order through the official Polymarket UI first (even $1).",
                "Without the manual UI trade, the first API order will often fail with 'signer mismatch' or similar errors. This is the real root cause behind many GitHub reports (e.g. clob-client issue #70).",
                "After the one manual UI trade, API orders with correct funder + sig_type=3 should work."
            ],
            "setup_instructions_for_agents": [
                "1. In the project folder, run: cp .env.example .env",
                "2. Edit the new .env file and replace every placeholder with your real values (PK, CLOB_API_KEY, CLOB_SECRET, CLOB_PASS_PHRASE, FUNDER if needed).",
                "3. For Hermes: Prefer putting the values (or ${VAR} references) inside the mcp_servers.env block in config.yaml instead of relying only on a global .env.",
                "4. Call check_clob_auth(include_raw=true) immediately after starting the MCP."
            ],
            "routing_notes": [
                "Gamma first → CLOB second. Never call any trading tool without first getting clean clobTokenIds via get_clob_token_ids().",
                "Preferred sequence: search_markets/get_events → get_clob_token_ids (or get_event_details) → liquidity/risk → place_order.",
                "CLOB is for execution; Gamma is for discovery and metadata.",
                "For CLOB setup questions: call polymarket_alpha_setup_guide(platform='hermes') or read the output of get_clob_docs()."
            ],
            "real_time_data": {
                "description": "Production-grade, fully-managed real-time story: Gamma discovery + background WebSocket wiring (auto-reconnect, dedup, buffering, health) + two consumption primitives (event-driven + snapshot poll) + dynamic control + copy-paste patterns. Zero agent-side WS or asyncio code required. All tools visible in read-only mode.",
                "philosophy": "Discover (Gamma) → Wire once with high-level starters (start_full_market_monitor or watch_* or start_realtime_market_watcher) → Inspect (get_websocket_status + get_connection_health) → Consume reactively (listen_for_ws_events with wait_for_event_type preferred) or snapshot (get_latest_ws_messages with return_immediately) → Act (CLOB on signals). Use pause/resume for efficiency; update_* for live subscription changes.",
                "recommended_entrypoints": [
                    "get_realtime_trading_guide() — authoritative full workflow + sequences + examples (call this first for real-time)",
                    "start_full_market_monitor(slugs_or_queries=[...]) — ultimate one-call Gamma+WS powerhouse (mix slugs + natural language)",
                    "get_realtime_market_snapshot(identifiers=[...]) — NEW high-level: quick WS connect/reuse + latest book/price data + full CLOB public snapshots (orderbook/price/spread/trades) in one call; uses parse_ws_event internally",
                    "watch_market_by_slug / watch_markets_by_query / auto_subscribe_popular_markets — specialized high-level starters",
                    "start_realtime_market_watcher — batteries-included with ready subscription handle + pre-built consumption examples",
                    "get_realtime_market_snapshot(identifiers) — combined one-shot: WS recent parsed events (using parse_ws_event) + fresh public CLOB book/price/spread/trades per asset"
                ],
                "consumption_primitives": {
                    "listen_for_ws_events": "Event-driven reactive consumption. Supports timeout, event_types filter, wait_for_event_type (blocks for specific), return_immediately (zero-wait snapshot). Returns only fresh messages with _received_at timestamps.",
                    "get_latest_ws_messages": "Primary snapshot / poll tool. Zero-wait buffer read with event_types + asset_id filters. Perfect for tight loops and 'what is the current state?' checks."
                },
                "control_tools": [
                    "update_market_subscription / update_user_subscription / update_sports_subscription — dynamic add/remove without reconnect",
                    "pause_websocket / resume_websocket — keep connection alive but stop/start buffering (resource saver)",
                    "get_connection_health(channel) — deep per-channel diagnostics (uptime, recent_errors ring buffer, backoff, latency)",
                    "get_websocket_status — cross-channel lightweight snapshot of subscriptions + health basics"
                ],
                "parse_and_patterns": "NEW structured output helper: parse_ws_event(raw_message) in realtime_helpers.py (and exposed via get_realtime_helper_patterns) returns typed dicts with 'event_type', 'normalized' (clean asset_id/price/size/side/timestamp etc), and 'specific' (bids/asks for book, scores for sports, order details etc) for all common event_types. Full copy-paste async loop recipes available via get_realtime_helper_patterns(). realtime_helpers.py + get_realtime_market_snapshot are the source of truth for modern consumption.",
                "channels": {
                    "market": "Public prices, book updates, trades for any tokens (slugs/conditionIds/tokenIds auto-resolved)",
                    "user": "Authenticated real-time your orders, fills, trades (requires same CLOB creds as trading)",
                    "sports": "Public live scores/updates for sports-linked markets (lower volume, great for in-play automation)"
                },
                "production_notes": [
                    "Connections are long-lived for the MCP process lifetime with automatic exponential-backoff reconnect + re-sub + (for user) re-auth.",
                    "Always inspect health after wiring. Keep listen timeouts short (5-12s) for responsive stdio agents.",
                    "get_realtime_helper_patterns() + get_realtime_trading_guide() + this real_time_data section = complete reference.",
                    "Snapshot tool = get_latest_ws_messages or the new get_realtime_market_snapshot (does WS + CLOB public in one). Use parse_ws_event(raw) on any events from listen/get_latest for clean typed output (book, price_change, trade, order, sports). See realtime_helpers.py."
                ]
            },
            "websocket_support": {
                "description": "Fully managed background WebSocket connections for the three official Polymarket channels (Market, User, Sports). This is a major advantage over raw SDK usage (py-clob-client-v2 or direct websockets): the MCP owns the entire connection lifecycle, exponential backoff + jitter reconnect, credential re-fetch + re-auth (User), automatic re-subscription, content-hashed deduplication, timestamped ring buffers with age pruning, and health metrics. Agents never manage async tasks, pings, or reconnect state machines inside the stdio MCP request loop.",
                "channels": {
                    "market": "Public. Real-time orderbook depth (level 2), price_change, last_trade_price, trades, and best bid/ask for subscribed tokens. identifiers param accepts clobTokenIds, slugs, or condition_ids (auto-resolved via Gamma inside the MCP).",
                    "user": "Authenticated. Real-time order lifecycle, fills, trades, and cancellations for the API key's account. Requires CLOB_API_KEY/SECRET/PASSPHRASE (same as trading). Optional markets filter (condition IDs).",
                    "sports": "Public. Live sports scores, event updates, and related data for sports-linked markets. Lower volume; subscribe by leagues or all."
                },
                "core_managed_tools": [
                    "connect_market_websocket / connect_user_websocket / connect_sports_websocket (base)",
                    "HIGH-LEVEL STARTERS (recommended 95% of cases): start_full_market_monitor, watch_market_by_slug, watch_markets_by_query, auto_subscribe_popular_markets, start_realtime_market_watcher",
                    "DYNAMIC CONTROL: update_market_subscription / update_user_subscription / update_sports_subscription, pause_websocket / resume_websocket",
                    "disconnect_market_websocket / disconnect_user_websocket / disconnect_sports_websocket (rare)",
                    "STATUS & HEALTH: get_websocket_status() (lightweight snapshot), get_connection_health(channel) (deep diagnostics)",
                    "CONSUMPTION (snapshot + reactive): listen_for_ws_events (event-driven, wait_for_event_type, return_immediately), get_latest_ws_messages (zero-wait buffer poll / snapshot tool with filters)",
                    "NEW STRUCTURED OUTPUT: parse_ws_event(raw_message) — returns clean typed dicts (event_type + normalized fields + specific payload) for book/price_change/trade/order/sports etc. (in realtime_helpers.py, surfaced by get_realtime_helper_patterns)",
                    "HIGH-LEVEL SNAPSHOT: get_realtime_market_snapshot(identifiers) — one call does WS connect/reuse + latest WS events (parsed) + full public CLOB snapshots (orderbook + price + spread + trades)",
                    "PATTERNS & GUIDE: get_realtime_helper_patterns() (copy-paste loops + parse helpers), get_realtime_trading_guide() (full workflow)"
                ],
                "consumption_tools": {
                    "listen_for_ws_events": "The highest-signal real-time consumption primitive. Waits up to timeout_seconds (default 8.0, keep short for stdio responsiveness) for new messages to arrive on the channel. Powerful options: event_types filter list, wait_for_event_type (blocks until that exact type appears), return_immediately=True (instant buffer snapshot, no wait). Only returns messages with _received_at after the call (freshness). Closest approximation to server-push notifications available to agents.",
                    "get_latest_ws_messages": "Fast non-blocking poll of the recent buffer. Supports limit, event_types filter, and asset_id filter. Ideal for quick on-demand snapshots or tight polling loops when you do not want to block at all."
                },
                "listen_vs_polling": "Use get_latest_ws_messages() for immediate, zero-wait access to whatever has already buffered (best for 'what just happened?' checks or low-latency loops). Use listen_for_ws_events() when your logic benefits from a short controlled wait for the next relevant event instead of busy-polling REST or the buffer — especially with wait_for_event_type or event_types to react to price moves, your fills, or specific book updates. listen_for_ws_events is the tool for event-driven agent flows.",
                "auto_reconnect_behavior": "Production-grade resilience on every channel. _connection_loop + _recv_loop automatically recover from disconnects (network blips, server side, idle timeouts). Exponential backoff with jitter (1s → 60s cap). On successful reconnect: re-creates the WS, re-sends the prior subscription payload (assets/markets/leagues), and for User channel re-fetches fresh creds from env/config and performs full re-auth. subscribed sets + _connect_params survive across drops. reconnect_count, last_error, and last_message_time are always available via status/health. Dedup hash window prevents replayed or duplicate messages post-reconnect.",
                "market_channel_example": "connect_market_websocket(identifiers=['will-trump-win-2024', '0x8f3a...conditionId'])\n# later, non-blocking snapshot:\nget_latest_ws_messages(channel='market', event_types=['price_change', 'last_trade_price'], limit=15)\n# or wait briefly for a specific update:\nlisten_for_ws_events(channel='market', timeout_seconds=10.0, wait_for_event_type='book', event_types=['book'])",
                "user_channel_example": "connect_user_websocket()  # all your markets, or pass condition IDs\n# reactive to your activity:\nlisten_for_ws_events(channel='user', timeout_seconds=12.0, event_types=['trade', 'order'], wait_for_event_type='trade')",
                "advantage_over_raw_sdk": "Raw direct WebSocket usage (or even the low-level parts of py-clob-client-v2 or the new unified polymarket-client) requires the caller to implement persistent asyncio tasks, manual ping/pong, full reconnect + resubscribe state machines, credential handling, dedup logic, and safe consumption from a background thread or task while the MCP stdio loop runs. Most agents and harnesses cannot do this reliably or at all. polymarket-alpha turns the three official channels into fire-and-forget managed services + two trivial consumption tools. This is one of the strongest practical advantages of the MCP versus calling the underlying SDK (v2 or unified) or raw WS endpoints directly from an agent. See get_unified_sdk_guidance() for SDK options.",
                "usage_notes": [
                    "Connect once early in the session for the tokens/markets you care about. The connections live for the entire MCP process lifetime.",
                    "User channel requires the same CLOB_* credentials as place_limit_order / check_clob_auth.",
                    "Keep listen_for_ws_events timeouts short (5-15s typical) to avoid blocking the agent.",
                    "Call get_connection_health('market') or 'user' if last_message_age grows unexpectedly or to inspect backoff state.",
                    "Sports channel is useful for automated in-play sports market strategies but carries lower message volume.",
                    "For complete agent guidance: call get_realtime_trading_guide() and get_realtime_helper_patterns(). NEW: get_realtime_market_snapshot(identifiers) for instant combined real-time + CLOB public view; use parse_ws_event(raw) (realtime_helpers) on all WS events for clean normalized/specific typed dicts. See real_time_data section above."
                ]
            },
            "sdk_evolution_and_guidance": {
                "current_implementation": "This MCP implements CLOB surface using the mature py-clob-client-v2 (plus polymarket-apis for rich positions and gasless).",
                "official_direction": "Polymarket recommends the new unified `polymarket-client` SDK (beta) for new work. It provides a single cohesive client for market discovery, CLOB trading, realtime streams, account data, and gasless operations.",
                "action_for_agents": "Call get_unified_sdk_guidance() for the full decision matrix, migration notes, install instructions (`pip install --pre polymarket-client`), and copy-paste examples for both PublicClient/SecureClient and the current v2 path.",
                "mcp_stance": "The MCP high-level tools (Gamma → get_clob_token_ids → CLOB trading + fully managed realtime + gasless_*) are the recommended path for agent harnesses regardless of which raw SDK is used underneath. Raw drops via get_client() target the current v2 client only.",
                "when_raw_sdk_makes_sense": "Only when building external code, custom low-level logic, or preparing for future unified parity. Always read live docs via get_polymarket_llms_txt() first."
            }
        }
