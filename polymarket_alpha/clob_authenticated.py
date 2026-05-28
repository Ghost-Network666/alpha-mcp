"""
Authenticated CLOB Portfolio and Trading tools (current production implementation).

These tools are **always visible** in get_capabilities() (even in read-only mode).
They use the official py-clob-client-v2 against the current CLOB endpoint.

Polymarket now recommends the unified `polymarket-client` for new work.
High-level MCP tools + get_unified_sdk_guidance() give the complete picture.
See get_client() only for advanced drops to the current v2 client.

Preferred native entry points for agent harnesses (Hermes etc.):
- check_clob_auth(include_raw=True)   ← call this FIRST every session
- get_clob_balance(), get_client()
- get_polygon_erc20_balance(token_address)
- place_order / place_market_order / cancel_* (high level wrappers exist)

Direct class access is discouraged. Use only the @mcp.tool functions.
"""

from fastmcp import FastMCP
from py_clob_client_v2.clob_types import OrderArgs, MarketOrderArgs, OrderType, PartialCreateOrderOptions, Side

import os

from .config import (
    PUSD_ADDRESS,
    get_authenticated_clob_client,
    get_auth_status,
    get_data_client,
    get_official_credentials,
    get_user_address,
    require_private_key,
)


def _get_env(name: str):
    val = os.environ.get(name)
    return val.strip() if val else None


def get_clob_auth_diagnostic(include_raw: bool = False) -> dict:
    """
    Core reusable diagnostic logic for CLOB auth readiness.
    Extracted so get_mcp_health_report() and the check_clob_auth tool can share it.
    Never leaks secrets.
    """
    from .config import (
        get_authenticated_clob_client,
        get_auth_status,
        get_official_credentials,
    )

    try:
        creds = get_official_credentials()
        client = get_authenticated_clob_client()
        status = get_auth_status()

        # Basic validation calls
        diag: dict = {
            "status": "ok",
            "auth_mode": status.mode,
            "has_pk": bool(creds["pk"]),
            "has_direct_l2_creds": creds["has_direct_clob_creds"],
            "using_legacy_names": creds["using_legacy_names"],
            "effective_signature_type": creds["signature_type"] or 3,
            "funder": creds["funder"],
            "clob_host": creds["clob_url"] or "https://clob.polymarket.com",
        }

        # Try to get a balance to prove the L2 creds actually work against the CLOB
        try:
            bal = client.get_balance_allowance()  # type: ignore[attr-defined]
            diag["balance_check"] = "success"
            diag["balance_allowance"] = bal
        except Exception as be:
            diag["balance_check"] = f"failed: {be}"
            diag["note"] = "L2 credentials may be invalid or rate-limited. Re-generate via Polymarket UI if needed."

        # Surface the address the CLOB sees you as (very important for sig_type=3)
        try:
            # py-clob-client-v2 often exposes .get_address() or similar after auth
            if hasattr(client, "get_address"):
                diag["clob_reported_address"] = client.get_address()  # type: ignore
            elif hasattr(client, "address"):
                diag["clob_reported_address"] = client.address  # type: ignore
        except Exception:
            pass

        if include_raw:
            diag["raw_credentials_preview"] = {
                "pk_present": bool(creds["pk"]),
                "api_key_present": bool(creds["clob_api_key"]),
                "funder_present": bool(creds["funder"]),
            }
            # Never leak secrets
            diag["raw_note"] = "Secrets are never returned. Only presence + config flags."

        diag["recommendation"] = (
            "If balance_check=success you are ready. "
            "Next: get_clob_balance(), get_mid_price(token_id), get_live_orderbook(token_id), then place_order(). "
            "Make sure you copied .env.example → .env and replaced all placeholders with real CLOB credentials."
        )

        # Surface the two major undocumented / easy-to-miss sig_type=3 gotchas
        sig = diag.get("effective_signature_type")
        if sig == 3 or str(sig) == "3":
            diag["signature_type_3_warnings"] = [
                "For signature_type=3 you MUST use your DEPOSIT WALLET address as the 'funder', not your EOA.",
                "Find it at polymarket.com → Profile → Wallet (different 0x address from your MetaMask/EOA).",
                "CRITICAL: You must place at least ONE manual order via the official Polymarket website UI first.",
                "Without that manual UI trade, your first API order will very likely fail with 'signer mismatch'.",
                "This is a common source of pain (real root cause of many reports around clob-client issue #70).",
                "After one small manual trade in the UI, API orders should work normally with the correct funder."
            ]

        return diag

    except Exception as e:
        status = get_auth_status()
        return {
            "status": "error",
            "error": str(e),
            "auth_mode": status.mode,
            "action": "Ensure PK (and optionally CLOB_API_KEY/CLOB_SECRET/CLOB_PASS_PHRASE + FUNDER) are correctly set inside your mcp_servers env block. Call polymarket_alpha_setup_guide(platform='hermes').",
        }


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
                    "Call gasless_get_balances() or get_pusd_balance() to see actual on-chain pUSD for your gasless wallet.",
                    "For moving funds: gasless_transfer_pusd / gasless_transfer_token.",
                    "Note: get_clob_balance() = CLOB trading power. get_pusd_balance() = on-chain Polygon collateral.",
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

        Best practice: Call get_clob_token_ids() (from Gamma) + liquidity_analysis + risk_check first.

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

    # =====================================================================
    # NEW HIGH-LEVEL / DIAGNOSTIC TOOLS FOR SIMPLE NATIVE HARNESS USAGE
    # These are the preferred entry points per the "much simpler native use" model.
    # =====================================================================

    @mcp.tool
    def check_clob_auth(include_raw: bool = False) -> dict:
        """
        MANDATORY FIRST CALL after MCP load for any CLOB trading session.

        Verifies that PK + L2 credentials are valid, reports the effective
        trading address, signature_type (defaults to 3), funder (if used),
        and whether orders can be placed.

        Set include_raw=true for full client diagnostic dump (useful for debugging).
        """
        # Delegate to the shared pure implementation (also used by get_mcp_health_report)
        return get_clob_auth_diagnostic(include_raw=include_raw)

    @mcp.tool
    def get_clob_balance(refresh: bool = False) -> dict:
        """
        Preferred high-level balance tool for CLOB trading (replaces/aliases get_balance).

        Returns USDC collateral available on the authenticated CLOB account.
        Use refresh=true to force a fresh network call.
        """
        try:
            client = get_authenticated_clob_client()
            # Current production (py-clob-client-v2) get_balance_allowance is the source of truth for CLOB trading power.
            # See get_unified_sdk_guidance() for the official unified SDK alternative.
            result = client.get_balance_allowance()  # type: ignore[attr-defined]
            return {
                "balance": result,
                "note": "This is the collateral the CLOB sees for order placement. For on-chain gasless wallet balances use gasless_get_balances().",
            }
        except Exception as e:
            return {"error": str(e), "hint": "Call check_clob_auth(include_raw=true) first."}

    @mcp.tool
    def get_client() -> dict:
        """
        Returns a lightweight handle + metadata for the underlying authenticated ClobClient (py-clob-client-v2).

        For advanced agents that need to drop down to raw py-clob-client-v2 methods.
        For the official recommended unified SDK direction (`polymarket-client`), call get_unified_sdk_guidance() instead.
        Most users should stick to the high-level MCP tools (place_order, get_clob_balance, etc.).
        """
        try:
            client = get_authenticated_clob_client()
            creds = get_official_credentials()
            return {
                "type": "py_clob_client_v2.ClobClient",
                "host": getattr(client, "host", None),
                "chain_id": getattr(client, "chain_id", 137),
                "signature_type": creds["signature_type"] or 3,
                "funder": creds["funder"],
                "has_direct_l2_creds": creds["has_direct_clob_creds"],
                "warning": "Do not cache this client across sessions. Use the high-level MCP tools for normal operation.",
                "advanced_usage": "client.create_and_post_order(...) etc. — only for power users.",
            }
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool
    def get_polygon_erc20_balance(token_address: str, owner_address: str | None = None) -> dict:
        """
        Generic ERC-20 balance check on Polygon.

        If owner_address is omitted, uses the address derived from PK.
        This is the **on-chain** view.

        IMPORTANT: Polymarket uses **pUSD** (not raw USDC) as the collateral token
        for all on-chain gasless actions (split, merge, redeem, transfers).
        Use get_pusd_balance() for the most common case.
        """
        try:
            import httpx
            from eth_account import Account

            owner = owner_address
            if not owner:
                pk = require_private_key()
                try:
                    owner = Account.from_key(pk).address
                except Exception:
                    return {"error": "Could not derive owner address from PK"}

            rpc_url = _get_env("POLYGON_RPC_URL") or "https://polygon-rpc.com"

            data = "0x70a08231" + owner[2:].rjust(64, "0")
            payload = {
                "jsonrpc": "2.0",
                "method": "eth_call",
                "params": [{"to": token_address, "data": data}, "latest"],
                "id": 1,
            }

            with httpx.Client(timeout=12) as http:
                resp = http.post(rpc_url, json=payload)
                result = resp.json()
                if "error" in result:
                    return {"error": result["error"], "rpc": rpc_url}

                hex_bal = result.get("result", "0x0")
                balance_wei = int(hex_bal, 16) if hex_bal else 0

                decimals_note = "pUSD and USDC use 6 decimals. Most other tokens use 18."
                return {
                    "owner": owner,
                    "token": token_address,
                    "balance_wei": str(balance_wei),
                    "balance_hex": hex_bal,
                    "rpc_used": rpc_url,
                    "note": decimals_note,
                }
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool
    def get_pusd_balance(owner_address: str | None = None) -> dict:
        """
        Convenience wrapper for pUSD balance on Polygon.

        pUSD (0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB) is Polymarket's
        official collateral token on Polygon. This is what your gasless wallet
        holds for split/merge/redeem operations.

        This is the on-chain balance (different from get_clob_balance which is
        the CLOB exchange's view of your trading collateral).
        """
        return get_polygon_erc20_balance(PUSD_ADDRESS, owner_address)

    # Backwards-compatible aliases (so old agent prompts continue to work)
    @mcp.tool
    def get_balance() -> dict:
        """Legacy alias for get_clob_balance(). Prefer get_clob_balance() in new native flows."""
        try:
            client = get_authenticated_clob_client()
            result = client.get_balance_allowance()  # type: ignore[attr-defined]
            return {"balance": result, "note": "Legacy alias — prefer the get_clob_balance tool going forward."}
        except Exception as e:
            return {"error": str(e)}
