"""
Analysis tools. All visible in read-only mode.

Production-grade market microstructure and pre-trade analysis.
All tools are read-only and safe.

See get_mcp_health_report(), get_polymarket_llms_txt(), get_clob_docs(),
get_realtime_trading_guide(), and get_realtime_helper_patterns() for full
workflows, especially before using analysis outputs in live or paper trading.
"""

from typing import Any, Optional

from fastmcp import FastMCP
from py_clob_client_v2 import ClobClient

from .config import get_clob_host, get_chain_id


def _get_public_clob_client() -> ClobClient:
    """Lightweight public (unauth) ClobClient for analysis tools. Mirrors clob_public pattern."""
    return ClobClient(host=get_clob_host(), chain_id=get_chain_id())


def _safe_float(x: Any, default: float = 0.0) -> float:
    try:
        return float(x)
    except Exception:
        return default


def _get_orderbook_data(token_id: str) -> dict:
    """Fetch and normalize orderbook. Returns raw + parsed bids/asks sorted."""
    client = _get_public_clob_client()
    raw = client.get_order_book(token_id) or {}
    bids_raw = raw.get("bids") or []
    asks_raw = raw.get("asks") or []

    bids: list[tuple[float, float]] = []
    asks: list[tuple[float, float]] = []
    for b in bids_raw:
        p = _safe_float(b.get("price") if isinstance(b, dict) else (b[0] if isinstance(b, (list, tuple)) else b))
        s = _safe_float(b.get("size") if isinstance(b, dict) else (b[1] if isinstance(b, (list, tuple)) and len(b) > 1 else 0))
        if p > 0 and s > 0:
            bids.append((p, s))
    for a in asks_raw:
        p = _safe_float(a.get("price") if isinstance(a, dict) else (a[0] if isinstance(a, (list, tuple)) else a))
        s = _safe_float(a.get("size") if isinstance(a, dict) else (a[1] if isinstance(a, (list, tuple)) and len(a) > 1 else 0))
        if p > 0 and s > 0:
            asks.append((p, s))

    # Sort: bids descending price (best first for sells), asks ascending (best first for buys)
    bids.sort(key=lambda x: -x[0])
    asks.sort(key=lambda x: x[0])

    best_bid = bids[0][0] if bids else None
    best_ask = asks[0][0] if asks else None
    mid = (best_bid + best_ask) / 2 if best_bid and best_ask else (best_bid or best_ask)

    return {
        "raw": raw,
        "bids": bids,
        "asks": asks,
        "best_bid": best_bid,
        "best_ask": best_ask,
        "mid": mid,
        "bids_depth_usdc": sum(p * s for p, s in bids),
        "asks_depth_usdc": sum(p * s for p, s in asks),
    }


def _compute_slippage_for_notional(book_data: dict, notional_usdc: float, side: str) -> dict:
    """
    Walk the book to estimate realistic fill for the given notional.
    side: 'buy' (hit asks, acquire shares) or 'sell' (hit bids, sell shares for USDC).
    Returns weighted avg price, shares, slippage vs mid/best, levels touched.
    """
    if notional_usdc <= 0:
        return {"error": "notional must be positive"}

    bids = book_data["bids"]
    asks = book_data["asks"]
    mid = book_data.get("mid")
    best_bid = book_data.get("best_bid")
    best_ask = book_data.get("best_ask")

    total_cost = 0.0
    total_shares = 0.0
    levels = 0
    remaining = notional_usdc

    if side.lower() == "buy":
        # Taker buy: consume asks from best (lowest price)
        levels_list = asks
        reference = best_ask or mid
    else:
        # Taker sell: consume bids from best (highest price)
        levels_list = bids
        reference = best_bid or mid

    for price, size in levels_list:
        if remaining <= 0:
            break
        levels += 1
        value_available = price * size
        take = min(remaining, value_available)
        shares_here = take / price
        total_cost += take
        total_shares += shares_here
        remaining -= take

    if total_shares <= 0:
        return {
            "side": side,
            "notional_usdc": notional_usdc,
            "filled_notional_usdc": 0.0,
            "shares": 0.0,
            "avg_fill_price": None,
            "slippage_bps": None,
            "levels_consumed": levels,
            "error": "Insufficient depth to fill requested notional",
            "depth_available_usdc": book_data.get("asks_depth_usdc" if side == "buy" else "bids_depth_usdc"),
        }

    avg_price = total_cost / total_shares
    filled_notional = total_cost if side == "buy" else total_shares * avg_price  # for sell it's proceeds

    slippage_bps = None
    if reference and reference > 0 and avg_price > 0:
        # Positive slippage = worse price for the taker
        if side == "buy":
            slippage_bps = ((avg_price - reference) / reference) * 10000
        else:
            slippage_bps = ((reference - avg_price) / reference) * 10000

    return {
        "side": side,
        "notional_usdc": notional_usdc,
        "filled_notional_usdc": round(filled_notional, 4),
        "shares": round(total_shares, 6),
        "avg_fill_price": round(avg_price, 6),
        "reference_price": round(reference, 6) if reference else None,
        "slippage_bps": round(slippage_bps, 1) if slippage_bps is not None else None,
        "levels_consumed": levels,
        "partial_fill": remaining > 0.01,
        "remaining_notional": round(max(0.0, remaining), 4),
    }


def register_analysis_tools(mcp: FastMCP) -> None:

    @mcp.tool
    def calculate_implied_probability(price: float, fee_bps: int = 0) -> dict:
        """
        Convert market price (0-1 scale) to implied probability (percent).

        See get_mcp_health_report(), get_polymarket_llms_txt(), get_clob_docs()
        and get_realtime_trading_guide() for context on pricing, fees, and when
        to use probability thinking vs raw prices.
        """
        if not (0 <= price <= 1):
            return {"error": "price must be between 0 and 1"}
        prob = price * 100
        adjusted = prob  # simple; real fee adjustment can be added
        return {
            "price": price,
            "implied_probability_pct": round(prob, 4),
            "fee_bps": fee_bps,
            "note": "For binary markets, Yes price * 100 ≈ Yes probability. Cross-check with detect_yes_no_arb()."
        }

    @mcp.tool
    def liquidity_analysis(token_id: str, notional_usdc: float = 1000) -> dict:
        """
        Production-grade liquidity & slippage estimator.

        Fetches live public orderbook via CLOB and walks bids/asks to compute
        realistic expected fill price, slippage (bps), shares received, and
        depth exhaustion for BOTH buy and sell sides for the requested notional.

        CRITICAL: Call this (and risk_check) before any meaningful size.
        MCP strongly recommends calling get_mcp_health_report() + get_live_orderbook
        first, then this. Also see get_polymarket_llms_txt() and get_realtime_trading_guide().
        """
        if not token_id or notional_usdc <= 0:
            return {"error": "token_id required and notional_usdc > 0"}

        try:
            book = _get_orderbook_data(token_id)
            buy = _compute_slippage_for_notional(book, notional_usdc, "buy")
            sell = _compute_slippage_for_notional(book, notional_usdc, "sell")

            return {
                "token_id": token_id,
                "requested_notional_usdc": notional_usdc,
                "best_bid": book["best_bid"],
                "best_ask": book["best_ask"],
                "mid": book["mid"],
                "total_bid_depth_usdc": round(book["bids_depth_usdc"], 2),
                "total_ask_depth_usdc": round(book["asks_depth_usdc"], 2),
                "buy_side": buy,
                "sell_side": sell,
                "recommendation": "Large slippage or partial fill? Split orders or use limit. Re-check after WS updates via get_latest_ws_messages.",
                "source": "live CLOB orderbook (public)",
            }
        except Exception as e:
            return {
                "token_id": token_id,
                "requested_notional_usdc": notional_usdc,
                "error": str(e),
                "note": "Orderbook fetch failed. Confirm token_id via get_clob_token_ids(). Call get_mcp_health_report().",
            }

    @mcp.tool
    def risk_check(proposed_size_usdc: float, token_id: str = None) -> dict:
        """
        Pre-trade risk assessment. MCP strongly recommends calling this
        (plus liquidity_analysis) before any place_*_order.

        See get_mcp_health_report(), get_realtime_trading_guide(), and
        get_polymarket_llms_txt() for complete pre-trade checklists.
        """
        warnings = []
        if proposed_size_usdc > 5000:
            warnings.append("Large size. Consider splitting or checking liquidity first.")
        if proposed_size_usdc > 25000:
            warnings.append("Very large notional — high market impact risk even on liquid markets.")
        if token_id:
            warnings.append("Run liquidity_analysis(token_id) + get_live_orderbook(token_id) for precise depth before sizing.")
        rec = "Proceed with caution" if warnings else "Size appears reasonable for most markets; still verify liquidity."
        return {
            "size": proposed_size_usdc,
            "warnings": warnings,
            "recommendation": rec,
            "next_steps": ["liquidity_analysis(token_id)", "get_live_orderbook(token_id)", "get_mcp_health_report()"],
        }

    # -------------------------------------------------------------------------
    # NEW high-value production analysis tools (7 added)
    # -------------------------------------------------------------------------

    @mcp.tool
    def orderbook_imbalance(token_id: str) -> dict:
        """
        Quantifies current bid vs ask pressure from live orderbook depth.

        Returns total notional on each side, imbalance ratio (positive = bid heavy),
        and interpretation. Useful for short-term directional bias before
        limit orders or market impact sims.

        Always cross-reference with get_mcp_health_report(), get_live_orderbook(),
        get_realtime_trading_guide(), and recent WS book events via listen_for_ws_events().
        """
        try:
            book = _get_orderbook_data(token_id)
            bid_depth = book["bids_depth_usdc"]
            ask_depth = book["asks_depth_usdc"]
            total = bid_depth + ask_depth
            if total <= 0:
                return {"token_id": token_id, "error": "No depth"}

            imbalance = (bid_depth - ask_depth) / total  # -1 to +1
            ratio = bid_depth / ask_depth if ask_depth > 0 else float("inf")

            interp = "balanced"
            if imbalance > 0.25:
                interp = "strong bid pressure (bullish bias)"
            elif imbalance < -0.25:
                interp = "strong ask pressure (bearish bias)"

            return {
                "token_id": token_id,
                "bid_depth_usdc": round(bid_depth, 2),
                "ask_depth_usdc": round(ask_depth, 2),
                "imbalance_ratio": round(ratio, 4) if ask_depth > 0 else None,
                "imbalance_score": round(imbalance, 4),  # positive = more bids
                "interpretation": interp,
                "best_bid": book["best_bid"],
                "best_ask": book["best_ask"],
                "note": "Snapshot only. Combine with WS price_change/book events and get_latest_ws_messages for momentum.",
            }
        except Exception as e:
            return {"token_id": token_id, "error": str(e), "advice": "Verify token via get_clob_token_ids(). See get_mcp_health_report()."}

    @mcp.tool
    def detect_yes_no_arb(
        condition_id: str | None = None,
        yes_token: str | None = None,
        no_token: str | None = None,
    ) -> dict:
        """
        Detects simple yes/no arbitrage opportunity on a binary market.

        Provide condition_id (preferred) OR both yes_token + no_token.
        Uses live mids. Flags when yes_price + no_price deviates meaningfully
        from 1.0 (after tiny buffer for fees/slip).

        Routes agents to get_mcp_health_report(), get_polymarket_llms_txt(section on markets),
        get_clob_token_ids(), get_event_details(), and get_realtime_trading_guide() for arb execution.
        """
        if not yes_token or not no_token:
            if condition_id:
                # Minimal: advise caller to resolve via Gamma first (consistent with MCP discipline)
                return {
                    "condition_id": condition_id,
                    "error": "Provide yes_token and no_token (or call get_clob_token_ids(condition_id=...) / get_event_details first)",
                    "recommended": "get_clob_token_ids(condition_id=condition_id) then pass the two clob_token_ids",
                }
            return {"error": "Provide condition_id (then resolve tokens) OR yes_token + no_token explicitly"}

        try:
            client = _get_public_clob_client()
            yes_mid = _safe_float(client.get_midpoint(yes_token))
            no_mid = _safe_float(client.get_midpoint(no_token))
            sum_price = yes_mid + no_mid
            deviation = sum_price - 1.0

            arb_possible = abs(deviation) > 0.005  # 50bps buffer for fees/slip/rounding
            direction = None
            if deviation > 0.005:
                direction = "yes+no > 1: potential sell both (or buy the cheap synthetic)"
            elif deviation < -0.005:
                direction = "yes+no < 1: potential buy both (arb the mispricing)"

            return {
                "condition_id": condition_id,
                "yes_token": yes_token,
                "no_token": no_token,
                "yes_mid": round(yes_mid, 6),
                "no_mid": round(no_mid, 6),
                "sum": round(sum_price, 6),
                "deviation_from_1": round(deviation, 6),
                "arb_possible": arb_possible,
                "direction_hint": direction,
                "buffer_bps": 50,
                "note": "Live mids only. Confirm full depth with liquidity_analysis + orderbook before sizing. Fees/slippage usually eat small edges. Use get_realtime_trading_guide() + paper sims first.",
            }
        except Exception as e:
            return {"error": str(e), "advice": "Use get_clob_token_ids first. Call get_mcp_health_report()."}

    @mcp.tool
    def volume_profile(token_id: str, lookback: int = 100) -> dict:
        """
        Aggregates recent trade volume and size distribution from CLOB public trades.

        Provides notional volume, avg trade size, trade count in window.
        Complements price_history for microstructure context.

        See get_mcp_health_report(), get_recent_trades (CLOB), get_price_history,
        get_realtime_trading_guide(), and listen_for_ws_events(event_types=['trade']).
        """
        try:
            client = _get_public_clob_client()
            trades = client.get_trades(token_id, limit=min(lookback, 500)) or []
            if not trades:
                return {"token_id": token_id, "trades_analyzed": 0, "note": "No recent trades or token may be inactive."}

            total_notional = 0.0
            total_size = 0.0
            count = 0
            prices = []
            for t in trades:
                price = _safe_float(t.get("price") or t.get("p"))
                size = _safe_float(t.get("size") or t.get("amount") or t.get("s"))
                if price > 0 and size > 0:
                    total_notional += price * size
                    total_size += size
                    count += 1
                    prices.append(price)

            avg_price = sum(prices) / len(prices) if prices else 0
            avg_trade_size = total_size / count if count else 0

            return {
                "token_id": token_id,
                "trades_analyzed": count,
                "total_notional_usdc": round(total_notional, 2),
                "total_shares": round(total_size, 4),
                "avg_trade_price": round(avg_price, 6),
                "avg_trade_size_shares": round(avg_trade_size, 6),
                "lookback_requested": lookback,
                "note": "Recent public trades only. For deeper history use get_price_history + get_realtime_helper_patterns().",
            }
        except Exception as e:
            return {"token_id": token_id, "error": str(e), "see_also": "get_mcp_health_report() and get_clob_docs()"}

    @mcp.tool
    def price_volatility(token_id: str, history: int = 50) -> dict:
        """
        Simple realized volatility estimate from recent price history (or trades).

        Returns price range, std-dev proxy (pure Python), recent change.
        No external libs. For production strategies combine with WS price_change stream.

        See get_mcp_health_report(), get_price_history(), get_realtime_trading_guide(),
        and parse_ws_event on price_change events.
        """
        try:
            client = _get_public_clob_client()
            # Prefer recent trades for granularity; fallback to history endpoint if needed
            trades = client.get_trades(token_id, limit=min(max(history, 20), 200)) or []
            prices: list[float] = []
            for t in trades:
                p = _safe_float(t.get("price") or t.get("p") or t.get("last_trade_price"))
                if p > 0:
                    prices.append(p)

            if len(prices) < 3:
                # Fallback: try price history
                hist = client.get_prices_history(token_id, interval="1m") or {}  # type: ignore
                # Simplified extraction
                for item in (hist.get("history") or hist.get("prices") or [])[:history]:
                    p = _safe_float(item.get("price") or item.get("p") or item.get("close"))
                    if p > 0:
                        prices.append(p)

            if len(prices) < 3:
                return {"token_id": token_id, "error": "Insufficient price points for volatility"}

            prices = prices[-history:]
            mean_p = sum(prices) / len(prices)
            var = sum((p - mean_p) ** 2 for p in prices) / max(1, len(prices) - 1)
            std = var ** 0.5
            recent_change = (prices[-1] - prices[0]) / prices[0] * 100 if prices[0] > 0 else 0
            high = max(prices)
            low = min(prices)
            range_pct = (high - low) / mean_p * 100 if mean_p > 0 else 0

            return {
                "token_id": token_id,
                "samples": len(prices),
                "mean_price": round(mean_p, 6),
                "std_dev": round(std, 6),
                "range_pct": round(range_pct, 3),
                "recent_change_pct": round(recent_change, 3),
                "high": round(high, 6),
                "low": round(low, 6),
                "note": "Pure-Python sample std. Higher frequency via WS (listen_for_ws_events + price_change). Use for suggested_position_size risk scaling.",
            }
        except Exception as e:
            return {"token_id": token_id, "error": str(e), "see": "get_mcp_health_report(), get_price_history()"}

    @mcp.tool
    def suggested_position_size(
        token_id: str,
        risk_pct: float = 0.02,
        account_usdc: float = 10000.0,
        max_slippage_bps: float = 150.0,
    ) -> dict:
        """
        Recommends a conservative position size in USDC for the given token
        using current liquidity, simple vol proxy, and risk budget.

        risk_pct = fraction of account willing to risk on this idea.
        Combines depth from liquidity walk + rough vol scaling.

        Always validate with liquidity_analysis + risk_check + paper sessions first.
        See get_mcp_health_report(), get_realtime_trading_guide(), get_polymarket_llms_txt().
        """
        try:
            book = _get_orderbook_data(token_id)
            # Rough depth at ~best levels
            usable_depth = min(book["bids_depth_usdc"], book["asks_depth_usdc"]) or 500
            # Conservative cap: 8% of visible depth or risk budget
            risk_budget = account_usdc * max(0.001, min(0.25, risk_pct))
            vol_adj = 0.6  # simple dampener (would use price_volatility in real)
            suggested = min(risk_budget, usable_depth * 0.08) * vol_adj

            # Cap further if slippage would be high on that size
            liq = liquidity_analysis(token_id, suggested)  # reuse our own tool
            buy_slip = (liq.get("buy_side") or {}).get("slippage_bps") or 0
            if buy_slip > max_slippage_bps:
                suggested = suggested * (max_slippage_bps / max(1, buy_slip))

            suggested = max(10.0, min(suggested, account_usdc * 0.15))
            return {
                "token_id": token_id,
                "suggested_size_usdc": round(suggested, 2),
                "risk_pct_used": risk_pct,
                "account_usdc": account_usdc,
                "rationale": "Capped at ~8% of visible depth * risk budget * vol dampener, further limited by max_slippage target.",
                "max_slippage_target_bps": max_slippage_bps,
                "next": "Run liquidity_analysis on this size, then paper_place_limit_order in a simulation session before live.",
            }
        except Exception as e:
            return {"token_id": token_id, "error": str(e), "fallback": "Use risk_check + liquidity_analysis manually. Consult get_mcp_health_report()."}

    @mcp.tool
    def cross_market_correlation(
        token_id_a: str,
        token_id_b: str,
        lookback: int = 30,
    ) -> dict:
        """
        SIMPLE pure-Python price correlation between two tokens (Pearson on recent mids/trades).

        Returns rough correlation coefficient (-1 to 1). Not a substitute for
        proper time-series analysis or external data feeds. Useful quick screen.

        See get_mcp_health_report(), get_realtime_trading_guide() (multi-market WS patterns),
        get_latest_ws_messages with filters, and get_polymarket_llms_txt().
        """
        try:
            client = _get_public_clob_client()
            prices_a: list[float] = []
            prices_b: list[float] = []

            for tid, bucket in [(token_id_a, prices_a), (token_id_b, prices_b)]:
                trades = client.get_trades(tid, limit=min(lookback * 2, 100)) or []
                for t in trades:
                    p = _safe_float(t.get("price") or t.get("p"))
                    if p > 0:
                        bucket.append(p)
                        if len(bucket) >= lookback:
                            break

            n = min(len(prices_a), len(prices_b))
            if n < 5:
                return {"token_a": token_id_a, "token_b": token_id_b, "error": "Insufficient overlapping samples", "samples": n}

            pa = prices_a[-n:]
            pb = prices_b[-n:]
            mean_a = sum(pa) / n
            mean_b = sum(pb) / n
            cov = sum((pa[i] - mean_a) * (pb[i] - mean_b) for i in range(n))
            var_a = sum((x - mean_a) ** 2 for x in pa)
            var_b = sum((x - mean_b) ** 2 for x in pb)
            denom = (var_a * var_b) ** 0.5
            corr = (cov / denom) if denom > 0 else 0.0

            return {
                "token_a": token_id_a,
                "token_b": token_id_b,
                "samples": n,
                "correlation": round(corr, 4),
                "interpretation": "positive" if corr > 0.3 else ("negative" if corr < -0.3 else "weak/no correlation"),
                "note": "Simple rolling sample. For production use managed WS multi-asset + external analytics. Combine with start_full_market_monitor on related slugs.",
            }
        except Exception as e:
            return {"error": str(e), "see_also": ["get_mcp_health_report()", "get_realtime_helper_patterns()"]}

    @mcp.tool
    def get_market_microstructure(token_id: str) -> dict:
        """
        One-shot rich snapshot of market quality: spread, depth, imbalance,
        recent volume, rough vol, and suggested safe size.

        The highest-signal single analysis call for a token before paper or live trading.

        See get_mcp_health_report() FIRST, get_live_orderbook(), liquidity_analysis(),
        get_realtime_trading_guide(), get_realtime_market_snapshot() (WS + CLOB combined),
        and get_polymarket_llms_txt().
        """
        try:
            book = _get_orderbook_data(token_id)
            spread_bps = None
            if book["best_bid"] and book["best_ask"] and book["best_bid"] > 0:
                spread_bps = ((book["best_ask"] - book["best_bid"]) / ((book["best_bid"] + book["best_ask"]) / 2)) * 10000

            imb = orderbook_imbalance(token_id)
            vol = volume_profile(token_id, 30)
            suggested = suggested_position_size(token_id, 0.015, 10000)

            return {
                "token_id": token_id,
                "best_bid": book["best_bid"],
                "best_ask": book["best_ask"],
                "mid": book["mid"],
                "spread_bps": round(spread_bps, 1) if spread_bps else None,
                "bid_depth_usdc": round(book["bids_depth_usdc"], 2),
                "ask_depth_usdc": round(book["asks_depth_usdc"], 2),
                "imbalance": imb.get("imbalance_score"),
                "imbalance_interpretation": imb.get("interpretation"),
                "recent_volume_notional": vol.get("total_notional_usdc"),
                "recent_trades": vol.get("trades_analyzed"),
                "suggested_conservative_size_usdc": suggested.get("suggested_size_usdc"),
                "microstructure_quality": "good" if (spread_bps or 999) < 80 and book["bids_depth_usdc"] > 2000 else "thin",
                "timestamp_note": "Live snapshot. Refresh frequently via WS or get_latest_ws_messages + get_realtime_market_snapshot.",
            }
        except Exception as e:
            return {"token_id": token_id, "error": str(e), "recovery": "Call get_mcp_health_report() and get_clob_token_ids first."}
