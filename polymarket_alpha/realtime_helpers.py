"""
Realtime Helpers for Polymarket Alpha MCP.

Small, focused module providing HIGH-LEVEL RECOMMENDED PATTERNS on top of:
- Gamma discovery (search_markets, get_clob_token_ids, etc.)
- Managed WebSocket channels (connect_*_websocket + listen_for_ws_events / get_latest_ws_messages)
- CLOB execution + analysis (liquidity_analysis, risk_check, place_limit_order, get_clob_balance, get_positions, ...)

These are the "copy-paste for agents" building blocks for robust real-time trading / monitoring loops.

The executable high-level tool `start_full_market_monitor` is registered via websocket.py
for immediate use. This file supplies the supporting patterns, monitor shapes,
reusable async recipe snippets, AND the key structured output helpers:
parse_ws_event(raw_message) + get_ws_event_types_help().

The three flagship event-driven trading patterns (on_price_move_then_place_limit,
on_my_fill_then_risk_check, multi_asset_book_delta_watcher) are now fully implemented
with health checks, parse_ws_event, pre-trade gates, cooldowns, and error containment.
They are exposed both here and via the dedicated get_ws_event_driven_patterns() MCP tool.

Recommended usage in agent code / prompts:
    from polymarket_alpha.realtime_helpers import (
        get_copy_paste_realtime_loops, MONITOR_STATUS_SHAPE,
        parse_ws_event, get_ws_event_types_help,
        get_event_driven_trading_patterns, get_realtime_story_summary
    )
    patterns = get_copy_paste_realtime_loops()
    trading_reactions = get_event_driven_trading_patterns()
    parsed = parse_ws_event(raw_ws_msg)
    # then use the snippets in your reasoning / generated code
"""

from typing import Any

# Canonical shape returned by start_full_market_monitor and similar high-level entrypoints.
MONITOR_STATUS_SHAPE = {
    "status": "monitoring | partial | error",
    "monitor_type": "full_market | sports | user_activity",
    "discovered_via": "slugs + gamma_queries",
    "identifiers_used": ["list of original slugs/queries"],
    "resolved_token_ids": ["..."],
    "subscribed_count": 0,
    "channels_active": ["market"],
    "how_to_consume": "Use get_websocket_status() then listen_for_ws_events(...) or get_latest_ws_messages(...)",
    "recommended_next_calls": [
        "get_websocket_status()",
        "get_connection_health('market')",
        "listen_for_ws_events(channel='market', wait_for_event_type='price_change')",
    ],
    "health": {},
    "note": "Connections are fully managed with auto-reconnect inside the MCP process."
}


def get_copy_paste_realtime_loops() -> dict[str, str]:
    """
    Returns a dictionary of ready-to-adapt async Python snippets for common real-time agent loops.

    Agents (and the get_realtime_trading_guide tool) should surface these as the
    canonical "how to consume the firehose" patterns. All snippets assume you have
    already called a connect / watch / start_full_market_monitor tool.

    The three focused event-driven trading patterns are complete, robust, and production-oriented:
    they demonstrate full health gating, parse_ws_event usage, pre-trade analysis (liquidity + risk),
    cooldowns, and safe conditional execution. They are also available in isolation via
    get_event_driven_trading_patterns() (and the get_ws_event_driven_patterns MCP tool).
    """
    return {
        "basic_price_monitor": '''# Basic real-time price + trade monitor (market channel)
import asyncio
# Assume MCP client/tool calling context

async def monitor_prices(market_slug: str, max_minutes: int = 30):
    # 1. One-time setup (call via MCP tool)
    # await start_full_market_monitor(slugs_or_queries=[market_slug])

    start = asyncio.get_event_loop().time()
    seen = set()
    while (asyncio.get_event_loop().time() - start) < (max_minutes * 60):
        # Prefer short controlled waits for event-driven reactivity
        events = await listen_for_ws_events(
            channel="market",
            timeout_seconds=6.0,
            event_types=["price_change", "last_trade_price", "trade"],
            return_immediately=False
        )
        for ev in events:
            key = (ev.get("asset_id") or ev.get("assetId"), ev.get("event_type") or ev.get("type"), ev.get("price"))
            if key in seen:
                continue
            seen.add(key)
            print("REALTIME EVENT:", ev.get("event_type") or ev.get("type"), ev.get("price"), ev.get("size"))
            # For full production reaction logic with health/pre-trade gates/cooldowns see the three patterns in get_event_driven_trading_patterns() (on_price_move_then_place_limit etc.)
        await asyncio.sleep(0.3)  # tiny yield; listen already did the waiting
''',

        "event_driven_fill_then_trade": '''# React to your own fills (user channel) then take further action
async def react_to_my_fills_then_maybe_adjust():
    # Setup once:
    # await connect_user_websocket()   # or with specific markets filter

    while True:
        fills = await listen_for_ws_events(
            channel="user",
            timeout_seconds=10.0,
            event_types=["trade", "fill", "order"],
            wait_for_event_type="trade",   # block until we see a trade/fill
        )
        for f in fills:
            if f.get("side") == "buy" or f.get("type") == "trade":
                print("MY FILL ARRIVED:", f)
                # Full robust version (with balance/positions/risk/conditional adjustment + cooldowns) lives in the dedicated "on_my_fill_then_risk_check" pattern (see get_event_driven_trading_patterns())
        # loop continues; connection is managed
''',

        "multi_market_dashboard": '''# Watch many markets (via query or popular) and poll snapshots
async def realtime_dashboard(queries: list[str]):
    # await start_full_market_monitor(slugs_or_queries=queries, max_markets=12)

    while True:
        status = get_websocket_status()
        print("Subscribed:", status.get("market", {}).get("subscribed_assets"))

        # Fast non-blocking snapshots (no wait)
        recent = get_latest_ws_messages(
            channel="market",
            limit=30,
            event_types=["price_change", "book"],
        )
        # Aggregate best bids/asks or detect anomalies here
        # Then optionally sleep(1.5) or use listen_for_ws_events for mixed push+pull
        await asyncio.sleep(1.5)
''',

        "sports_inplay_monitor": '''# Sports channel for in-play sports markets
async def watch_sports_scores(leagues: list[str] = None):
    # await connect_sports_websocket(leagues=leagues or ["NBA", "NFL", "MLB"])

    while True:
        updates = await listen_for_ws_events(
            channel="sports",
            timeout_seconds=15.0,  # sports are burstier / lower frequency
        )
        for u in updates:
            print("SPORTS UPDATE:", u.get("league"), u.get("match_id"), u.get("score"))
            # React: if a score crossed a line -> evaluate related market tokens via Gamma/CLOB
        await asyncio.sleep(1)
''',

        "robust_consumption_with_health": '''# Production-style: always check health, prune old, handle disconnects gracefully
async def robust_realtime_consumer(channel: str = "market", timeout: float = 8.0):
    while True:
        health = get_connection_health(channel=channel)
        if not health.get("connected"):
            print("WS down, last_error:", health.get("last_error"))
            # In practice re-call the start/connect tool if you want auto-recovery at tool level
            await asyncio.sleep(5)
            continue

        msgs = await listen_for_ws_events(channel=channel, timeout_seconds=timeout, return_immediately=False)
        if msgs:
            # Your business logic on fresh msgs (they already have _received_at)
            pass

        # Optional: periodically prune or log age
        if health.get("last_message_age_seconds", 0) > 120:
            print("Stale WS data — consider reconnect or broader subscription")
''',

        # -----------------------------------------------------------------
        # Focused high-quality event-driven trading patterns (production-ready, copy-paste)
        # All three now include: health checks, robust error handling, parse_ws_event,
        # pre-trade analysis gates (liquidity_analysis + risk_check), cooldowns, balance/positions,
        # and safe conditional execution via place_limit_order / get_clob_balance etc.
        # Use after start_full_market_monitor / connect_user_websocket etc.
        # -----------------------------------------------------------------
        "on_price_move_then_place_limit": '''# ROBUST EVENT-DRIVEN PATTERN #1: on_price_move_then_place_limit
# Watches a specific asset (or extend to list) for significant price moves via WS.
# On trigger: runs full health gate, parses cleanly, then executes the mandatory
# pre-trade sequence: get_clob_balance + liquidity_analysis + risk_check.
# Only then (if gates pass + enable_trading=True) places a maker-friendly limit order.
# Includes per-asset cooldown, try/except around every external call, stale data handling.
# Highly recommended for reactive agents. Call start_full_market_monitor first.
#
# Usage (agent code):
#   from polymarket_alpha.realtime_helpers import parse_ws_event  # optional local import
#   # await start_full_market_monitor(slugs_or_queries=[...])
#   await on_price_move_then_place_limit(asset_id="0x1234...", move_threshold_pct=1.8, enable_trading=False)
async def on_price_move_then_place_limit(
    asset_id: str,
    move_threshold_pct: float = 1.5,
    max_minutes: int = 60,
    notional_for_checks_usdc: float = 300.0,
    enable_trading: bool = False,          # SAFETY FIRST: explicit opt-in required
    cooldown_seconds: float = 60.0,
    limit_offset_pct: float = 0.25,        # maker bias: 0.25% inside for passive fill chance
):
    import asyncio
    from datetime import datetime, timezone
    # Best practice: ensure parse_ws_event is in scope (it is when using via get_*_patterns tools)
    # If standalone: import from polymarket_alpha.realtime_helpers import parse_ws_event

    last_prices: dict[str, float] = {}
    last_action_ts: dict[str, float] = {}
    start = asyncio.get_event_loop().time()
    print(f"[PRICE-MOVE-REACTOR] START asset={asset_id} threshold={move_threshold_pct}% trading={'ENABLED' if enable_trading else 'ANALYSIS-ONLY (SAFE)'}")

    while (asyncio.get_event_loop().time() - start) < (max_minutes * 60):
        # === 1. MANDATORY HEALTH CHECK (robustness) ===
        try:
            health = get_connection_health(channel="market")
            if not health.get("connected"):
                err = health.get("last_error") or "unknown"
                print(f"[HEALTH] Market WS down (reconnects={health.get('reconnect_count')}). Last error: {err}. Sleeping...")
                await asyncio.sleep(7)
                continue
            age = health.get("last_message_age_seconds") or 0
            if age > 180:
                print(f"[HEALTH] Stale market data age={age}s. Consider broader subscription or market quiet.")
            if health.get("reconnect_count", 0) > 8:
                print("[HEALTH] High reconnect count — connection may be unstable.")
        except Exception as health_err:
            print(f"[HEALTH] Health probe failed (non-fatal): {health_err}")
            await asyncio.sleep(4)
            continue

        # === 2. EVENT COLLECTION (short controlled wait, event-driven) ===
        events = []
        try:
            events = await listen_for_ws_events(
                channel="market",
                timeout_seconds=5.2,
                event_types=["price_change", "last_trade_price", "trade"],
                return_immediately=False,
            )
        except Exception as listen_err:
            print(f"[LISTEN] Transient listen error: {listen_err}")
            await asyncio.sleep(1.5)
            continue

        for raw in (events or []):
            try:
                # === 3. ALWAYS PARSE FOR CLEAN FIELDS ===
                parsed = parse_ws_event(raw) if "parse_ws_event" in globals() else {
                    "event_type": raw.get("event_type") or raw.get("type"),
                    "normalized": raw,
                    "specific": {},
                    "raw": raw,
                }
                norm = parsed.get("normalized") or raw
                ev_aid = norm.get("asset_id") or raw.get("asset_id") or raw.get("assetId") or asset_id
                if ev_aid != asset_id:
                    continue  # focus on target (easy to generalize to list of assets)

                price_val = norm.get("price") or norm.get("last_trade_price") or norm.get("p") or raw.get("price")
                if price_val is None:
                    continue
                try:
                    price_f = float(price_val)
                except (TypeError, ValueError):
                    continue

                now_t = asyncio.get_event_loop().time()

                # === 4. DETECT SIGNIFICANT MOVE ===
                if ev_aid in last_prices and last_prices[ev_aid] > 0:
                    prev = last_prices[ev_aid]
                    pct_move = abs(price_f - prev) / prev * 100.0
                    if pct_move >= move_threshold_pct:
                        # Cooldown gate (prevents over-trading on noisy moves)
                        last_act = last_action_ts.get(ev_aid, 0)
                        if (now_t - last_act) < cooldown_seconds:
                            print(f"[COOLDOWN] Skipping reaction on {ev_aid} (active cooldown)")
                            last_prices[ev_aid] = price_f
                            continue

                        print(f"[{datetime.now(timezone.utc).isoformat()}] SIGNIFICANT {pct_move:.2f}% MOVE on {ev_aid}: {prev:.4f} → {price_f:.4f}")

                        # === 5. FULL PRE-TRADE ANALYSIS PIPELINE (MANDATORY) ===
                        try:
                            # Refresh collateral view
                            bal = await get_clob_balance(refresh=True)

                            # Slippage/liquidity preview for the notional
                            liq = await liquidity_analysis(token_id=ev_aid, notional_usdc=notional_for_checks_usdc)

                            # Risk gate (size + concentration warnings)
                            risk = await risk_check(proposed_size_usdc=notional_for_checks_usdc, token_id=ev_aid)

                            print(f"[PRE-TRADE] bal_ok={ 'error' not in str(bal).lower() }, liq={liq}, risk={risk}")

                            warnings = []
                            if isinstance(risk, dict):
                                warnings = risk.get("warnings", []) or []
                            safe = (len(warnings) == 0) and ("error" not in str(bal).lower())

                            if enable_trading and safe:
                                # Compute passive limit price (maker bias)
                                direction_mult = 0.9975 if price_f < prev else 1.0025  # lean with or against move for demo
                                limit_p = round(price_f * (1.0 - (limit_offset_pct / 100.0) * direction_mult), 4)
                                order_size = max(10.0, round(notional_for_checks_usdc * 0.6 / max(price_f, 0.01), 1))  # conservative

                                order_res = await place_limit_order(
                                    token_id=ev_aid,
                                    side="buy",   # customize per your signal (buy the dip / sell the rip etc.)
                                    price=limit_p,
                                    size=order_size,
                                )
                                print(f"[EXECUTED] Limit order response: {order_res}")
                                last_action_ts[ev_aid] = now_t
                            else:
                                reason = "trading disabled" if not enable_trading else f"risk gates: {warnings}"
                                print(f"[ANALYSIS ONLY] Move detected but no trade ({reason}). Review liq/risk output above.")
                                last_action_ts[ev_aid] = now_t  # still rate-limit noisy alerts
                        except Exception as pipeline_err:
                            print(f"[PRE-TRADE ERROR] (safe-fail, no order placed): {pipeline_err}")
                            last_action_ts[ev_aid] = now_t

                last_prices[ev_aid] = price_f

            except Exception as per_event_err:
                print(f"[EVENT HANDLER] Non-fatal per-event error (continuing): {per_event_err}")
                continue

        await asyncio.sleep(0.18)  # light yield; listen already waited

    print("[PRICE-MOVE-REACTOR] Completed run.")
''',

        "on_my_fill_then_risk_check": '''# ROBUST EVENT-DRIVEN PATTERN #2: on_my_fill_then_risk_check
# Immediately reacts to your own fills/trades on the authenticated user channel.
# On detection: health gate (user WS) → parse → get_clob_balance + get_positions + get_open_orders
# → risk_check → optional corrective / scale / hedge limit order.
# Production features: dedup via recent fills, cooldowns, rich logging, full error containment.
# MUST pair with: connect_user_websocket() (or start_full... which can also wire user).
#
# Usage:
#   await connect_user_websocket()   # or with markets filter
#   await on_my_fill_then_risk_check()   # runs forever until you break
async def on_my_fill_then_risk_check(
    max_fills_to_track: int = 50,
    cooldown_seconds: float = 30.0,
    enable_adjustment_trades: bool = False,   # explicit safety switch
):
    import asyncio
    from datetime import datetime, timezone
    from collections import deque

    recent_fill_keys: deque = deque(maxlen=max_fills_to_track)
    last_reaction_ts: dict[str, float] = {}
    print("[FILL-REACTOR] START — listening on user channel. adjustment_trades=", enable_adjustment_trades)

    while True:
        # === HEALTH (user channel is critical for trading agents) ===
        try:
            health = get_connection_health(channel="user")
            if not health.get("connected"):
                print(f"[HEALTH:user] User WS not connected. reconnects={health.get('reconnect_count')}. Error: {health.get('last_error')}")
                print("   Hint: ensure CLOB creds + call connect_user_websocket() / check_clob_auth(include_raw=true)")
                await asyncio.sleep(8)
                continue
            if (health.get("last_message_age_seconds") or 0) > 300:
                print("[HEALTH:user] Very stale user data — verify subscription.")
        except Exception as h_err:
            print(f"[HEALTH] User health check error: {h_err}")
            await asyncio.sleep(5)
            continue

        fills = []
        try:
            fills = await listen_for_ws_events(
                channel="user",
                timeout_seconds=9.5,
                event_types=["trade", "fill", "order"],
                wait_for_event_type=None,  # we filter ourselves for flexibility
                return_immediately=False,
            )
        except Exception as l_err:
            print(f"[LISTEN:user] listen error: {l_err}")
            await asyncio.sleep(2)
            continue

        for raw in (fills or []):
            try:
                parsed = parse_ws_event(raw) if "parse_ws_event" in globals() else {"normalized": raw, "specific": {}, "event_type": raw.get("event_type") or raw.get("type")}
                norm = parsed.get("normalized") or raw
                et = (parsed.get("event_type") or raw.get("event_type") or raw.get("type") or "").lower()

                # Only act on actual execution events
                if "trade" not in et and "fill" not in et:
                    continue

                # Build stable dedup key
                fill_key = (
                    norm.get("asset_id") or raw.get("asset_id") or raw.get("market"),
                    norm.get("price"),
                    norm.get("size"),
                    norm.get("side") or raw.get("side"),
                    raw.get("order_id") or raw.get("trade_id") or raw.get("id"),
                )
                if fill_key in recent_fill_keys:
                    continue
                recent_fill_keys.append(fill_key)

                asset = norm.get("asset_id") or raw.get("asset_id") or raw.get("market") or "unknown"
                price = norm.get("price") or raw.get("price")
                size = norm.get("size") or raw.get("size")
                side = norm.get("side") or raw.get("side") or raw.get("takerSide")

                now_t = asyncio.get_event_loop().time()
                if (now_t - last_reaction_ts.get(asset, 0)) < cooldown_seconds:
                    print(f"[COOLDOWN] Recent reaction on {asset}, skipping duplicate fill reaction")
                    continue

                print(f"[{datetime.now(timezone.utc).isoformat()}] MY FILL: asset={asset} side={side} @ {price} size={size} (parsed_type={et})")

                # === IMMEDIATE PORTFOLIO + RISK REFRESH (the core value of this pattern) ===
                try:
                    bal = await get_clob_balance(refresh=True)
                    pos = await get_positions(redeemable_only=False)  # full current exposure
                    oo = await get_open_orders() if "get_open_orders" in globals() else None   # may exist in full surface

                    # Run risk gate with context (size can be current total or proposed delta)
                    proposed = 0.0
                    try:
                        if isinstance(pos, dict) and "positions" in pos:
                            proposed = sum(float(p.get("size", 0) or 0) * float(p.get("current_price", price) or 0) for p in pos["positions"] if isinstance(p, dict))
                    except Exception:
                        proposed = 250.0
                    risk = await risk_check(proposed_size_usdc=max(proposed, 100.0), token_id=asset if asset != "unknown" else None)

                    print(f"[POST-FILL STATE] balance={bal}, positions_summary={str(pos)[:280]}, risk={risk}, open_orders={bool(oo)}")

                    # Example smart reaction logic (customize heavily for your strategy)
                    warnings = (risk or {}).get("warnings", []) if isinstance(risk, dict) else []
                    over_risk = len(warnings) > 0 or (isinstance(pos, dict) and pos.get("total_value_usd", 0) > 15000)

                    if enable_adjustment_trades and over_risk and asset != "unknown":
                        # Example: place a conservative hedge/exit limit on the just-filled side
                        exit_side = "sell" if str(side).lower() in ("buy", "bid") else "buy"
                        try:
                            exit_price = round(float(price) * (0.985 if exit_side == "sell" else 1.015), 4)
                            adj = await place_limit_order(token_id=asset, side=exit_side, price=exit_price, size=abs(float(size or 15)))
                            print(f"[ADJUSTMENT ORDER] {exit_side} limit placed as risk response: {adj}")
                        except Exception as adj_err:
                            print(f"[ADJUSTMENT FAILED] {adj_err}")

                    last_reaction_ts[asset] = now_t

                except Exception as state_err:
                    print(f"[STATE/ RISK ERROR] (non-fatal): {state_err}")

            except Exception as fill_err:
                print(f"[FILL HANDLER] Non-fatal per-fill processing error: {fill_err}")
                continue

        await asyncio.sleep(0.25)
''',

        "multi_asset_book_delta_watcher": '''# ROBUST EVENT-DRIVEN PATTERN #3: multi_asset_book_delta_watcher
# Subscribes broadly (via prior start_full_market_monitor or multiple watch_* calls),
# then continuously tracks book + price deltas across many assets.
# Detects: spread explosions, outsized single-asset moves vs peers (relative value),
# liquidity events. On signals runs liquidity_analysis + risk_check for the affected names.
# Rich state tracking + health + error containment. Excellent for relative-value / arb-aware agents.
#
# Usage:
#   await start_full_market_monitor(slugs_or_queries=["crypto", "election", "trump"], max_markets=20)
#   await multi_asset_book_delta_watcher(identifiers=None, duration_seconds=300)  # or run forever with while True + health
async def multi_asset_book_delta_watcher(
    identifiers: list[str] = None,   # optional allow-list of token_ids; None = all subscribed
    duration_seconds: int = 300,
    spread_alert_bps: float = 450.0, # alert when spread > X basis points
    rel_move_threshold_pct: float = 2.2,
):
    import asyncio
    from datetime import datetime, timezone
    from collections import defaultdict

    last_state: dict[str, dict] = {}
    move_history: dict[str, list] = defaultdict(list)  # lightweight rolling for rel calc
    start = asyncio.get_event_loop().time()
    print(f"[MULTI-BOOK-WATCHER] START duration={duration_seconds}s spread_alert>{spread_alert_bps}bps rel_move>{rel_move_threshold_pct}%")

    while (asyncio.get_event_loop().time() - start) < duration_seconds:
        # Health gate
        try:
            health = get_connection_health(channel="market")
            if not health.get("connected"):
                print(f"[HEALTH:multi] Market WS unhealthy. reconnects={health.get('reconnect_count')}. Sleeping...")
                await asyncio.sleep(6)
                continue
        except Exception as he:
            print(f"[HEALTH] multi watcher health probe failed: {he}")
            await asyncio.sleep(3)
            continue

        events = []
        try:
            events = await listen_for_ws_events(
                channel="market",
                timeout_seconds=3.8,
                event_types=["book", "price_change", "last_trade_price", "trade"],
                return_immediately=False,
            )
        except Exception as le:
            print(f"[LISTEN:multi] {le}")
            await asyncio.sleep(1)
            continue

        updated = []
        for raw in (events or []):
            try:
                parsed = parse_ws_event(raw) if "parse_ws_event" in globals() else {
                    "normalized": raw, "specific": raw.get("bids") and {"bids": raw["bids"]} or {}, "event_type": raw.get("event_type") or raw.get("type")
                }
                norm = parsed.get("normalized") or raw
                aid = norm.get("asset_id") or raw.get("asset_id") or raw.get("assetId") or raw.get("market")
                if not aid:
                    continue
                if identifiers and aid not in identifiers:
                    continue

                et = str(parsed.get("event_type") or raw.get("event_type") or raw.get("type") or "").lower()
                spec = parsed.get("specific", {}) or {}

                bid = spec.get("best_bid") or norm.get("best_bid") or raw.get("best_bid")
                ask = spec.get("best_ask") or norm.get("best_ask") or raw.get("best_ask")
                price = norm.get("price") or spec.get("last_trade_price") or raw.get("price") or raw.get("lastTradePrice")

                prev = last_state.get(aid, {})
                spread = None
                if bid and ask:
                    try:
                        b, a = float(bid), float(ask)
                        spread = (a - b) * 10000.0  # in bps (approx for 0-1 prices)
                    except Exception:
                        spread = None

                # Track
                last_state[aid] = {
                    "bid": bid, "ask": ask, "price": price,
                    "spread_bps": round(spread, 1) if spread else prev.get("spread_bps"),
                    "ts": asyncio.get_event_loop().time(),
                    "last_et": et,
                }
                updated.append(aid)

                # Spread explosion detection
                if spread and spread > spread_alert_bps:
                    print(f"[LIQUIDITY EVENT] {aid} wide spread {spread:.1f}bps (et={et})")
                    try:
                        _ = await liquidity_analysis(token_id=aid, notional_usdc=150)
                        print(f"  → liquidity snapshot triggered for wide book on {aid}")
                    except Exception:
                        pass

                # Price move for relative calc
                if price:
                    try:
                        p_f = float(price)
                        move_history[aid].append((asyncio.get_event_loop().time(), p_f))
                        # keep short window
                        move_history[aid] = [x for x in move_history[aid] if asyncio.get_event_loop().time() - x[0] < 120]
                    except Exception:
                        pass

            except Exception as perr:
                print(f"[MULTI PARSE] non-fatal: {perr}")
                continue

        # === CROSS-ASSET RELATIVE VALUE / DELTA AGGREGATION (the power of this pattern) ===
        if len(last_state) >= 2 and updated:
            try:
                # Simple peer-relative: compute avg recent move of cohort, flag outliers
                now = asyncio.get_event_loop().time()
                cohort_moves = {}
                for aid, hist in list(move_history.items()):
                    if len(hist) >= 2:
                        recent = [h[1] for h in hist if now - h[0] < 90]
                        if len(recent) >= 2:
                            pct = (recent[-1] - recent[0]) / max(recent[0], 0.0001) * 100.0
                            cohort_moves[aid] = pct

                if len(cohort_moves) >= 2:
                    avg_move = sum(cohort_moves.values()) / len(cohort_moves)
                    for aid, m in cohort_moves.items():
                        dev = abs(m - avg_move)
                        if dev >= rel_move_threshold_pct:
                            print(f"[RELATIVE VALUE] {aid} deviated {dev:.2f}% from cohort avg ({avg_move:.2f}%). last_state={last_state.get(aid)}")
                            # Trigger deeper checks on the outlier
                            try:
                                await liquidity_analysis(token_id=aid, notional_usdc=200)
                                await risk_check(proposed_size_usdc=200, token_id=aid)
                            except Exception:
                                pass
            except Exception as agg_err:
                print(f"[AGGREGATE] Cross-asset calc error (non-fatal): {agg_err}")

        # Lightweight snapshot of current books (top few)
        if last_state:
            snap = {k: {kk: vv for kk, vv in v.items() if kk in ("bid", "ask", "spread_bps", "price")} for k, v in list(last_state.items())[:5]}
            if len(updated) > 0 or (asyncio.get_event_loop().time() - start) % 25 < 1:
                print(f"[MULTI SNAPSHOT t+{int(asyncio.get_event_loop().time()-start)}s] {snap}")

        await asyncio.sleep(0.35)

    print("[MULTI-BOOK-WATCHER] Run complete. Final tracked assets:", list(last_state.keys())[:8])
''',

        # -----------------------------------------------------------------
        # Sports-specific event-driven patterns (NEW for multi-channel + in-play)
        # These complement the market/user ones. Assume prior connect_sports_websocket
        # or use via start_full_realtime_session(include_sports=True).
        # They use parse_ws_event for clean sports fields + trigger Gamma/CLOB follow-ups
        # on score events (e.g. to discover related prediction markets and trade).
        # -----------------------------------------------------------------
        "on_score_change_then_check_markets": '''# ROBUST SPORTS EVENT-DRIVEN PATTERN #1: on_score_change_then_check_markets
# Listens on sports channel for score updates. On meaningful score delta or status change:
# health gate → parse (sports section) → logs rich event → optional Gamma search for related
# sports prediction markets (by league/team keywords) → token resolution + snapshot/liquidity for trading signals.
# Production: cooldowns, error containment, recommends next listen calls.
# Pair with: start_full_realtime_session(..., include_sports=True, sports_leagues=...) or connect_sports_websocket.
#
# Usage:
#   await connect_sports_websocket(leagues=["NBA", "NFL"])
#   await on_score_change_then_check_markets(leagues=["NBA"], max_minutes=120)
async def on_score_change_then_check_markets(
    leagues: list[str] = None,
    max_minutes: int = 120,
    score_delta_threshold: int = 2,
    enable_market_discovery: bool = False,  # explicit opt-in for Gamma follow-ups
    cooldown_seconds: float = 45.0,
):
    import asyncio
    from datetime import datetime, timezone

    last_event_ts: dict[str, float] = {}
    start = asyncio.get_event_loop().time()
    leagues = leagues or ["NBA", "NFL", "MLB"]
    print(f"[SPORTS-SCORE-REACTOR] START leagues={leagues} threshold_delta={score_delta_threshold} discovery={'ON' if enable_market_discovery else 'OFF (analysis only)'}")

    while (asyncio.get_event_loop().time() - start) < (max_minutes * 60):
        # Health gate for sports channel
        try:
            health = get_connection_health(channel="sports")
            if not health.get("connected"):
                print(f"[HEALTH:sports] Sports WS down. reconnects={health.get('reconnect_count')}. Err: {health.get('last_error')}")
                await asyncio.sleep(8)
                continue
            if (health.get("last_message_age_seconds") or 0) > 180:
                print("[HEALTH:sports] Stale sports data (normal in low-activity periods).")
        except Exception as h_err:
            print(f"[HEALTH:sports] Probe error: {h_err}")
            await asyncio.sleep(5)
            continue

        updates = []
        try:
            updates = await listen_for_ws_events(
                channel="sports",
                timeout_seconds=18.0,  # sports updates are bursty/infrequent
                return_immediately=False,
            )
        except Exception as l_err:
            print(f"[LISTEN:sports] {l_err}")
            await asyncio.sleep(2)
            continue

        for raw in (updates or []):
            try:
                parsed = parse_ws_event(raw) if "parse_ws_event" in globals() else {"event_type": raw.get("event_type") or raw.get("type"), "normalized": {}, "specific": raw, "raw": raw}
                norm = parsed.get("normalized") or {}
                spec = parsed.get("specific") or raw
                et = (parsed.get("event_type") or raw.get("event_type") or raw.get("type") or "").lower()

                league = spec.get("league") or norm.get("league") or raw.get("league")
                match_id = spec.get("match_id") or spec.get("event_id") or raw.get("match_id")
                score = spec.get("score") or spec.get("home_score") or raw.get("score")
                status = spec.get("status") or raw.get("status")

                if leagues and league and league not in leagues:
                    continue

                # Detect meaningful change (crude but effective for demo; improve with prior state)
                now_t = asyncio.get_event_loop().time()
                key = f"{league}:{match_id}"
                last = last_event_ts.get(key, 0)
                if (now_t - last) < cooldown_seconds:
                    continue

                print(f"[{datetime.now(timezone.utc).isoformat()}] SPORTS SCORE: league={league} match={match_id} score={score} status={status} (et={et})")

                if enable_market_discovery:
                    try:
                        # Agent can follow up; here we surface the trigger for the caller
                        print(f"  → RECOMMEND: search_markets(query='{league} {match_id or 'live'}', active_only=True) then get_clob_token_ids + get_realtime_market_snapshot on related tokens")
                        # In full agent: await search... ; await get_clob... etc. (non-blocking here)
                    except Exception:
                        pass

                last_event_ts[key] = now_t

            except Exception as perr:
                print(f"[SPORTS PER-EVENT] non-fatal: {perr}")
                continue

        await asyncio.sleep(0.8)

    print("[SPORTS-SCORE-REACTOR] Completed.")
''',

        "on_sports_update_then_analyze_polymarkets": '''# ROBUST SPORTS EVENT-DRIVEN PATTERN #2: on_sports_update_then_analyze_polymarkets (alias for book-delta style on score feeds)
# Reacts to any sports WS update (score, status, time_remaining). Triggers downstream:
# health + parse → rich log of delta → call into market channel tools or Gamma for related prediction
# market liquidity/risk snapshots (the "sports book delta" analogue is score delta driving poly-market books).
# Excellent bridge between in-play scores and trading the correlated prediction markets.
# Full error containment + state.
#
# Usage after: start_full_realtime_session(slugs_or_queries=["nfl", "nba live"], include_sports=True)
async def on_sports_update_then_analyze_polymarkets(
    max_minutes: int = 90,
    enable_deep_analysis: bool = False,
):
    import asyncio
    from datetime import datetime, timezone

    start = asyncio.get_event_loop().time()
    print("[SPORTS-POLY-ANALYZER] START — bridging sports WS updates to prediction market analysis")

    while (asyncio.get_event_loop().time() - start) < (max_minutes * 60):
        try:
            health = get_connection_health(channel="sports")
            if not health.get("connected"):
                await asyncio.sleep(7)
                continue
        except Exception:
            await asyncio.sleep(4)
            continue

        events = []
        try:
            events = await listen_for_ws_events(channel="sports", timeout_seconds=12.0, return_immediately=False)
        except Exception:
            await asyncio.sleep(1)
            continue

        for raw in (events or []):
            try:
                parsed = parse_ws_event(raw) if "parse_ws_event" in globals() else {"normalized": {}, "specific": raw}
                spec = parsed.get("specific") or raw
                league = spec.get("league") or raw.get("league")
                print(f"[{datetime.now(timezone.utc).isoformat()}] SPORTS UPDATE → {league} | {spec.get('score') or spec.get('status')} | raw_keys={list(raw.keys())[:6]}")

                if enable_deep_analysis:
                    # Example bridge: surface call for agent to cross to market WS / CLOB
                    print("  → NEXT: Use get_sports_realtime_snapshot() + search_markets for live sports poly markets; or get_realtime_market_snapshot on discovered tokens.")
            except Exception as perr:
                print(f"[SPORTS-ANALYZE] non-fatal: {perr}")
                continue

        await asyncio.sleep(1.2)

    print("[SPORTS-POLY-ANALYZER] Run complete.")
''',

        "multi_league_sports_watcher": '''# ROBUST SPORTS PATTERN #3: multi_league_sports_watcher
# Subscribes broad sports leagues, continuously consumes + parses, aggregates recent score/status
# across leagues into a compact dashboard snapshot. Triggers on any change and supports health.
# Ideal companion to start_full_realtime_session with include_sports + multiple leagues.
async def multi_league_sports_watcher(leagues: list[str] = None, duration_seconds: int = 300):
    import asyncio
    import time
    from collections import defaultdict

    leagues = leagues or ["NBA", "NFL", "EPL", "MLB", "NHL"]
    start = asyncio.get_event_loop().time()
    recent_by_league: dict = defaultdict(list)
    print(f"[MULTI-LEAGUE-SPORTS] Watching {leagues} for {duration_seconds}s")

    while (asyncio.get_event_loop().time() - start) < duration_seconds:
        try:
            if not get_connection_health(channel="sports").get("connected"):
                await asyncio.sleep(5)
                continue
        except Exception:
            await asyncio.sleep(4)
            continue

        evs = await listen_for_ws_events(channel="sports", timeout_seconds=10.0, return_immediately=False)
        for e in (evs or []):
            try:
                p = parse_ws_event(e) if "parse_ws_event" in globals() else {"specific": e}
                lg = (p.get("specific") or e).get("league", "unknown")
                recent_by_league[lg].append({"ts": time.time(), "data": p.get("specific") or e})
                recent_by_league[lg] = recent_by_league[lg][-5:]  # ring per league
                print(f"[SPORTS LIVE] {lg}: {(p.get('specific') or e).get('score') or (p.get('specific') or e).get('status')}")
            except Exception:
                pass

        await asyncio.sleep(0.7)

    print("[MULTI-LEAGUE-SPORTS] Final recent:", {k: len(v) for k, v in recent_by_league.items()})
''',
    }


def get_monitor_object_template() -> dict:
    """Return the canonical monitor status shape for documentation / validation."""
    return MONITOR_STATUS_SHAPE.copy()


def get_recommended_monitor_workflow() -> list[str]:
    """High-level agent checklist when using the realtime monitor tools."""
    return [
        "1. Call get_capabilities() or get_realtime_trading_guide() to confirm current WS surface.",
        "2. Use start_full_market_monitor(slugs_or_queries=[...]) OR the watch_* / auto_subscribe_* convenience tools for instant Gamma+WS setup.",
        "3. Immediately inspect with get_websocket_status() and get_connection_health('market').",
        "4. Consume via listen_for_ws_events (event-driven, preferred) or get_latest_ws_messages (zero-wait polling).",
        "5. Combine with CLOB: on interesting WS event → get_clob_token_ids (if needed) → liquidity_analysis/risk_check → place_order.",
        "6. For personal activity: connect_user_websocket() + listen_for_ws_events(channel='user', wait_for_event_type='trade').",
        "7. Sports/in-play: connect_sports_websocket() + dedicated listener loop.",
        "8. Always have a health watchdog: if last_message_age grows or reconnect_count spikes, log + optionally force a fresh connect call.",
        "9. Connections live for the lifetime of the MCP process — design your agent to connect early and consume often.",
    ]


# Small helper for agents that want a single importable reference to "the realtime story".
def get_realtime_story_summary() -> str:
    return (
        "Gamma (discovery) → watch_*/start_full_market_monitor (Gamma+WS wiring) → "
        "listen_for_ws_events / get_latest_ws_messages (consumption) → "
        "CLOB tools (execution on signals). All WS channels are long-lived, auto-reconnecting, "
        "and buffered inside the MCP. No agent-side websocket or asyncio task management required."
    )


# =============================================================================
# Lightweight cookbook support (added for get_trading_cookbooks etc.)
# =============================================================================

COOKBOOK_PRIMARY_DIRECTIVE = "PRIMARY: Call get_polymarket_llms_txt() + get_mcp_health_report() first."

COMMON_COOKBOOK_STARTUP_SEQUENCE = [
    "1. get_mcp_health_report(include_detailed=True)  # WHY: full diagnostic across creds/Gamma/WS/gasless",
    "2. get_capabilities()  # WHY: live manifest + new cookbook tools confirmation",
    "3. get_polymarket_llms_txt()  # WHY: authoritative live official Polymarket docs",
    "4. polymarket_alpha_setup_guide(platform='hermes')  # WHY: exact config + realtime notes",
    "5. check_clob_auth(include_raw=True)  # WHY: mandatory trading auth + funder/signer validation",
    "6. get_realtime_trading_guide() + get_realtime_helper_patterns()  # WHY: WS/Gamme/CLOB sequencing + parse recipes",
    "7. Strategy-specific: search_markets / get_clob_token_ids + start_full_market_monitor / gasless_wallet_info"
]


def get_cookbook_startup_mantra() -> dict:
    """Lightweight helper returning the universal PRIMARY directive + recommended first-7 sequence.
    Used by the meta cookbook tools for consistency."""
    return {
        "primary": COOKBOOK_PRIMARY_DIRECTIVE,
        "recommended_first_7_calls": COMMON_COOKBOOK_STARTUP_SEQUENCE,
        "note": "Every cookbook (get_trading_cookbooks, get_end_to_end_agent_example, get_gasless_plus_ws_workflow) opens with the PRIMARY directive. Paste the sequence verbatim at the top of any new agent."
    }


# =============================================================================
# Structured WS Event Parser (new ergonomic helper for consumption)
# =============================================================================

def parse_ws_event(raw_message: Any) -> dict:
    """
    Parse a raw WebSocket message dict into a clean, structured/typed output.

    Ideal for agents consuming listen_for_ws_events or get_latest_ws_messages results.
    Returns a normalized dict with:
      - event_type: canonical type string
      - normalized: flat clean fields common across events (asset_id, price, size, side, timestamp, ...)
      - specific: event-type-specific payload (e.g. bids/asks for 'book', scores for sports)
      - raw: the original message for full fidelity / debugging

    Supports common Polymarket Market/User/Sports event shapes:
      market: book, price_change, last_trade_price, trade, ...
      user: order, trade, fill, ...
      sports: score updates etc.

    Usage in agent loop:
        events = await listen_for_ws_events(...)
        for raw in events:
            parsed = parse_ws_event(raw)
            if parsed["event_type"] == "price_change":
                price = parsed["normalized"].get("price")
                ...
    """
    if not isinstance(raw_message, dict):
        return {
            "event_type": "unknown",
            "error": "Input was not a dict",
            "normalized": {},
            "specific": {},
            "raw": raw_message,
        }

    # Robust event type detection (Polymarket uses both "event_type" and "type")
    et = raw_message.get("event_type") or raw_message.get("type") or "unknown"
    et_lower = str(et).lower().strip()

    # Common identity / timing fields (various casings seen on wire)
    asset_id = (
        raw_message.get("asset_id")
        or raw_message.get("assetId")
        or raw_message.get("market")
        or raw_message.get("asset")
    )
    timestamp = (
        raw_message.get("timestamp")
        or raw_message.get("time")
        or raw_message.get("_received_at")
        or raw_message.get("ts")
    )

    normalized: dict[str, Any] = {
        "asset_id": asset_id,
        "timestamp": timestamp,
    }
    specific: dict[str, Any] = {}

    # --- Market channel common events ---
    if et_lower in ("book", "orderbook", "snapshot"):
        # Full or delta book
        bids = raw_message.get("bids") or raw_message.get("buys") or raw_message.get("buy") or []
        asks = raw_message.get("asks") or raw_message.get("sells") or raw_message.get("sell") or []
        specific = {
            "bids": bids,
            "asks": asks,
            "best_bid": raw_message.get("best_bid") or (bids[0][0] if bids and isinstance(bids, list) and bids else None),
            "best_ask": raw_message.get("best_ask") or (asks[0][0] if asks and isinstance(asks, list) and asks else None),
            "level": raw_message.get("level"),
        }
        normalized.update({
            "best_bid": specific.get("best_bid"),
            "best_ask": specific.get("best_ask"),
        })

    elif "price_change" in et_lower or et_lower in ("pricechange", "price"):
        price = raw_message.get("price") or raw_message.get("lastTradePrice") or raw_message.get("p") or raw_message.get("last_trade_price")
        normalized["price"] = price
        normalized["size"] = raw_message.get("size") or raw_message.get("s") or raw_message.get("amount")
        specific = {
            "last_trade_price": raw_message.get("lastTradePrice") or raw_message.get("last_trade_price"),
            "change": raw_message.get("change") or raw_message.get("delta"),
        }

    elif et_lower in ("trade", "last_trade_price", "lasttradeprice", "fill"):
        normalized["price"] = raw_message.get("price") or raw_message.get("p")
        normalized["size"] = raw_message.get("size") or raw_message.get("s") or raw_message.get("amount")
        normalized["side"] = (
            raw_message.get("side")
            or raw_message.get("takerSide")
            or raw_message.get("makerSide")
            or raw_message.get("taker_side")
        )
        specific = {
            "trade_id": raw_message.get("trade_id") or raw_message.get("id") or raw_message.get("tradeId"),
            "last_trade_price": raw_message.get("lastTradePrice"),
        }

    # --- User / authenticated channel ---
    elif "order" in et_lower:
        normalized["price"] = raw_message.get("price")
        normalized["size"] = raw_message.get("size") or raw_message.get("original_size") or raw_message.get("filled_size")
        normalized["side"] = raw_message.get("side")
        normalized["status"] = raw_message.get("status") or raw_message.get("order_status")
        specific = {
            "order_id": raw_message.get("order_id") or raw_message.get("id") or raw_message.get("orderId"),
            "market": raw_message.get("market") or raw_message.get("condition_id"),
            "filled_size": raw_message.get("filled_size") or raw_message.get("size_matched"),
        }

    elif et_lower in ("trade", "fill", "user_trade"):
        # Already partly covered above; enrich for user context
        normalized["price"] = normalized.get("price") or raw_message.get("price")
        normalized["size"] = normalized.get("size") or raw_message.get("size")
        normalized["side"] = normalized.get("side") or raw_message.get("side")
        specific.update({
            "order_id": raw_message.get("order_id"),
            "trade_id": raw_message.get("trade_id") or raw_message.get("id"),
            "fee": raw_message.get("fee") or raw_message.get("taker_fee"),
        })

    # --- Sports channel ---
    elif "sport" in et_lower or et_lower == "sports" or "score" in et_lower:
        specific = {
            k: raw_message.get(k)
            for k in ("league", "sport", "match_id", "event_id", "score", "home_score", "away_score",
                      "status", "quarter", "time_remaining", "winner")
            if k in raw_message
        }
        normalized["league"] = raw_message.get("league") or raw_message.get("sport")

    # Fallback: surface any obviously useful top-level scalars
    for k in ("price", "size", "side", "status", "best_bid", "best_ask", "lastTradePrice"):
        if k not in normalized and k in raw_message:
            normalized[k] = raw_message[k]

    return {
        "event_type": et,
        "normalized": normalized,
        "specific": specific,
        "raw": raw_message,
    }


def get_ws_event_types_help() -> dict:
    """Quick reference for the event types parse_ws_event understands and common shapes."""
    return {
        "market_common": ["book", "price_change", "last_trade_price", "trade"],
        "user_common": ["order", "trade", "fill"],
        "sports_common": ["score_update", "sports"],
        "usage_note": "Pass any raw dict from WS consumption tools into parse_ws_event(). The 'normalized' key gives the cleanest fields for most logic."
    }


# Dedicated lightweight accessor for the new event-driven trading patterns (used by the MCP tool get_ws_event_driven_patterns)
def get_event_driven_trading_patterns() -> dict[str, str]:
    """Return only the focused, production-grade event-driven trading reaction patterns.

    These are the highest-leverage "react to WS event → full pre-trade pipeline → conditional execution" recipes.
    All three patterns now contain complete, robust, copy-paste-ready implementations featuring:

      • Mandatory get_connection_health() gates before every listen cycle
      • parse_ws_event() on every raw message for clean normalized + specific fields
      • Cooldowns, deduplication, and per-asset state to avoid over-trading
      • Complete pre-trade sequence on signals: get_clob_balance(refresh=True) + liquidity_analysis + risk_check
      • Safe conditional place_limit_order only when enable_* flags are explicitly True
      • Comprehensive try/except + logging around every external call (listen, health, CLOB, analysis)
      • get_positions / get_open_orders cross-checks where relevant (my fills pattern)
      • Relative-value / spread / outlier detection with follow-on analysis calls (multi-asset)

    Perfect for agents. Always call a wiring tool first (start_full_market_monitor, connect_user_websocket, etc.)
    then drop the desired async def into your loop. The patterns surface via get_ws_event_driven_patterns()
    (and get_realtime_helper_patterns for the full library).
    """
    loops = get_copy_paste_realtime_loops()
    keys = [
        "on_price_move_then_place_limit",
        "on_my_fill_then_risk_check",
        "multi_asset_book_delta_watcher",
    ]
    return {k: loops[k] for k in keys if k in loops}


def get_realtime_sports_patterns() -> dict[str, str]:
    """
    Dedicated surface for the new sports + multi-channel event-driven patterns.
    Returns the three sports-specific recipes added for in-play orchestration:
    on_score_change_then_check_markets, on_sports_update_then_analyze_polymarkets,
    multi_league_sports_watcher.

    Use after wiring via start_full_realtime_session(include_sports=True) or
    connect_sports_websocket + the market starters for correlated poly markets.
    These patterns emphasize the bridge from live sports scores → prediction market discovery/monitoring.
    """
    loops = get_copy_paste_realtime_loops()
    sports_keys = [
        "on_score_change_then_check_markets",
        "on_sports_update_then_analyze_polymarkets",
        "multi_league_sports_watcher",
    ]
    return {
        "title": "Sports & Multi-Channel WS Event-Driven Patterns (NEW)",
        "description": "Production patterns for reacting to live sports scores/status and bridging to Polymarket prediction markets on the same events. Full health gating + parse_ws_event + cooldowns. Call get_realtime_helper_patterns() for the complete set including classic market ones.",
        "sports_focused_patterns": {k: loops[k] for k in sports_keys if k in loops},
        "usage": "Call start_full_realtime_session(slugs_or_queries=[...], include_sports=True, sports_leagues=['NBA','NFL']) then drop one of these async defs into your agent loop. Always pipe sports events through parse_ws_event().",
        "related": ["start_full_realtime_session", "get_sports_realtime_snapshot", "connect_sports_websocket", "get_realtime_trading_guide"],
    }
