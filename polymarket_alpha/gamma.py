"""
Gamma discovery tools (current V2 Gamma API).
All tools are always visible.
"""

import httpx
from fastmcp import FastMCP

from .config import get_gamma_url


def register_gamma_tools(mcp: FastMCP) -> None:

    @mcp.tool
    async def search_markets(query: str, limit: int = 15, active_only: bool = True) -> list[dict]:
        """
        Search across Polymarket markets and events.

        PURPOSE
        -------
        Primary discovery tool. Use when user mentions any topic, election, crypto, sports, etc.

        RETURNS
        -------
        List of matching markets with slugs, ids, and basic pricing info.
        """
        url = f"{get_gamma_url()}/public-search"
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(url, params={"q": query, "limit": min(limit, 50)})
            return r.json() if r.status_code == 200 else [{"error": r.text}]

    @mcp.tool
    async def get_market_details(slug: str = None, market_id: str = None, token_id: str = None) -> dict:
        """
        Get full market details including clobTokenIds (required for all CLOB operations).

        This is one of the most important tools. Call it after search_markets.
        """
        base = get_gamma_url()
        if slug:
            url = f"{base}/markets/slug/{slug}"
        elif market_id:
            url = f"{base}/markets/{market_id}"
        elif token_id:
            url = f"{base}/markets/token/{token_id}"
        else:
            return {"error": "Provide slug, market_id or token_id"}

        async with httpx.AsyncClient() as client:
            r = await client.get(url)
            return r.json() if r.status_code == 200 else {"error": r.text}

    @mcp.tool
    async def get_active_markets(limit: int = 20, tag: str = None) -> list[dict]:
        """List currently active/tradable markets. Good for browsing hot markets."""
        params = {"active": "true", "closed": "false", "limit": limit}
        if tag:
            params["tag"] = tag
        async with httpx.AsyncClient() as client:
            r = await client.get(f"{get_gamma_url()}/markets", params=params)
            return r.json() if r.status_code == 200 else []

    # Add get_events, get_tags, get_series similarly (abbreviated for now)
    @mcp.tool
    async def get_events(limit: int = 10) -> list[dict]:
        async with httpx.AsyncClient() as client:
            r = await client.get(f"{get_gamma_url()}/events", params={"limit": limit})
            return r.json() if r.status_code == 200 else []

    @mcp.tool
    def get_gamma_docs() -> dict:
        """
        Structured documentation for the Gamma API (Polymarket's market & event discovery layer).

        This is the primary tool for agents to understand parameters, categories, and how to use Gamma
        to discover markets and obtain clobTokenIds for CLOB trading.
        Call this to extract all relevant docs and usage patterns.

        Recommended pattern:
        1. Call get_polymarket_llms_txt(section="gamma") for the latest official docs
        2. Call this tool (get_gamma_docs) for MCP-specific routing, workflows, and parameter guidance
        """
        return {
            "api_name": "Gamma API (V2)",
            "base_url": get_gamma_url(),
            "description": "Official discovery layer for markets, events, tags, and metadata. Always use this first to get slugs and clobTokenIds before any CLOB operations.",
            "categories": [
                "Search & Discovery",
                "Market Lookup (by slug, id, token)",
                "Market Listing & Filtering",
                "Events & Series",
                "Tags & Relationships"
            ],
            "main_endpoints": [
                {
                    "name": "public-search",
                    "path": "/public-search",
                    "method": "GET",
                    "description": "Keyword search across markets and events.",
                    "required_parameters": ["q"],
                    "optional_parameters": ["limit"],
                    "example": "search_markets(query='election', limit=10)",
                    "returns": "Array of markets/events with title, slug, id, volume, liquidity, active status"
                },
                {
                    "name": "market-by-slug",
                    "path": "/markets/slug/{slug}",
                    "method": "GET",
                    "description": "Full market details including clobTokenIds.",
                    "required_parameters": ["slug"],
                    "example": "get_market_details(slug='will-trump-win')",
                    "critical_fields": ["clobTokenIds", "conditionId", "negRisk", "resolutionSource"]
                },
                {
                    "name": "market-by-token",
                    "path": "/markets/token/{token_id}",
                    "method": "GET",
                    "description": "Lookup market using a clobTokenId from orderbook or position.",
                    "required_parameters": ["token_id"]
                },
                {
                    "name": "list-markets",
                    "path": "/markets",
                    "method": "GET",
                    "description": "Paginated/filtered market list.",
                    "optional_parameters": ["active", "closed", "limit", "tag"],
                    "example": "get_active_markets(limit=20, tag='Politics')"
                },
                {
                    "name": "events",
                    "path": "/events",
                    "method": "GET",
                    "description": "List events (groups of related markets).",
                    "optional_parameters": ["limit"]
                }
            ],
            "how_to_use": {
                "step_1": "Use search_markets() or get_active_markets() to discover",
                "step_2": "Call get_market_details() with slug or token_id to get clobTokenIds",
                "step_3": "Pass clobTokenIds to CLOB tools (get_orderbook, place_limit_order, etc.)",
                "step_4": "For resolved markets use redeemable filters in get_positions()"
            },
            "routing_notes": [
                "Gamma is read-only and public — no auth needed.",
                "Gamma provides the human-readable layer; CLOB is the trading execution layer.",
                "Always resolve to clobTokenIds via Gamma before any trading action."
            ]
        }
