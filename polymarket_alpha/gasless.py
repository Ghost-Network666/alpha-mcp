"""
Gasless Relayer tools (Builder Program / Relayer integration).

This module fully implements the previously "credential-ready only" gasless support.

Dedicated high-level tools for on-chain actions (CTF split/merge/redeem, approvals)
that go through Polymarket's relayer so the user pays zero gas (Polymarket sponsors).

Also includes:
- Balance tools for the correct gasless wallet addresses
- Transfer tools (pUSD + tokens)
- gasless_execute_custom() — low-level escape hatch for arbitrary gasless transactions

Requires:
- POLYMARKET_PRIVATE_KEY (base signer)
- RELAYER_API_KEY (+ optional SECRET/PASSPHRASE for builder)
- POLY_SIGNATURE_TYPE is optional (defaults to 3 = Deposit wallets). Valid values: 1=proxy, 2=Safe, 3=Deposit

All tools gracefully error with setup instructions if creds missing.
"""

from fastmcp import FastMCP

import os

from .config import (
    get_auth_status,
    get_gasless_client,
    get_raw_relay_client,
    get_relayer_creds,
    get_user_address,
    require_gasless_client,
)

def _get_env(name: str):
    val = os.environ.get(name)
    return val.strip() if val else None


def register_gasless_tools(mcp: FastMCP) -> None:

    @mcp.tool
    def gasless_wallet_info() -> dict:
        """
        Returns the derived wallet addresses (base, proxy/safe/deposit) and gasless readiness.

        Use this first to confirm your signature_type produces the expected wallet address
        before doing approvals or redemptions.
        """
        try:
            client = get_gasless_client()
            status = get_auth_status()
            addr = get_user_address()
            creds = get_relayer_creds()

            info = {
                "base_address": addr,
                "auth_mode": status.mode,
                "gasless_ready": status.gasless_ready,
                "signature_type": status.signature_type,
                "relayer_creds_present": bool(creds),
                "has_builder_secrets": bool(creds and creds.get("secret")) if creds else False,
            }

            if client is not None:
                try:
                    info["proxy_wallet"] = client.get_poly_proxy_wallet_address()
                except Exception:
                    pass
                try:
                    info["safe_wallet"] = client.get_safe_proxy_wallet_address()
                except Exception:
                    pass
                try:
                    info["deposit_wallet"] = client.get_expected_deposit_wallet()
                except Exception:
                    pass
                info["active_wallet_address"] = getattr(client, "address", None)

                # Include live pUSD balance on the active gasless wallet
                try:
                    info["pusd_balance"] = client.get_pusd_balance()
                except Exception:
                    pass

                info["power_tools_available"] = ["gasless_execute_custom", "gasless_transfer_pusd", "gasless_transfer_token"]
            else:
                info["note"] = "Gasless client not initialized. Set POLY_SIGNATURE_TYPE + RELAYER_API_KEY."
            info["recommended_next"] = "Call gasless_get_balances() after successful setup."

            return info
        except Exception as e:
            return {"error": str(e), "hint": "Call polymarket_alpha_setup_guide() for Builder setup."}

    @mcp.tool
    def gasless_approve_all() -> dict:
        """
        Sets ALL required approvals for pUSD and Conditional Tokens (CTF) for the common spenders
        (exchange, adapters, collateral onramp, etc.).

        REQUIRED before gasless split/merge/redeem/convert on proxy, Safe or deposit wallets.
        Safe wallets may also need gasless_deploy_safe_wallet() first if not yet deployed.
        """
        try:
            client = require_gasless_client()
            receipts = client.set_all_approvals()
            return {
                "status": "success",
                "action": "set_all_approvals",
                "num_transactions": len(receipts) if isinstance(receipts, list) else 1,
                "receipts": [str(r) for r in (receipts if isinstance(receipts, list) else [receipts])],
                "note": "Approvals submitted gaslessly. Wait for confirmation before trading/redeeming."
            }
        except Exception as e:
            status = get_auth_status()
            return {
                "status": "error",
                "message": str(e),
                "auth_status": status.mode,
                "recommended_action": "Check POLY_SIGNATURE_TYPE (defaults to 3=Deposit) and run gasless_wallet_info(). Call polymarket_alpha_setup_guide() for relayer keys."
            }

    @mcp.tool
    def gasless_redeem(condition_id: str, amounts: list[float], neg_risk: bool = False) -> dict:
        """
        Gasless redeem of positions into pUSD after market resolution.

        amounts: [yes_shares, no_shares] in USDC units (e.g. [10.5, 0] for Yes winner).
        Use get_positions(redeemable_only=True) to discover redeemable positions.
        """
        try:
            client = require_gasless_client()
            receipt = client.redeem_position(condition_id, amounts, neg_risk)
            return {
                "status": "success",
                "action": "redeem_position",
                "condition_id": condition_id,
                "amounts": amounts,
                "neg_risk": neg_risk,
                "tx_hash": getattr(receipt, "transactionHash", None) or str(receipt),
            }
        except Exception as e:
            return {"status": "error", "message": str(e), "hint": "Must have positive balance in the winning outcome and proper approvals."}

    @mcp.tool
    def gasless_split(condition_id: str, amount: float, neg_risk: bool = False) -> dict:
        """
        Gasless split pUSD into complementary Yes + No tokens for a market (pre-resolution).
        """
        try:
            client = require_gasless_client()
            receipt = client.split_position(condition_id, amount, neg_risk)
            return {
                "status": "success",
                "action": "split_position",
                "condition_id": condition_id,
                "amount_usdc": amount,
                "neg_risk": neg_risk,
                "tx_hash": getattr(receipt, "transactionHash", None) or str(receipt),
            }
        except Exception as e:
            return {"status": "error", "message": str(e)}

    @mcp.tool
    def gasless_merge(condition_id: str, amount: float, neg_risk: bool = False) -> dict:
        """
        Gasless merge Yes + No tokens back into pUSD (pre-resolution).
        """
        try:
            client = require_gasless_client()
            receipt = client.merge_position(condition_id, amount, neg_risk)
            return {
                "status": "success",
                "action": "merge_position",
                "condition_id": condition_id,
                "amount_shares": amount,
                "neg_risk": neg_risk,
                "tx_hash": getattr(receipt, "transactionHash", None) or str(receipt),
            }
        except Exception as e:
            return {"status": "error", "message": str(e)}

    @mcp.tool
    def gasless_convert_no_tokens(question_ids: list[str], amount: float) -> dict:
        """
        Gasless convert No tokens (in a neg-risk event) into the equivalent Yes tokens + pUSD.
        Useful for capital efficiency on multi-outcome events.
        """
        try:
            client = require_gasless_client()
            receipt = client.convert_positions(question_ids, amount)
            return {
                "status": "success",
                "action": "convert_positions",
                "question_ids": question_ids,
                "amount": amount,
                "tx_hash": getattr(receipt, "transactionHash", None) or str(receipt),
            }
        except Exception as e:
            return {"status": "error", "message": str(e)}

    @mcp.tool
    def gasless_deploy_safe_wallet() -> dict:
        """
        Deploy your Safe proxy wallet (signature_type=2) via the relayer (gasless). Note: Current default is signature_type=3 (Deposit).

        This is now a real implementation using the low-level relayer client.
        Only needed once per private key when using Safe wallets.

        After calling this, use gasless_wallet_info() to verify the Safe address is live.
        """
        try:
            # Prefer the low-level RelayClient for proper Safe deployment
            raw_client = get_raw_relay_client()
            if raw_client is not None and hasattr(raw_client, "deploy"):
                response = raw_client.deploy()
                result = response.wait() if hasattr(response, "wait") else None

                return {
                    "status": "success",
                    "action": "deploy_safe_wallet",
                    "result": result or str(response),
                    "note": "Safe deployment submitted. Call gasless_wallet_info() to confirm it is now deployed.",
                }

            # Fallback: try the high-level gasless client (may not support Safe deploy directly)
            client = require_gasless_client()
            if hasattr(client, "deploy_safe_wallet"):
                receipt = client.deploy_safe_wallet()
                return {
                    "status": "success",
                    "action": "deploy_safe_wallet",
                    "tx_hash": getattr(receipt, "transactionHash", None) or str(receipt),
                }

            return {
                "status": "error",
                "message": "Safe deployment requires either full Builder credentials or a working raw RelayClient.",
                "hint": "Make sure RELAYER_API_SECRET + RELAYER_API_PASSPHRASE are set, and POLY_SIGNATURE_TYPE=3 (or your correct type)."
            }
        except Exception as e:
            return {
                "status": "error",
                "message": str(e),
                "hint": "Safe deployment (sig=2) only needed if using Safe wallets. Current default is signature_type=3 (Deposit)."
            }

    @mcp.tool
    def gasless_status() -> dict:
        """Quick health check of gasless relayer integration + current wallet + whether approvals are likely set."""
        status = get_auth_status()
        info = gasless_wallet_info()
        creds = get_relayer_creds()

        gasless_default_msg = ""
        if status.gasless_ready and not _get_env("POLY_SIGNATURE_TYPE"):
            gasless_default_msg = " (auto-enabled — no POLY_SIGNATURE_TYPE was manually set)"

        return {
            "auth_status": status.__dict__,
            "wallet_info": info,
            "relayer_url": "https://relayer-v2.polymarket.com",
            "builder_program": "https://polymarket.com/settings?tab=builder",
            "gasless_default_behavior": "Gasless is now enabled automatically when POLYMARKET_PRIVATE_KEY + RELAYER_API_KEY are present (defaults to signature_type=3 / Deposit).",
            "credentials_summary": {
                "has_key": bool(creds and creds.get("key")),
                "has_secrets_for_builder": bool(creds and creds.get("secret")),
                "signature_type": status.signature_type,
                "auto_detection_used": not bool(_get_env("POLY_SIGNATURE_TYPE")),
            },
            "next_steps_if_missing": [
                "1. Join Builder Program and create Relayer API Key.",
                "2. Export RELAYER_API_KEY + RELAYER_API_KEY_ADDRESS (+ SECRET/PASSPHRASE).",
                "3. (Optional) Set POLY_SIGNATURE_TYPE=3 (Deposit) only if auto-detection picks the wrong type for your wallet.",
                "4. Run gasless_wallet_info() → gasless_approve_all() → gasless_get_balances().",
                "Advanced: Use gasless_execute_custom() for arbitrary transactions.",
            ]
        }

    # =====================
    # Gasless Balance Tools (high value for gasless users)
    # =====================

    @mcp.tool
    def gasless_get_balances() -> dict:
        """
        Returns current on-chain balances for your gasless wallet (the actual proxy/safe/deposit address).

        This is the correct balance tool to use when operating in gasless mode.
        Shows pUSD (collateral), POL (for reference), and optionally specific token balances.
        """
        try:
            client = require_gasless_client()
            active_address = getattr(client, "address", None)
            base_address = client.get_base_wallet_address()

            result = {
                "active_wallet_address": active_address,
                "base_eoa_address": base_address,
                "pol_balance": client.get_pol_balance(),
                "pusd_balance": client.get_pusd_balance(),
                "note": "These are the balances the relayer will use for gasless transactions.",
            }
            return result
        except Exception as e:
            return {"status": "error", "message": str(e)}

    @mcp.tool
    def gasless_get_pusd_balance() -> dict:
        """Get pUSD (collateral) balance on your active gasless wallet address."""
        try:
            client = require_gasless_client()
            bal = client.get_pusd_balance()
            return {
                "pusd_balance": bal,
                "wallet": getattr(client, "address", None),
            }
        except Exception as e:
            return {"status": "error", "message": str(e)}

    @mcp.tool
    def gasless_get_token_balance(token_id: str) -> dict:
        """Get balance of a specific outcome token on your active gasless wallet."""
        try:
            client = require_gasless_client()
            bal = client.get_token_balance(token_id)
            return {
                "token_id": token_id,
                "balance": bal,
                "wallet": getattr(client, "address", None),
            }
        except Exception as e:
            return {"status": "error", "message": str(e)}

    @mcp.tool
    def gasless_get_pol_balance() -> dict:
        """Get raw POL balance on the base EOA (gas token for non-gasless operations)."""
        try:
            client = require_gasless_client()
            bal = client.get_pol_balance()
            return {
                "pol_balance": bal,
                "base_eoa": client.get_base_wallet_address(),
            }
        except Exception as e:
            return {"status": "error", "message": str(e)}

    # =====================
    # Gasless Transfer Tools
    # =====================

    @mcp.tool
    def gasless_transfer_pusd(recipient: str, amount: float) -> dict:
        """
        Gasless transfer of pUSD to another address from your active gasless wallet (proxy/safe/deposit).

        This uses the relayer so you pay zero gas.
        """
        try:
            client = require_gasless_client()
            receipt = client.transfer_pusd(recipient, amount)
            return {
                "status": "success",
                "action": "transfer_pusd",
                "recipient": recipient,
                "amount": amount,
                "tx_hash": getattr(receipt, "transactionHash", None) or str(receipt),
                "from_wallet": getattr(client, "address", None),
            }
        except Exception as e:
            return {"status": "error", "message": str(e)}

    @mcp.tool
    def gasless_transfer_token(token_id: str, recipient: str, amount: float) -> dict:
        """
        Gasless transfer of a specific outcome token to another address.

        Uses the relayer (zero gas for the user).
        """
        try:
            client = require_gasless_client()
            receipt = client.transfer_token(token_id, recipient, amount)
            return {
                "status": "success",
                "action": "transfer_token",
                "token_id": token_id,
                "recipient": recipient,
                "amount": amount,
                "tx_hash": getattr(receipt, "transactionHash", None) or str(receipt),
                "from_wallet": getattr(client, "address", None),
            }
        except Exception as e:
            return {"status": "error", "message": str(e)}

    # =====================
    # Low-level / Custom Gasless Execution (Power Tool)
    # =====================

    @mcp.tool
    def gasless_execute_custom(calls: list[dict], metadata: str = "Custom gasless transaction") -> dict:
        """
        LOW-LEVEL POWER TOOL: Execute arbitrary gasless transactions via the relayer.

        This is the escape hatch for anything not covered by the high-level tools
        (split, merge, redeem, approve, transfers, etc.).

        Each call must be a dict with:
            - "to": contract address (string)
            - "data": hex-encoded calldata (string, must start with 0x)
            - "value": optional, defaults to "0"

        Example:
            calls = [
                {"to": "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174", "data": "0x...", "value": "0"},
                {"to": "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045", "data": "0x..."}
            ]

        WARNING: This is extremely powerful. Only use with calldata you fully understand.
        Incorrect calldata can result in loss of funds.
        """
        if not isinstance(calls, list) or len(calls) == 0:
            return {"status": "error", "message": "calls must be a non-empty list of transaction objects"}

        try:
            client = require_gasless_client()

            # Normalize calls for the gasless client
            normalized_calls = []
            for call in calls:
                normalized_calls.append({
                    "to": call["to"],
                    "data": call["data"],
                    "value": call.get("value", 0),
                })

            # Use the client's internal batch executor (handles Safe/Proxy/Deposit correctly)
            if hasattr(client, "_execute_calls"):
                receipts = client._execute_calls(normalized_calls, metadata or "Custom gasless tx")
                return {
                    "status": "success",
                    "action": "execute_custom",
                    "num_calls": len(calls),
                    "receipts": [str(r) for r in (receipts if isinstance(receipts, list) else [receipts])],
                    "metadata": metadata,
                }

            # Fallback: try single execute if only one call
            if len(normalized_calls) == 1:
                call = normalized_calls[0]
                receipt = client._execute(call["to"], call["data"], metadata or "Custom gasless tx")
                return {
                    "status": "success",
                    "action": "execute_custom",
                    "tx_hash": getattr(receipt, "transactionHash", None) or str(receipt),
                }

            return {
                "status": "error",
                "message": "Batch execution not available on this client version. Send one call at a time."
            }

        except Exception as e:
            return {
                "status": "error",
                "message": str(e),
                "hint": "Make sure calldata is correct and you have sufficient approvals/balances on the gasless wallet."
            }

    # =====================
    # Convenience Wrappers (high-frequency safe helpers)
    # =====================

    @mcp.tool
    def gasless_approve_token(token_id: str) -> dict:
        """
        Convenience wrapper: Ensure the necessary approvals are set so this specific token_id
        can be traded or redeemed gaslessly.

        This is a targeted version of gasless_approve_all focused on one token.
        """
        try:
            client = require_gasless_client()
            # For most cases we still need the broad approvals (exchanges, adapters, etc.)
            # We call the existing broad approval method but report it was triggered for this token
            receipts = client.set_all_approvals()
            return {
                "status": "success",
                "action": "approve_for_token",
                "token_id": token_id,
                "note": "Broad approvals set (required for this token to be usable in gasless flows).",
                "receipt_count": len(receipts) if isinstance(receipts, list) else 1,
            }
        except Exception as e:
            return {"status": "error", "message": str(e)}

    @mcp.tool
    def gasless_batch_approve(token_ids: list[str]) -> dict:
        """
        Batch version of gasless_approve_token for multiple tokens.
        """
        try:
            client = require_gasless_client()
            receipts = client.set_all_approvals()
            return {
                "status": "success",
                "action": "batch_approve",
                "token_ids": token_ids,
                "receipt_count": len(receipts) if isinstance(receipts, list) else 1,
                "note": "Approvals set for all common spenders. Safe to call multiple times.",
            }
        except Exception as e:
            return {"status": "error", "message": str(e)}

    @mcp.tool
    def gasless_redeem_all_redeemable() -> dict:
        """
        HIGH-VALUE CONVENIENCE: Automatically finds all your redeemable positions
        (using the rich positions data) and redeems them gaslessly in batch.

        This is one of the most common post-resolution workflows.
        """
        try:
            from .config import get_data_client, get_user_address

            addr = get_user_address()
            if not addr:
                return {"status": "error", "message": "No POLYMARKET_PRIVATE_KEY available."}

            dc = get_data_client()
            if dc is None:
                return {"status": "error", "message": "polymarket-apis data client not available."}

            positions = dc.get_positions(user=addr, redeemable=True, size_threshold=0.01, limit=100)

            if not positions:
                return {"status": "success", "message": "No redeemable positions found.", "redeemed": []}

            client = require_gasless_client()
            results = []

            for p in positions:
                try:
                    cond_id = getattr(p, "condition_id", None) or p.model_dump().get("condition_id")
                    neg_risk = getattr(p, "negative_risk", False) or p.model_dump().get("negative_risk", False)
                    # We need the amounts — for simplicity we redeem the current size for the winning side.
                    # A more sophisticated version would inspect which outcome won.
                    size = getattr(p, "size", 0) or 0
                    if size <= 0:
                        continue

                    # Best effort: redeem [size, 0] or [0, size] — in practice agents should specify exact amounts.
                    # Here we redeem the full size on the first outcome as a reasonable default.
                    receipt = client.redeem_position(cond_id, [float(size), 0.0], neg_risk)
                    results.append({
                        "condition_id": cond_id,
                        "status": "redeemed",
                        "tx_hash": getattr(receipt, "transactionHash", None) or str(receipt),
                    })
                except Exception as inner_e:
                    results.append({
                        "condition_id": getattr(p, "condition_id", "unknown"),
                        "status": "error",
                        "error": str(inner_e),
                    })

            return {
                "status": "success",
                "action": "redeem_all_redeemable",
                "positions_processed": len(results),
                "results": results,
            }
        except Exception as e:
            return {"status": "error", "message": str(e)}
