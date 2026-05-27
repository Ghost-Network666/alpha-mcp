"""
Authenticated CLOB Portfolio and Trading tools (V2 stack).

These tools are **always visible** in get_capabilities() (even in read-only mode).
They use the official py-clob-client-v2 against the current CLOB V2 endpoint.

When called without proper credentials they return clear, actionable errors
that point agents to polymarket_alpha_setup_guide().
"""

from fastmcp import FastMCP
from py_clob_client_v2.clob_types import OrderArgs, MarketOrderArgs, OrderType, PartialCreateOrderOptions, Side

from .config import (
    get_authenticated_clob_client,
    get_auth_status,
    get_data_client,
    get_user_address,
    require_private_key,
)


def register_authenticated_tools(mcp: FastMCP) -> None:

    @mcp.tool
    def get_balance() -> dict:
        """
        Returns the authenticated wallet's USDC (collateral) balance and allowances.

        PURPOSE
        -------
        Check how much you can spend before placing orders.

        AUTH
        ----
        Requires POLYMARKET_PRIVATE_KEY (L1). L2 creds are auto-derived.

        WHEN TO USE
        -----------
        Before any trading session or when deciding position size.
        """
        try:
            client = get_authenticated_clob_client()
            return client.get_balance_allowance()  # type: ignore[attr-defined]
        except Exception as e:
            status = get_auth_status()
            return {
                "error": str(e),
                "auth_status": status.mode,
                "action": "Call polymarket_alpha_setup_guide(platform='hermes') for setup instructions."
            }

    @mcp.tool
    def get_positions(size_threshold: float = 0.01, redeemable_only: bool = False) -> dict:
        """
        RICH aggregated portfolio view (replaces previous per-token basic output).

        Uses Polymarket Data API for positions + value + PnL signals.
        Returns summary (total value, #positions) + detailed list with title, outcome, size,
        current value, cashPnL, redeemable/mergeable flags, and neg_risk flag.

        This is the recommended tool for "what are my positions?" queries.
        """
        try:
            addr = get_user_address()
            if not addr:
                status = get_auth_status()
                return {
                    "error": "No POLYMARKET_PRIVATE_KEY available to derive wallet address.",
                    "auth_mode": status.mode,
                    "hint": "Set POLYMARKET_PRIVATE_KEY and call polymarket_alpha_setup_guide() if needed."
                }

            dc = get_data_client()
            if dc is None:
                return {
                    "error": "polymarket-apis package not available for rich positions.",
                    "hint": "pip install -U polymarket-apis (already declared in pyproject.toml)"
                }

            positions = dc.get_positions(
                user=addr,
                size_threshold=size_threshold,
                redeemable=redeemable_only,
                limit=200,
                sort_by="CURRENT",
                sort_direction="DESC",
            )

            # Build rich response using correct Pydantic model fields
            enriched = []
            total_current_value = 0.0
            total_cash_pnl = 0.0
            redeemable_count = 0
            mergeable_count = 0

            for p in positions:
                # Use model_dump for robustness against model changes
                data = p.model_dump() if hasattr(p, "model_dump") else p.__dict__

                entry = {
                    "title": data.get("title"),
                    "slug": data.get("slug"),
                    "condition_id": data.get("condition_id"),
                    "event_id": data.get("event_id"),
                    "outcome": data.get("outcome"),
                    "token_id": data.get("token_id"),
                    "size_tokens": data.get("size"),
                    "avg_price": data.get("avg_price"),
                    "current_price": data.get("current_price"),
                    "current_value_usdc": data.get("current_value"),
                    "initial_value_usdc": data.get("initial_value"),
                    "cash_pnl": data.get("cash_pnl"),
                    "percent_pnl": data.get("percent_pnl"),
                    "redeemable": data.get("redeemable", False),
                    "mergeable": data.get("mergeable", False),
                    "negative_risk": data.get("negative_risk", False),
                }
                enriched.append(entry)

                # Aggregate stats
                try:
                    if entry["current_value_usdc"]:
                        total_current_value += float(entry["current_value_usdc"])
                    if entry["cash_pnl"]:
                        total_cash_pnl += float(entry["cash_pnl"])
                except Exception:
                    pass

                if entry["redeemable"]:
                    redeemable_count += 1
                if entry["mergeable"]:
                    mergeable_count += 1

            value_resp = None
            try:
                value_resp = dc.get_value(user=addr)
            except Exception:
                pass

            summary = {
                "wallet": addr,
                "num_positions": len(enriched),
                "total_current_value_usdc": round(total_current_value, 2),
                "total_cash_pnl": round(total_cash_pnl, 2),
                "redeemable_positions": redeemable_count,
                "mergeable_positions": mergeable_count,
                "data_api_value": value_resp.model_dump() if value_resp else None,
                "filters": {"size_threshold": size_threshold, "redeemable_only": redeemable_only},
            }

            return {
                "summary": summary,
                "positions": enriched,
                "recommendations": [
                    "Use gasless_redeem on redeemable positions after resolution.",
                    "Call gasless_approve_all() before first gasless on-chain actions if using proxy/Safe.",
                    "Call gasless_get_balances() to see actual on-chain balances for your gasless wallet.",
                    "For moving funds: gasless_transfer_pusd / gasless_transfer_token.",
                    "For anything else: gasless_execute_custom() (advanced).",
                ]
            }
        except Exception as e:
            return {
                "error": str(e),
                "hint": "Ensure POLYMARKET_PRIVATE_KEY is valid. Rich positions require the polymarket-apis data client."
            }

    @mcp.tool
    def get_open_orders() -> list[dict]:
        """All currently resting limit orders for this wallet."""
        try:
            client = get_authenticated_clob_client()
            return client.get_open_orders() or []  # type: ignore[attr-defined]
        except Exception as e:
            return [{"error": str(e)}]

    @mcp.tool
    def get_fills(limit: int = 50) -> list[dict]:
        """Recent fills/trades executed by this wallet."""
        try:
            client = get_authenticated_clob_client()
            return client.get_trades(limit=limit) or []  # type: ignore[attr-defined]
        except Exception as e:
            return [{"error": str(e)}]

    # =====================
    # Trading Tools
    # =====================

    @mcp.tool
    def place_limit_order(
        token_id: str,
        side: str,
        price: float,
        size: float,
    ) -> dict:
        """
        Place a GTC limit order.

        Best practice: Call liquidity_analysis + risk_check first.

        AUTH: Requires POLYMARKET_PRIVATE_KEY.
        Gasless note: CLOB orders themselves are off-chain signed. Gasless relayer is mainly for on-chain CTF actions.
        """
        try:
            require_private_key()
            client = get_authenticated_clob_client()

            order_side = Side.BUY if side.lower() == "buy" else Side.SELL

            order = client.create_and_post_order(
                order_args=OrderArgs(
                    token_id=token_id,
                    price=price,
                    side=order_side,
                    size=size,
                ),
                options=PartialCreateOrderOptions(tick_size="0.01"),
                order_type=OrderType.GTC,
            )
            return {"status": "success", "order": order}
        except Exception as e:
            status = get_auth_status()
            return {
                "status": "error",
                "message": str(e),
                "auth_status": status.mode,
                "recommended_action": "Call polymarket_alpha_setup_guide(platform='hermes') if credentials are missing."
            }

    @mcp.tool
    def place_market_order(
        token_id: str,
        side: str,
        amount_usdc: float,
    ) -> dict:
        """
        Place a market order (usually FOK).

        Use when you need immediate execution.
        """
        try:
            require_private_key()
            client = get_authenticated_clob_client()

            order_side = Side.BUY if side.lower() == "buy" else Side.SELL

            order = client.create_and_post_market_order(
                order_args=MarketOrderArgs(
                    token_id=token_id,
                    amount=amount_usdc,
                    side=order_side,
                    order_type=OrderType.FOK,
                ),
                options=PartialCreateOrderOptions(tick_size="0.01"),
            )
            return {"status": "success", "order": order}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    @mcp.tool
    def cancel_order(order_id: str) -> dict:
        """Cancel a single open order by ID."""
        try:
            client = get_authenticated_clob_client()
            result = client.cancel_order(order_id)  # type: ignore[attr-defined]
            return {"status": "success", "result": result}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    @mcp.tool
    def cancel_all_orders() -> dict:
        """
        Cancel every open order for this wallet.

        Use with caution — this is a bulk operation.
        """
        try:
            client = get_authenticated_clob_client()
            result = client.cancel_all_orders()  # type: ignore[attr-defined]
            return {"status": "success", "result": result}
        except Exception as e:
            return {"status": "error", "message": str(e)}
