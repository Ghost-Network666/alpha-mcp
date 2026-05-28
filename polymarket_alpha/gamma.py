"""
Gamma discovery tools (current V2 Gamma API).
All tools are always visible.

This module ensures the MCP has strong, reliable coverage of:
- Market discovery & parameters
- Events (with embedded markets)
- Token ID / condition ID → clobTokenIds extraction (the critical bridge to CLOB trading)
"""

import json
from typing import Any, Optional

import httpx
from fastmcp import FastMCP

from .config import get_gamma_url


def _safe_json_parse_clob_tokens(raw: Any) -> list[str]:
    """Safely turn Gamma's clobTokenIds (string or list) into a clean Python list."""
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(x) for x in raw]
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return [str(x) for x in parsed]
        except Exception:
            pass
        # Sometimes it's a single token as string (rare)
        return [raw] if raw else []
    return []


def _gamma_get(path: str, params: Optional[dict] = None, timeout: float = 15.0) -> dict:
    """Centralized, defensive GET against Gamma with consistent error shape."""
    base = get_gamma_url()
    url = f"{base}{path}"
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            r = client.get(url, params=params or {})
            if r.status_code == 200:
                return r.json()
            return {
                "error": f"Gamma API returned {r.status_code}",
                "url": url,
                "body": r.text[:500] if r.text else None,
            }
    except httpx.TimeoutException:
        return {"error": "Gamma request timed out", "url": url}
    except Exception as e:
        return {"error": str(e), "url": url}


def quick_gamma_health_check() -> dict:
    """
    Fast reachability + basic functionality ping for Gamma API.
    Used by get_mcp_health_report() for credential-independent connectivity diagnostics.
    Pings /markets and /events with tiny limits and short timeouts.
    """
    base = get_gamma_url()
    endpoints = {
        "/markets": {"params": {"limit": "1", "active": "true"}},
        "/events": {"params": {"limit": "1", "active": "true"}},
    }
    results: dict[str, Any] = {}
    all_ok = True

    for path, cfg in endpoints.items():
        data = _gamma_get(path, cfg["params"], timeout=6.0)
        ok = "error" not in data and not (isinstance(data, dict) and data.get("error"))
        if not ok:
            all_ok = False
        results[path] = {
            "ok": ok,
            "error": data.get("error") if isinstance(data, dict) and "error" in data else None,
            "response_type": type(data).__name__,
            "has_data": bool(data) and not (isinstance(data, dict) and "error" in data),
        }

    return {
        "overall_reachable": all_ok,
        "gamma_base_url": base,
        "endpoints_tested": results,
        "note": "Quick ping only — full discovery uses search_markets / get_events etc.",
    }


def register_gamma_tools(mcp: FastMCP) -> None:

    # -------------------------------------------------------------------------
    # Core Discovery Tools (fixed + hardened)
    # -------------------------------------------------------------------------

    @mcp.tool
    async def search_markets(
        query: str,
        limit: int = 15,
        active_only: bool = True,
        tag: str | None = None,
    ) -> dict:
        """
        Primary keyword search across markets and events.

        IMPORTANT: The response shape is usually {"events": [...]} where each event
        contains a "markets" array. Each market has "clobTokenIds" (stringified JSON array).

        Use get_clob_token_ids() after this for clean token extraction.
        """
        params: dict[str, Any] = {"q": query, "limit": min(limit, 50)}
        if active_only:
            params["active"] = "true"
        if tag:
            params["tag"] = tag

        data = _gamma_get("/public-search", params)
        if "error" in data:
            return data
        return {"results": data, "note": "clobTokenIds inside markets[] are usually stringified JSON arrays — use get_clob_token_ids() for parsed lists."}

    @mcp.tool
    async def get_market_details(
        slug: str | None = None,
        market_id: str | None = None,
        token_id: str | None = None,
        condition_id: str | None = None,
    ) -> dict:
        """
        Full market metadata including the critical clobTokenIds.

        Supports four lookup modes. condition_id is extremely useful when coming from
        on-chain / gasless context or neg-risk markets.
        """
        base = get_gamma_url()
        if slug:
            path = f"/markets/slug/{slug}"
        elif market_id:
            path = f"/markets/{market_id}"
        elif token_id:
            path = f"/markets/token/{token_id}"
        elif condition_id:
            # Gamma supports ?condition_id=...
            data = _gamma_get("/markets", {"condition_id": condition_id})
            return data if isinstance(data, dict) else {"markets": data}
        else:
            return {"error": "Provide one of: slug, market_id, token_id, or condition_id"}

        data = _gamma_get(path)
        if "error" not in data and isinstance(data, dict):
            # Attach a parsed convenience field
            data["_parsed_clob_token_ids"] = _safe_json_parse_clob_tokens(data.get("clobTokenIds"))
        return data

    @mcp.tool
    async def get_active_markets(
        limit: int = 20,
        tag: str | None = None,
        closed: bool = False,
        new_only: bool = False,
    ) -> list[dict]:
        """Browse currently tradable markets with common filters."""
        params: dict[str, Any] = {
            "active": "true",
            "closed": "true" if closed else "false",
            "limit": min(limit, 100),
        }
        if tag:
            params["tag"] = tag
        if new_only:
            params["new"] = "true"

        data = _gamma_get("/markets", params)
        return data if isinstance(data, list) else [data]

    # -------------------------------------------------------------------------
    # Events (greatly strengthened — user explicitly asked for events coverage)
    # -------------------------------------------------------------------------

    @mcp.tool
    async def get_events(
        limit: int = 20,
        active: bool = True,
        closed: bool = False,
        tag: str | None = None,
        featured: bool | None = None,
        slug: str | None = None,
        event_id: str | None = None,
        volume_num_min: int | None = None,
    ) -> list[dict]:
        """
        List events (the containers that group related markets).

        Events are the best way to discover coherent groups (e.g. "Presidential Election 2028",
        "NBA Finals", "What will happen before GTA VI?").

        Each returned event usually contains a "markets" array with full clobTokenIds.
        """
        params: dict[str, Any] = {"limit": min(limit, 100)}
        if active:
            params["active"] = "true"
        if closed:
            params["closed"] = "true"
        if tag:
            params["tag"] = tag
        if featured is not None:
            params["featured"] = "true" if featured else "false"
        if slug:
            params["slug"] = slug
        if event_id:
            params["id"] = event_id
        if volume_num_min:
            params["volume_num_min"] = volume_num_min

        data = _gamma_get("/events", params)
        return data if isinstance(data, list) else [data]

    @mcp.tool
    async def get_event_details(
        slug: str | None = None,
        event_id: str | None = None,
    ) -> dict:
        """
        Get a single event + all its child markets with parsed clobTokenIds.

        This is the highest-signal Gamma tool for most agents. Returns the event
        object plus a clean "markets_with_tokens" list ready for CLOB calls.
        """
        if not slug and not event_id:
            return {"error": "Provide slug or event_id"}

        # First try to get the event list filtered, then pick the right one
        params = {}
        if slug:
            params["slug"] = slug
        if event_id:
            params["id"] = event_id

        events = _gamma_get("/events", params)
        if "error" in events:
            return events

        if not isinstance(events, list) or not events:
            # Fallback: try direct /events/{id} style if we have id
            if event_id:
                direct = _gamma_get(f"/events/{event_id}")
                if "error" not in direct:
                    events = [direct]
            if not events:
                return {"error": "Event not found"}

        event = events[0] if isinstance(events, list) else events

        # Enrich every market inside the event with parsed token ids
        enriched_markets = []
        for m in event.get("markets", []) or []:
            enriched = dict(m)  # shallow copy
            enriched["_clob_token_ids"] = _safe_json_parse_clob_tokens(m.get("clobTokenIds"))
            enriched_markets.append(enriched)

        event = dict(event)
        event["markets_with_tokens"] = enriched_markets
        event["parsed_market_count"] = len(enriched_markets)
        return event

    # -------------------------------------------------------------------------
    # Token ID / Condition ID helpers (the #1 handoff pain point)
    # -------------------------------------------------------------------------

    @mcp.tool
    async def get_clob_token_ids(
        slug: str | None = None,
        market_id: str | None = None,
        token_id: str | None = None,
        condition_id: str | None = None,
    ) -> dict:
        """
        THE RECOMMENDED WAY to go from any Gamma identifier to ready-to-trade clobTokenIds.

        Returns a clean normalized object:
        {
          "condition_id": "...",
          "clob_token_ids": ["0x..yes", "0x..no"],
          "neg_risk": bool,
          "question": "...",
          "slug": "...",
          "active": true,
          ...
        }

        Call this right after search_markets or get_market_details before touching any CLOB tool.
        """
        details = await get_market_details(
            slug=slug, market_id=market_id, token_id=token_id, condition_id=condition_id
        )

        if "error" in details:
            return details

        # Handle both single object and list responses (condition_id case)
        market = details
        if isinstance(details, list) and details:
            market = details[0]
        if isinstance(details, dict) and "markets" in details and details["markets"]:
            market = details["markets"][0]

        tokens = _safe_json_parse_clob_tokens(market.get("clobTokenIds"))

        return {
            "condition_id": market.get("conditionId"),
            "clob_token_ids": tokens,
            "neg_risk": market.get("negRisk", False) or market.get("negRiskAugmented", False),
            "question": market.get("question"),
            "slug": market.get("slug"),
            "active": market.get("active", True),
            "closed": market.get("closed", False),
            "volume": market.get("volume"),
            "liquidity": market.get("liquidity"),
            "outcomes": market.get("outcomes"),
            "source": "gamma",
            "raw_market_id": market.get("id"),
            "note": "Feed clob_token_ids directly into get_orderbook / place_limit_order etc.",
        }

    # -------------------------------------------------------------------------
    # Supporting discovery
    # -------------------------------------------------------------------------

    @mcp.tool
    async def get_tags(limit: int = 100) -> list[dict]:
        """Common tags/categories used for filtering markets and events."""
        data = _gamma_get("/tags", {"limit": min(limit, 200)})
        return data if isinstance(data, list) else [data]

    # -------------------------------------------------------------------------
    # Self-documentation (now much more complete on parameters + workflows)
    # -------------------------------------------------------------------------

    @mcp.tool
    def get_gamma_docs() -> dict:
        """
        Call get_polymarket_llms_txt() + get_mcp_health_report() first for any session.
        The definitive MCP-native reference for Gamma parameters, event handling,
        and the exact token ID extraction workflow required before any CLOB call.
        """
        return {
            "api_name": "Gamma API (V2)",
            "base_url": get_gamma_url(),
            "description": "Read-only discovery layer. Every CLOB trading flow MUST start here to obtain slugs, conditionIds, and especially clobTokenIds.",
            "critical_gotchas": [
                "clobTokenIds is almost always returned as a STRING containing a JSON array. Use get_clob_token_ids() to get a real list.",
                "Many markets live inside Events — get_event_details() or get_events() + markets[] is often more efficient than many individual market calls.",
                "condition_id is the best cross-reference key between on-chain/gasless actions and CLOB.",
                "Use active=true + closed=false for tradable markets in most cases.",
                "On Polygon, Polymarket uses pUSD (0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB) as the collateral token for gasless actions, not raw USDC."
            ],
            "recommended_workflow": [
                "1. search_markets(query) or get_active_markets(tag=...) or get_events(tag=...)",
                "2. get_clob_token_ids(slug=...)  ← the single best next step",
                "3. (optional) get_event_details() when you want the whole group + all tokens at once",
                "4. get_live_orderbook / get_mid_price / liquidity_analysis using the clean token list",
                "5. Only then place_limit_order / place_market_order"
            ],
            "main_tools": {
                "search_markets": {
                    "params": {
                        "query": "required string",
                        "limit": "default 15, max ~50",
                        "active_only": "boolean — now actually respected (adds active=true)",
                        "tag": "optional category filter"
                    },
                    "returns": "Usually {events: [...]} with nested markets"
                },
                "get_market_details": {
                    "params": {
                        "slug": "string",
                        "market_id": "string (numeric id)",
                        "token_id": "clob token id (one of the two)",
                        "condition_id": "0x... conditionId — very powerful for neg-risk & gasless"
                    },
                    "returns": "Full market object. Also attaches _parsed_clob_token_ids when possible."
                },
                "get_events": {
                    "params": {
                        "limit": "default 20",
                        "active": "bool (default true)",
                        "closed": "bool",
                        "tag": "string",
                        "featured": "bool",
                        "slug": "string",
                        "event_id": "string (numeric)",
                        "volume_num_min": "int — minimum volume filter"
                    },
                    "returns": "Array of events. Each usually contains a 'markets' array with clobTokenIds."
                },
                "get_event_details": {
                    "params": {"slug": "or", "event_id": "required"},
                    "returns": "Event + 'markets_with_tokens' (every market has a clean _clob_token_ids list)"
                },
                "get_clob_token_ids": {
                    "params": "slug | market_id | token_id | condition_id (exactly one)",
                    "returns": "Normalized object with 'clob_token_ids': list[str] ready for CLOB tools + condition_id + neg_risk flag"
                },
                "get_active_markets": {
                    "params": {"limit": "20", "tag": "optional", "closed": "bool", "new_only": "bool"}
                },
                "get_tags": {"params": {"limit": "100"}}
            },
            "token_id_extraction": {
                "why_it_matters": "CLOB orderbook, place_order, cancel, etc. all require the exact clobTokenId strings.",
                "best_tool": "Always prefer get_clob_token_ids() over manually parsing responses.",
                "common_pattern": "search_markets → get_clob_token_ids(slug from result) → get_orderbook(token_id[0])"
            },
            "events_vs_markets": {
                "when_to_use_events": "When the user asks about a broad topic (elections, sports leagues, 'what will happen before X'). Events give you the full related set in one call.",
                "when_to_use_market_details": "When you already have a specific slug or saw a single market in search results."
            },
            "see_also_high_value_meta": "For full MCP readiness always call get_mcp_health_report() first. For realtime/event-driven: get_realtime_trading_guide() + get_realtime_helper_patterns() + get_ws_event_driven_patterns(). For SDK direction: get_unified_sdk_guidance(). Complete CLOB surface (incl. WS): get_clob_docs().",
            "official_docs_via_native_tools": "Use list_polymarket_docs() + get_polymarket_doc(path='advanced/neg-risk.md' or 'concepts/positions-tokens.md' etc.) to read the live authoritative .md files for any Gamma/CLOB topic.",
        }
