"""
Analysis tools. All visible in read-only mode.
"""

from fastmcp import FastMCP


def register_analysis_tools(mcp: FastMCP) -> None:

    @mcp.tool
    def calculate_implied_probability(price: float, fee_bps: int = 0) -> dict:
        """Convert market price (0-1) to implied probability."""
        prob = price * 100
        return {"price": price, "implied_probability_pct": round(prob, 2)}

    @mcp.tool
    def liquidity_analysis(token_id: str, notional_usdc: float = 1000) -> dict:
        """
        Estimates slippage for a given notional size.
        Call this before trading.
        """
        return {
            "token_id": token_id,
            "requested_size_usdc": notional_usdc,
            "note": "Full implementation walks the orderbook. Use get_orderbook for raw data."
        }

    @mcp.tool
    def risk_check(proposed_size_usdc: float, token_id: str = None) -> dict:
        """
        Pre-trade risk assessment.
        MCP strongly recommends calling this before any place_*_order.
        """
        warnings = []
        if proposed_size_usdc > 5000:
            warnings.append("Large size. Consider splitting or checking liquidity first.")
        return {"size": proposed_size_usdc, "warnings": warnings, "recommendation": "Proceed with caution if warnings present."}
