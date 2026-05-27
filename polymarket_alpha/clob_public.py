"""
Public CLOB market data tools (V2 stack via py-clob-client-v2).
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
        MCP-structured documentation for the Polymarket CLOB (public data + authenticated trading via py-clob-client-v2).

        WHEN TO USE: After get_polymarket_llms_txt() when preparing CLOB calls, clarifying auth requirements (only PRIVATE_KEY needed), order parameters, or confirming the mandatory "Gamma first → CLOB second" routing.

        RETURNS: dict with api_name, categories, public_endpoints, authenticated_endpoints, how_to_use steps, authentication_notes, routing_notes.
        """
        return {
            "api_name": "CLOB V2 (py-clob-client-v2)",
            "base_url": "https://clob.polymarket.com",
            "description": "Execution layer for order books, limit/market orders, and authenticated portfolio. Orders are signed off-chain. Gasless relayer is NOT needed for CLOB trading.",
            "categories": [
                "Public Market Data (no auth)",
                "Authenticated Portfolio & Orders",
                "Order Placement",
                "Order Cancellation"
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
                    "name": "place_limit_order",
                    "required_parameters": ["token_id", "side", "price", "size"],
                    "auth_required": "POLYMARKET_PRIVATE_KEY only"
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
                }
            ],
            "how_to_use": {
                "step_1": "Get clobTokenId from Gamma (get_market_details or get_gamma_docs)",
                "step_2": "Use public tools for prices/liquidity (get_orderbook, get_price)",
                "step_3": "For trading: ensure POLYMARKET_PRIVATE_KEY is set, then place_limit_order or place_market_order",
                "step_4": "Monitor with get_open_orders + get_fills"
            },
            "authentication_notes": [
                "Only POLYMARKET_PRIVATE_KEY is needed for full CLOB trading.",
                "No gas or Relayer keys required for order placement/cancellation.",
                "Gasless tools are only for on-chain CTF actions (redeem, split, etc.)."
            ],
            "routing_notes": [
                "Gamma first → CLOB second. Never trade without clobTokenIds from Gamma.",
                "CLOB is for execution; Gamma is for discovery and metadata."
            ]
        }
