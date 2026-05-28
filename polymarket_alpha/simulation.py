"""
PAPER / SIMULATION ONLY layer for safe strategy development and harness testing.

All tools here are 100% read-only with respect to real Polymarket accounts,
wallets, and the live CLOB. No real orders, balances, or funds are touched.

Implements in-memory paper trading sessions + market impact simulation +
optional replay of recent managed WebSocket buffers into paper state.

See get_mcp_health_report(), get_polymarket_llms_txt(), get_clob_docs(),
get_realtime_trading_guide(), get_realtime_helper_patterns(), and
get_realtime_market_snapshot() for the authoritative live + realtime patterns
to mirror in simulation before going live.
"""

import time
import uuid
from typing import Any, Optional

from fastmcp import FastMCP
from py_clob_client_v2 import ClobClient

from .config import get_clob_host, get_chain_id

# =============================================================================
# In-memory simulation state (module-private, process lifetime)
# =============================================================================
_paper_sessions: dict[str, dict[str, Any]] = {}


def _get_public_clob_client() -> ClobClient:
    return ClobClient(host=get_clob_host(), chain_id=get_chain_id())


def _safe_float(x: Any, default: float = 0.0) -> float:
    try:
        return float(x)
    except Exception:
        return default


def _fetch_book_snapshot(token_id: str) -> dict:
    """Minimal book snapshot for sim impact and matching (same shape expectations as analysis)."""
    try:
        client = _get_public_clob_client()
        raw = client.get_order_book(token_id) or {}
        bids = []
        asks = []
        for b in raw.get("bids") or []:
            p = _safe_float(b.get("price") if isinstance(b, dict) else (b[0] if isinstance(b, (list, tuple)) else 0))
            s = _safe_float(b.get("size") if isinstance(b, dict) else (b[1] if isinstance(b, (list, tuple)) and len(b) > 1 else 0))
            if p > 0 and s > 0:
                bids.append({"price": p, "size": s})
        for a in raw.get("asks") or []:
            p = _safe_float(a.get("price") if isinstance(a, dict) else (a[0] if isinstance(a, (list, tuple)) else 0))
            s = _safe_float(a.get("size") if isinstance(a, dict) else (a[1] if isinstance(a, (list, tuple)) and len(a) > 1 else 0))
            if p > 0 and s > 0:
                asks.append({"price": p, "size": s})
        bids.sort(key=lambda x: -x["price"])
        asks.sort(key=lambda x: x["price"])
        mid = None
        if bids and asks:
            mid = (bids[0]["price"] + asks[0]["price"]) / 2
        return {
            "token_id": token_id,
            "bids": bids,
            "asks": asks,
            "best_bid": bids[0]["price"] if bids else None,
            "best_ask": asks[0]["price"] if asks else None,
            "mid": mid,
            "timestamp": time.time(),
        }
    except Exception as e:
        return {"token_id": token_id, "error": str(e), "bids": [], "asks": []}


def _compute_market_impact(token_id: str, side: str, size_usdc: float) -> dict:
    """Internal: walk live book for expected fill metrics (no state mutation)."""
    book = _fetch_book_snapshot(token_id)
    if "error" in book:
        return book

    side = side.lower()
    levels = book["asks"] if side == "buy" else book["bids"]
    reference = book["best_ask"] if side == "buy" else book["best_bid"]
    mid = book.get("mid") or reference

    remaining = size_usdc
    filled_notional = 0.0
    filled_shares = 0.0
    weighted_price_sum = 0.0
    levels_touched = 0

    for lvl in levels:
        if remaining <= 0:
            break
        levels_touched += 1
        px = lvl["price"]
        avail_value = px * lvl["size"]
        take = min(remaining, avail_value)
        shares = take / px
        filled_notional += take
        filled_shares += shares
        weighted_price_sum += px * shares
        remaining -= take

    avg_price = (weighted_price_sum / filled_shares) if filled_shares > 0 else None
    slippage_bps = None
    if avg_price and reference and reference > 0:
        if side == "buy":
            slippage_bps = ((avg_price - reference) / reference) * 10000
        else:
            slippage_bps = ((reference - avg_price) / reference) * 10000

    # Very conservative taker fee estimate (Polymarket CLOB is maker/taker but often low/zero for many pairs)
    taker_fee_bps = 2.0
    est_fee_usdc = (filled_notional * taker_fee_bps) / 10000 if filled_notional > 0 else 0.0

    return {
        "token_id": token_id,
        "side": side,
        "requested_size_usdc": size_usdc,
        "expected_fill_price": round(avg_price, 6) if avg_price else None,
        "expected_shares": round(filled_shares, 6),
        "expected_slippage_bps": round(slippage_bps, 1) if slippage_bps is not None else None,
        "est_taker_fee_usdc": round(est_fee_usdc, 4),
        "levels_consumed": levels_touched,
        "partial_fill": remaining > 0.01,
        "remaining_notional": round(max(0.0, remaining), 4),
        "reference_best": reference,
        "mid_at_snapshot": mid,
        "book_timestamp": book.get("timestamp"),
        "WARNING": "PAPER / SIMULATION ONLY - for strategy development and harness testing. Never use for real sizing without live verification.",
    }


def _get_session(session_id: str) -> Optional[dict]:
    return _paper_sessions.get(session_id)


def _mark_to_market(session: dict) -> None:
    """Update virtual P&L using current mids (best effort, live)."""
    positions = session.get("positions", {})
    total_unrealized = 0.0
    client = _get_public_clob_client()
    for tok, shares in list(positions.items()):
        if shares == 0:
            continue
        try:
            mid = _safe_float(client.get_midpoint(tok))
            # Assume entry not tracked in detail; mark current value
            total_unrealized += shares * mid
        except Exception:
            pass
    session["unrealized_value_usdc"] = round(total_unrealized, 4)


# =============================================================================
# Registration
# =============================================================================

def register_simulation_tools(mcp: FastMCP) -> None:

    @mcp.tool
    def simulate_market_impact(token_id: str, side: str, size_usdc: float) -> dict:
        """
        PAPER / SIMULATION ONLY - Walks the live public orderbook for a token and
        returns realistic expected fill price, shares received, slippage (bps),
        estimated taker fees, and whether the size would exhaust depth.

        Use this (and the paper session tools) to safely size and test strategies
        before any live place_limit_order / place_market_order.

        See get_mcp_health_report(), get_polymarket_llms_txt(), get_clob_docs(),
        liquidity_analysis (real), get_realtime_trading_guide(), and
        get_realtime_market_snapshot() for the live equivalents to mirror.
        """
        if not token_id or side.lower() not in ("buy", "sell") or size_usdc <= 0:
            return {
                "error": "token_id, side (buy/sell), size_usdc > 0 required",
                "WARNING": "PAPER / SIMULATION ONLY",
            }

        result = _compute_market_impact(token_id, side, size_usdc)
        result["WARNING"] = "PAPER / SIMULATION ONLY - for strategy development and harness testing. No real orders placed."
        result["recommended_next"] = [
            "create_paper_trading_session",
            "paper_place_limit_order (using sim results)",
            "paper_get_status",
            "Then compare against real liquidity_analysis + get_live_orderbook",
        ]
        return result

    @mcp.tool
    def create_paper_trading_session(session_name: str, initial_usdc: float = 10000.0) -> dict:
        """
        PAPER / SIMULATION ONLY - Creates an isolated in-memory paper trading session.

        Returns session_id you must pass to all other paper_* tools.
        Tracks virtual USDC balance, positions (token -> shares), open virtual limit orders,
        and a simple history. Mark-to-market uses live mids on status calls.

        Perfect for developing/testing full strategies, WS reaction logic (via replay),
        and harness validation without any real capital or auth.

        See get_mcp_health_report(), get_realtime_trading_guide(), and
        get_realtime_helper_patterns() for patterns worth simulating here first.
        """
        if initial_usdc <= 0:
            initial_usdc = 10000.0

        sid = str(uuid.uuid4())[:8]
        session = {
            "id": sid,
            "name": session_name or f"paper-{sid}",
            "balance_usdc": round(float(initial_usdc), 4),
            "initial_usdc": round(float(initial_usdc), 4),
            "positions": {},  # token_id -> shares (float)
            "open_orders": [],  # list of dicts
            "history": [],
            "created_at": time.time(),
            "unrealized_value_usdc": 0.0,
            "WARNING": "PAPER / SIMULATION ONLY - for strategy development and harness testing",
        }
        _paper_sessions[sid] = session
        return {
            "session_id": sid,
            "session_name": session["name"],
            "initial_usdc": session["initial_usdc"],
            "current_balance_usdc": session["balance_usdc"],
            "WARNING": "PAPER / SIMULATION ONLY - for strategy development and harness testing. Use paper_get_status, paper_place_limit_order, replay_ws_events, close_paper_session etc.",
            "next_steps": ["simulate_market_impact", "paper_place_limit_order", "get_available_paper_sessions"],
        }

    @mcp.tool
    def paper_place_limit_order(
        session_id: str,
        token_id: str,
        side: str,
        price: float,
        size: float,  # shares
    ) -> dict:
        """
        PAPER / SIMULATION ONLY - Places a virtual resting limit order inside a paper session.

        Immediately snapshots the live book and performs simplistic match logic:
        - If your limit crosses the book (aggressive), it "fills" instantly against
          available depth at simulated prices (updates balance + position).
        - Otherwise the order is stored as resting in the session.

        size = number of shares (not USDC). Price in 0-1 decimal.

        This + replay_ws_events lets you safely backtest reactive strategies.

        See get_mcp_health_report(), get_polymarket_llms_txt(), get_realtime_trading_guide(),
        listen_for_ws_events, and get_latest_ws_messages for the real patterns to replicate.
        """
        sess = _get_session(session_id)
        if not sess:
            return {"error": "Unknown session_id. Call create_paper_trading_session first.", "WARNING": "PAPER / SIMULATION ONLY"}

        side = side.lower()
        if side not in ("buy", "sell") or price <= 0 or size <= 0:
            return {"error": "side (buy/sell), price>0, size (shares)>0 required", "WARNING": "PAPER / SIMULATION ONLY"}

        book = _fetch_book_snapshot(token_id)
        order = {
            "order_id": str(uuid.uuid4())[:8],
            "token_id": token_id,
            "side": side,
            "price": round(price, 6),
            "size": round(size, 6),
            "remaining_size": round(size, 6),
            "status": "open",
            "created_at": time.time(),
        }

        filled = 0.0
        filled_notional = 0.0
        immediate_fills = []

        # Simple aggressive match
        if side == "buy":
            for lvl in book.get("asks", []):
                if order["remaining_size"] <= 0:
                    break
                if lvl["price"] <= order["price"]:  # crosses or better
                    match_size = min(order["remaining_size"], lvl["size"])
                    cost = match_size * lvl["price"]
                    if cost > sess["balance_usdc"]:
                        match_size = sess["balance_usdc"] / lvl["price"]
                        cost = sess["balance_usdc"]
                    if match_size <= 0:
                        break
                    order["remaining_size"] -= match_size
                    filled += match_size
                    filled_notional += cost
                    immediate_fills.append({"price": lvl["price"], "size": match_size})
                    sess["balance_usdc"] = round(sess["balance_usdc"] - cost, 4)
                    # position up
                    sess["positions"][token_id] = round(sess["positions"].get(token_id, 0.0) + match_size, 6)
        else:  # sell
            for lvl in book.get("bids", []):
                if order["remaining_size"] <= 0:
                    break
                if lvl["price"] >= order["price"]:
                    match_size = min(order["remaining_size"], lvl["size"])
                    proceeds = match_size * lvl["price"]
                    order["remaining_size"] -= match_size
                    filled += match_size
                    filled_notional += proceeds
                    immediate_fills.append({"price": lvl["price"], "size": match_size})
                    sess["balance_usdc"] = round(sess["balance_usdc"] + proceeds, 4)
                    sess["positions"][token_id] = round(sess["positions"].get(token_id, 0.0) - match_size, 6)

        if order["remaining_size"] > 0.0001:
            order["status"] = "resting"
            sess["open_orders"].append(order)
        else:
            order["status"] = "filled"

        sess["history"].append({
            "ts": time.time(),
            "action": "paper_limit",
            "order": order,
            "immediate_fills": immediate_fills,
            "filled_shares": round(filled, 6),
            "filled_notional": round(filled_notional, 4),
        })

        _mark_to_market(sess)

        return {
            "session_id": session_id,
            "order": order,
            "immediate_fills": immediate_fills,
            "filled_shares": round(filled, 6),
            "new_balance_usdc": sess["balance_usdc"],
            "current_position_shares": sess["positions"].get(token_id, 0.0),
            "WARNING": "PAPER / SIMULATION ONLY - for strategy development and harness testing. No real order was placed on Polymarket.",
            "note": "Resting orders stay in this session only. Use paper_get_status() to inspect. Replay WS events to simulate reactions.",
        }

    @mcp.tool
    def paper_get_status(session_id: str) -> dict:
        """
        PAPER / SIMULATION ONLY - Returns full virtual state for a paper session:
        balance, positions, open virtual orders, recent history, and live mark-to-market
        unrealized value (using current mids).

        Essential for tracking simulated P&L and strategy performance.

        See get_mcp_health_report(), get_realtime_trading_guide(), and
        get_latest_ws_messages for how to keep simulated state in sync with reality.
        """
        sess = _get_session(session_id)
        if not sess:
            return {"error": "Unknown session_id", "WARNING": "PAPER / SIMULATION ONLY"}

        _mark_to_market(sess)

        realized_pnl = sess["balance_usdc"] - sess["initial_usdc"]
        # Rough total equity (cash + current value of positions at mid)
        equity = sess["balance_usdc"] + sess.get("unrealized_value_usdc", 0.0)

        return {
            "session_id": session_id,
            "name": sess["name"],
            "balance_usdc": sess["balance_usdc"],
            "initial_usdc": sess["initial_usdc"],
            "realized_pnl_usdc": round(realized_pnl, 4),
            "unrealized_value_usdc": sess.get("unrealized_value_usdc", 0.0),
            "approx_equity_usdc": round(equity, 4),
            "positions": sess["positions"],
            "open_virtual_orders": sess["open_orders"],
            "history_length": len(sess["history"]),
            "created_at": sess["created_at"],
            "WARNING": "PAPER / SIMULATION ONLY - for strategy development and harness testing. All numbers are virtual.",
            "note": "P&L is approximate (mids only, no fees on paper fills). Use simulate_market_impact for pre-trade realism.",
        }

    @mcp.tool
    def replay_ws_events(
        channel: str = "market",
        limit: int = 20,
        apply_to_paper_session: str | None = None,
    ) -> dict:
        """
        PAPER / SIMULATION ONLY (optional integration) - Pulls recent events from the
        managed WebSocket buffers (same ones used by listen_for_ws_events / get_latest_ws_messages)
        and optionally "replays" them into a paper trading session (currently: appends to history
        + very simple price-triggered simulated fills for resting paper orders if a paper session is supplied).

        This enables safe backtesting of reactive agent logic (e.g. "on price_change, adjust paper limits").

        channel: "market", "user", or "sports" (market most useful for price/book/trade replay).
        apply_to_paper_session: session_id from create_paper_trading_session (optional).

        See get_mcp_health_report(), get_realtime_trading_guide(), get_realtime_helper_patterns(),
        listen_for_ws_events, get_latest_ws_messages, parse_ws_event, and start_full_market_monitor.
        """
        result: dict[str, Any] = {
            "channel": channel,
            "requested_limit": limit,
            "replayed_into": apply_to_paper_session,
            "events_retrieved": 0,
            "actions_taken": [],
            "WARNING": "PAPER / SIMULATION ONLY - for strategy development and harness testing.",
        }

        events: list[dict] = []
        try:
            # Access the managed WS internals (best-effort, same package)
            from . import websocket as ws_mod  # type: ignore

            mgr = None
            ch = channel.lower()
            if ch == "market":
                mgr = getattr(ws_mod, "_market_ws", None)
            elif ch == "user":
                mgr = getattr(ws_mod, "_user_ws", None)
            elif ch == "sports":
                mgr = getattr(ws_mod, "_sports_ws", None)

            if mgr and hasattr(mgr, "get_recent_messages"):
                events = mgr.get_recent_messages(limit) or []
            else:
                # Fallback: try to surface a status note
                result["note"] = "No active WS manager found for channel (connect via start_full_market_monitor or connect_* first for best replay)."
        except Exception as e:
            result["ws_access_error"] = str(e)
            result["note"] = "Replay attempted but WS manager not directly accessible. Start a real WS monitor first for buffer population."

        result["events_retrieved"] = len(events)
        result["sample_events"] = events[:3] if events else []

        sess = _get_session(apply_to_paper_session) if apply_to_paper_session else None
        if sess and events:
            # Very lightweight reaction simulation: if recent price_change or trade, try to match resting paper orders
            actions = []
            for ev in events[-limit:]:
                et = str(ev.get("event_type") or ev.get("type") or "").lower()
                if "price" in et or "trade" in et or "book" in et:
                    asset = ev.get("asset_id") or ev.get("market") or ev.get("token_id")
                    price = _safe_float(ev.get("price") or ev.get("last_trade_price") or (ev.get("normalized") or {}).get("price"))
                    if asset and price > 0:
                        # Attempt naive aggressive fill of any resting paper orders on this asset at the observed price
                        for o in list(sess["open_orders"]):
                            if o["token_id"] != asset or o["status"] != "resting":
                                continue
                            crosses = (o["side"] == "buy" and price <= o["price"]) or (o["side"] == "sell" and price >= o["price"])
                            if crosses:
                                match = min(o["remaining_size"], 10.0)  # tiny sim size for safety
                                o["remaining_size"] = round(o["remaining_size"] - match, 6)
                                if o["remaining_size"] < 0.0001:
                                    o["status"] = "filled"
                                if o["side"] == "buy":
                                    cost = match * price
                                    sess["balance_usdc"] = round(max(0.0, sess["balance_usdc"] - cost), 4)
                                    sess["positions"][asset] = round(sess["positions"].get(asset, 0.0) + match, 6)
                                else:
                                    sess["balance_usdc"] = round(sess["balance_usdc"] + (match * price), 4)
                                    sess["positions"][asset] = round(sess["positions"].get(asset, 0.0) - match, 6)
                                actions.append({"order_id": o["order_id"], "matched_on_replay": match, "at_price": price})
            sess["history"].append({"ts": time.time(), "action": "ws_replay", "events_processed": len(events), "fills": actions})
            result["actions_taken"] = actions
            _mark_to_market(sess)

        result["WARNING"] = "PAPER / SIMULATION ONLY - for strategy development and harness testing. Events are snapshots of live buffers; no real trading occurred."
        return result

    @mcp.tool
    def close_paper_session(session_id: str) -> dict:
        """
        PAPER / SIMULATION ONLY - Closes and removes a paper trading session (frees memory).

        Final status is returned before deletion.
        """
        sess = _get_session(session_id)
        if not sess:
            return {"error": "Unknown session_id", "WARNING": "PAPER / SIMULATION ONLY"}

        _mark_to_market(sess)
        final = {
            "session_id": session_id,
            "final_balance": sess["balance_usdc"],
            "final_positions": sess["positions"],
            "final_equity_approx": round(sess["balance_usdc"] + sess.get("unrealized_value_usdc", 0.0), 4),
        }
        _paper_sessions.pop(session_id, None)
        final["WARNING"] = "PAPER / SIMULATION ONLY - session closed and deleted. All state was virtual."
        return final

    @mcp.tool
    def get_available_paper_sessions() -> dict:
        """
        PAPER / SIMULATION ONLY - Lists all currently active paper trading sessions
        (ids, names, rough equity). Use to discover session_ids for status / orders / replay.

        See create_paper_trading_session and the full paper tool family.
        """
        sessions = []
        for sid, s in _paper_sessions.items():
            _mark_to_market(s)
            sessions.append({
                "session_id": sid,
                "name": s["name"],
                "balance_usdc": s["balance_usdc"],
                "approx_equity": round(s["balance_usdc"] + s.get("unrealized_value_usdc", 0.0), 2),
                "open_orders_count": len(s["open_orders"]),
                "positions_count": len([k for k, v in s["positions"].items() if v != 0]),
            })
        return {
            "active_sessions": len(sessions),
            "sessions": sessions,
            "WARNING": "PAPER / SIMULATION ONLY - for strategy development and harness testing. Nothing here touches real accounts or the live CLOB.",
            "usage": "Create sessions, run simulate_market_impact + paper_place_limit_order + replay_ws_events, inspect with paper_get_status.",
        }
