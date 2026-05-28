"""
Robust configuration for polymarket-alpha (current production CLOB + Gamma stack).

Supports:
- Simple mode: POLYMARKET_PRIVATE_KEY only (CLOB trading via py-clob-client-v2)
- Full gasless mode: Private key + Relayer/Builder credentials (defaults to signature_type=3)

Uses current production endpoints:
- CLOB: https://clob.polymarket.com (py-clob-client-v2)
- Gamma: https://gamma-api.polymarket.com

Polymarket's recommended future direction is the unified `polymarket-client` SDK.
See get_unified_sdk_guidance() for details and migration notes.
The MCP high-level tools abstract the underlying client — preferred for agents.

The MCP is the single source of truth. Agents should call get_capabilities()
or polymarket_alpha_setup_guide() when confused.

SECURITY
-------
This file ONLY READS credentials from the process environment (os.environ).
It never writes secrets to disk, never logs them, and never embeds example values.

When modifying this file or related docs:
- Never introduce real PK / CLOB_* values.
- All example text must use placeholders only (${PK}, 0xYour..., etc.).
- Real credential files (any file containing live CLOB_API_KEY, PK, etc.)
  must remain gitignored (see root .gitignore).
"""

import os
from dataclasses import dataclass
from typing import Optional

from eth_account import Account
from py_clob_client_v2 import ClobClient, ApiCreds as ClobApiCreds
from py_clob_client_v2.clob_types import BalanceAllowanceParams, AssetType, OpenOrderParams, TradeParams

# Polymarket unified APIs (for rich positions + gasless on-chain)
try:
    from polymarket_apis import PolymarketDataClient, PolymarketGaslessWeb3Client
    from polymarket_apis.types.clob_types import ApiCreds as PolymarketApiCreds
    _HAS_POLYMARKET_APIS = True
except Exception:
    PolymarketDataClient = None  # type: ignore
    PolymarketGaslessWeb3Client = None  # type: ignore
    PolymarketApiCreds = None  # type: ignore
    _HAS_POLYMARKET_APIS = False

# Low-level relayer client (used for advanced gasless operations like Safe deployment)
try:
    from py_builder_relayer_client.client import RelayClient
    from py_builder_relayer_client.models import RelayerTxType
    from py_builder_signing_sdk.config import BuilderConfig, BuilderApiKeyCreds, RemoteBuilderConfig
    _HAS_BUILDER_RELAYER = True
except Exception:
    RelayClient = None  # type: ignore
    RelayerTxType = None  # type: ignore
    BuilderConfig = None  # type: ignore
    BuilderApiKeyCreds = None  # type: ignore
    RemoteBuilderConfig = None  # type: ignore
    _HAS_BUILDER_RELAYER = False


@dataclass
class AuthStatus:
    has_private_key: bool
    has_relayer_creds: bool
    gasless_ready: bool
    signature_type: Optional[int]
    mode: str  # "read-only", "clob-trading", "gasless-enabled"
    description: str


def _get_env(name: str) -> Optional[str]:
    val = os.environ.get(name)
    return val.strip() if val else None


# =============================================================================
# Official credential names for the new simpler native Hermes / agent-harness flow
# (preferred over legacy POLYMARKET_* names)
# =============================================================================
OFFICIAL_PK_VARS = ("PK", "POLYMARKET_PRIVATE_KEY")
OFFICIAL_CLOB_API_KEY_VARS = ("CLOB_API_KEY", "POLYMARKET_CLOB_API_KEY")
OFFICIAL_CLOB_SECRET_VARS = ("CLOB_SECRET", "POLYMARKET_CLOB_SECRET")
OFFICIAL_CLOB_PASSPHRASE_VARS = ("CLOB_PASS_PHRASE", "POLYMARKET_CLOB_PASS_PHRASE")
OFFICIAL_CLOB_URL_VARS = ("CLOB_API_URL", "POLYMARKET_CLOB_HOST")
OFFICIAL_FUNDER_VARS = ("FUNDER", "POLY_FUNDER", "CLOB_FUNDER", "POLYMARKET_FUNDER")
OFFICIAL_SIG_TYPE_VARS = ("POLY_SIGNATURE_TYPE", "SIGNATURE_TYPE")


def _get_first_env(vars_tuple: tuple[str, ...]) -> Optional[str]:
    for name in vars_tuple:
        val = _get_env(name)
        if val:
            return val
    return None


def get_official_credentials() -> dict:
    """
    Returns the preferred native credential set.
    Primary names: PK + CLOB_API_KEY / CLOB_SECRET / CLOB_PASS_PHRASE
    Falls back to legacy names for compatibility (with future deprecation).
    """
    pk = _get_first_env(OFFICIAL_PK_VARS)
    clob_api_key = _get_first_env(OFFICIAL_CLOB_API_KEY_VARS)
    clob_secret = _get_first_env(OFFICIAL_CLOB_SECRET_VARS)
    clob_passphrase = _get_first_env(OFFICIAL_CLOB_PASSPHRASE_VARS)
    clob_url = _get_first_env(OFFICIAL_CLOB_URL_VARS)
    funder = _get_first_env(OFFICIAL_FUNDER_VARS)
    sig_type_str = _get_first_env(OFFICIAL_SIG_TYPE_VARS)

    has_direct_clob_creds = bool(clob_api_key and clob_secret and clob_passphrase)

    # Detect legacy usage
    using_legacy = False
    if not pk and _get_env("POLYMARKET_PRIVATE_KEY"):
        pk = _get_env("POLYMARKET_PRIVATE_KEY")
        using_legacy = True
    if not has_direct_clob_creds and _get_env("POLYMARKET_PRIVATE_KEY"):
        # We still support derivation from legacy PK only
        pass

    return {
        "pk": pk,
        "clob_api_key": clob_api_key,
        "clob_secret": clob_secret,
        "clob_passphrase": clob_passphrase,
        "clob_url": clob_url,
        "funder": funder,
        "signature_type": sig_type_str,
        "has_direct_clob_creds": has_direct_clob_creds,
        "using_legacy_names": using_legacy or bool(_get_env("RELAYER_API_KEY") or _get_env("BUILDER_API_KEY")),
    }


def get_auth_status() -> AuthStatus:
    creds = get_official_credentials()
    pk = creds["pk"]
    relayer_key = _get_env("RELAYER_API_KEY") or _get_env("BUILDER_API_KEY")
    relayer_address = _get_env("RELAYER_API_KEY_ADDRESS")
    sig_type_str = creds["signature_type"] or _get_env("POLY_SIGNATURE_TYPE")

    has_pk = bool(pk and len(pk) > 10)
    has_relayer = bool(relayer_key and relayer_address)

    # === Gasless "Always On By Default" logic ===
    # If user has PK + Relayer creds, we prefer gasless mode.
    # POLY_SIGNATURE_TYPE is now optional (we auto-detect or default intelligently).
    if has_pk and has_relayer:
        if sig_type_str:
            try:
                signature_type = int(sig_type_str)
            except Exception:
                signature_type = 3  # Deposit wallets (most common for advanced gasless / builder flows)
        else:
            # No explicit type set → auto-detection will run in get_gasless_client
            # Default to 3 (Deposit) per current requirements
            signature_type = 3

        if signature_type in (1, 2, 3):
            gasless_ready = True
            mode = "gasless-enabled"
            if sig_type_str:
                desc = f"Gasless + CLOB enabled (Relayer + PK, sig_type={signature_type})"
            else:
                desc = "Gasless + CLOB enabled (Relayer + PK) — signature type auto-detected or defaulted to 3 (Deposit)"
        else:
            gasless_ready = False
            mode = "clob-trading"
            desc = "CLOB trading enabled. Relayer creds present but invalid POLY_SIGNATURE_TYPE."
    elif has_pk:
        gasless_ready = False
        signature_type = None
        mode = "clob-trading"
        desc = "CLOB trading enabled (Private Key only). Gasless features unavailable (no RELAYER_API_KEY)."
    else:
        gasless_ready = False
        signature_type = None
        mode = "read-only"
        desc = "Read-only mode. All discovery and analysis tools available. Trading requires credentials."

    return AuthStatus(
        has_private_key=has_pk,
        has_relayer_creds=has_relayer,
        gasless_ready=gasless_ready,
        signature_type=signature_type,
        mode=mode,
        description=desc,
    )


def require_private_key() -> str:
    pk = _get_first_env(OFFICIAL_PK_VARS)
    if not pk:
        raise PermissionError(
            "This action requires PK (or legacy POLYMARKET_PRIVATE_KEY).\n"
            "Call polymarket_alpha_setup_guide(platform='hermes') for exact copy-paste instructions.\n"
            "Preferred: put PK + CLOB_* credentials directly in your agent's mcp_servers env block."
        )
    return pk


def get_clob_host() -> str:
    # Using py-clob-client-v2 against the current production CLOB endpoint.
    # (Unified `polymarket-client` is the official recommended future path — see get_unified_sdk_guidance().)
    creds = get_official_credentials()
    return creds["clob_url"] or _get_env("POLYMARKET_CLOB_HOST") or "https://clob.polymarket.com"


def get_chain_id() -> int:
    return int(_get_env("POLYMARKET_CHAIN_ID") or "137")


# Official pUSD (Polymarket USD) collateral token on Polygon
# This is the ERC-20 token used for on-chain gasless actions (split/merge/redeem, transfers).
# It is 1:1 backed by USDC. Use this address for on-chain balance checks in gasless flows.
PUSD_ADDRESS = "0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB"  # Proxy (user-facing) address on Polygon (chain 137)


def get_gamma_url() -> str:
    # Current Gamma API (V2-compatible market/event discovery)
    return _get_env("POLYMARKET_GAMMA_API") or "https://gamma-api.polymarket.com"


def get_relayer_url() -> str:
    return _get_env("RELAYER_URL") or "https://relayer-v2.polymarket.com"


# =============================================================================
# Authenticated CLOB Client (for portfolio + trading)
# =============================================================================

_authenticated_clob_client: ClobClient | None = None


def get_authenticated_clob_client() -> ClobClient:
    """
    Returns a fully authenticated ClobClient.

    Native flow (preferred):
    - Provide PK + CLOB_API_KEY / CLOB_SECRET / CLOB_PASS_PHRASE directly (no derivation).
    - signature_type defaults to 3 (Deposit wallets). FUNDER can be supplied via FUNDER / POLY_FUNDER env.
    - If only PK present: derives L2 creds (legacy/simple EOA path) but still applies signature_type=3 + funder when available.

    This is the "much simpler native use" path for Hermes and agent harnesses.
    """
    global _authenticated_clob_client
    if _authenticated_clob_client is not None:
        return _authenticated_clob_client

    private_key = require_private_key()
    creds_info = get_official_credentials()

    host = creds_info["clob_url"] or get_clob_host()
    chain_id = get_chain_id()

    # Determine signature_type (internal default 3, no mandatory env var)
    sig_type = 3
    if creds_info["signature_type"]:
        try:
            sig_type = int(creds_info["signature_type"])
        except Exception:
            sig_type = 3

    funder = creds_info["funder"]

    if creds_info["has_direct_clob_creds"]:
        # === SIMPLE NATIVE PATH: pre-provided L2 creds (recommended for harnesses) ===
        creds = ClobApiCreds(
            api_key=creds_info["clob_api_key"],
            api_secret=creds_info["clob_secret"],
            api_passphrase=creds_info["clob_passphrase"],
        )

        client_kwargs = {
            "host": host,
            "chain_id": chain_id,
            "key": private_key,
            "creds": creds,
        }
        if sig_type in (1, 2, 3):
            client_kwargs["signature_type"] = sig_type
        if funder:
            client_kwargs["funder"] = funder

        client = ClobClient(**client_kwargs)
        _authenticated_clob_client = client
        return client

    # === FALLBACK / LEGACY PATH: derive L2 from PK (still useful for pure EOA) ===
    temp_client = ClobClient(host=host, chain_id=chain_id, key=private_key)
    try:
        creds_dict = temp_client.create_or_derive_api_key()
    except Exception as e:
        # Surface a very clear error for harness users
        raise PermissionError(
            f"Failed to derive CLOB API credentials from PK: {e}\n"
            "For simpler native use, generate L2 creds once (apiKey/secret/passphrase) and provide them as CLOB_API_KEY / CLOB_SECRET / CLOB_PASS_PHRASE + PK.\n"
            "See polymarket_alpha_setup_guide(platform='hermes') for the exact config block."
        ) from e

    creds = ClobApiCreds(
        api_key=creds_dict["apiKey"],
        api_secret=creds_dict["secret"],
        api_passphrase=creds_dict["passphrase"],
    )

    client_kwargs = {
        "host": host,
        "chain_id": chain_id,
        "key": private_key,
        "creds": creds,
    }
    if sig_type in (1, 2, 3):
        client_kwargs["signature_type"] = sig_type
    if funder:
        client_kwargs["funder"] = funder

    client = ClobClient(**client_kwargs)
    _authenticated_clob_client = client
    return client


# =============================================================================
# Startup logging (used by server and __main__)
# =============================================================================

def log_startup_status() -> None:
    """Print friendly startup banner with current auth mode."""
    status = get_auth_status()
    print("\n" + "="*60)
    print("=== polymarket-alpha MCP (Polymarket V2 + Gasless) ===")
    print("="*60)
    print(f"Auth Mode: {status.mode}")
    print(f"Status: {status.description}\n")

    if status.gasless_ready:
        print("✓ Gasless Mode: ENABLED BY DEFAULT")
        print("  (PK + RELAYER credentials detected)")
        print("  Signature type defaults to 3 (Deposit wallets).\n")
    else:
        print("⚠️  Trading / Gasless features are LIMITED or DISABLED.")
        print("   The agent should call: polymarket_alpha_setup_guide(platform=\"hermes\")\n")

    print("For Hermes / agent harness users (recommended native flow):")
    print("  Put credentials INSIDE the mcp_servers entry in config.yaml (see setup guide).")
    print("  Primary vars: PK, CLOB_API_KEY, CLOB_SECRET, CLOB_PASS_PHRASE (CLOB_API_URL optional).")
    print("  First actions after launch (ALWAYS): get_mcp_health_report() then get_capabilities(); for trading also check_clob_auth(include_raw=True)")
    print("  Real-time data (major strength): start_full_market_monitor / watch_* / start_realtime_market_watcher (Gamma+WS) + listen_for_ws_events / get_latest_ws_messages (consume) + get_realtime_trading_guide() + get_realtime_helper_patterns()")
    print("\nCall polymarket_alpha_setup_guide(platform=\"hermes\") for the EXACT copy-paste block.\n")
    print("="*60 + "\n")


# =============================================================================
# Address derivation (used for positions + gasless)
# =============================================================================

def get_user_address() -> Optional[str]:
    """Derive checksum address from PK (preferred) or legacy POLYMARKET_PRIVATE_KEY."""
    pk = _get_first_env(OFFICIAL_PK_VARS)
    if not pk:
        return None
    try:
        acct = Account.from_key(pk)
        return acct.address
    except Exception:
        return None


# =============================================================================
# Data client (rich portfolio / positions from Polymarket data API)
# =============================================================================

_data_client: "PolymarketDataClient | None" = None


def get_data_client() -> Optional["PolymarketDataClient"]:
    """Return (cached) unauthenticated PolymarketDataClient for rich positions/PnL queries."""
    global _data_client
    if not _HAS_POLYMARKET_APIS or PolymarketDataClient is None:
        return None
    if _data_client is None:
        _data_client = PolymarketDataClient()
    return _data_client


# =============================================================================
# Gasless Relayer Client (full implementation of Builder/Relayer integration)
# =============================================================================

_gasless_client: "PolymarketGaslessWeb3Client | None" = None


def get_gasless_client() -> Optional["PolymarketGaslessWeb3Client"]:
    """
    Initialize and return a cached PolymarketGaslessWeb3Client using relayer or builder creds.

    Requires:
      - POLYMARKET_PRIVATE_KEY
      - RELAYER_API_KEY (or BUILDER_API_KEY)
      - POLY_SIGNATURE_TYPE (optional — defaults to 3 / Deposit wallets; 1=proxy, 2=Safe, 3=Deposit)
      - Optionally RELAYER_API_SECRET + RELAYER_API_PASSPHRASE for builder level-2 headers

    Returns None (with clear error guidance) if prerequisites missing.
    This completes the previously "credential-ready only" gasless support.
    """
    global _gasless_client
    if _gasless_client is not None:
        return _gasless_client

    if not _HAS_POLYMARKET_APIS or PolymarketGaslessWeb3Client is None:
        return None

    pk = _get_first_env(OFFICIAL_PK_VARS)
    relayer_key = _get_env("RELAYER_API_KEY") or _get_env("BUILDER_API_KEY")
    secret = _get_env("RELAYER_API_SECRET") or _get_env("BUILDER_SECRET")
    passphrase = _get_env("RELAYER_API_PASSPHRASE") or _get_env("BUILDER_PASSPHRASE")
    sig_type_str = _get_env("POLY_SIGNATURE_TYPE")

    if not pk or not relayer_key:
        return None

    # === Auto-detection logic (Gasless Always On By Default) ===
    # Default changed to 3 (Deposit wallets) per current requirements
    if sig_type_str:
        try:
            sig_type = int(sig_type_str)
        except Exception:
            sig_type = 3
    else:
        sig_type = None  # Will attempt auto-detection below

    if sig_type not in (1, 2, 3):
        sig_type = None

    try:
        temp_client_kwargs = {}
        if secret and passphrase:
            builder_creds = PolymarketApiCreds(
                api_key=relayer_key,
                api_secret=secret,
                api_passphrase=passphrase,
            )
            temp_client_kwargs["builder_creds"] = builder_creds
        else:
            temp_client_kwargs["relayer_api_key"] = relayer_key

        # Start with 3 (Deposit) as the temporary default during detection
        client = PolymarketGaslessWeb3Client(
            private_key=pk,
            signature_type=3,  # temporary default (Deposit)
            **temp_client_kwargs
        )

        # Auto-detect if user did not explicitly set POLY_SIGNATURE_TYPE
        if sig_type is None:
            try:
                base_addr = client.get_base_wallet_address()
                detected = client.detect_wallet_signature_type(base_addr)
                if detected in (1, 2, 3):
                    sig_type = detected
                    print(f"[gasless] Auto-detected signature_type={sig_type} for {base_addr}")
                else:
                    sig_type = 3  # Deposit wallets (current default)
                    print("[gasless] Could not auto-detect signature type. Defaulting to 3 (Deposit)")
            except Exception:
                sig_type = 3
                print("[gasless] Auto-detection failed. Defaulting to signature_type=3 (Deposit)")

        # Recreate with the final (detected or defaulted) type if different from 3
        if sig_type != 3:
            client = PolymarketGaslessWeb3Client(
                private_key=pk,
                signature_type=sig_type,  # type: ignore[arg-type]
                **temp_client_kwargs
            )

        _gasless_client = client
        return client

    except Exception as e:
        print(f"[gasless] Failed to init gasless client: {e}")
        return None


def require_gasless_client() -> "PolymarketGaslessWeb3Client":
    """Raise clear actionable error if gasless client cannot be created."""
    client = get_gasless_client()
    if client is None:
        status = get_auth_status()
        raise PermissionError(
            "Gasless operations require PK (or POLYMARKET_PRIVATE_KEY) + RELAYER_API_KEY (or BUILDER_API_KEY).\n"
            "POLY_SIGNATURE_TYPE is optional — defaults to 3 (Deposit wallets).\n"
            f"Current auth: {status.mode}. Call polymarket_alpha_setup_guide(platform='hermes') for exact instructions."
        )
    return client


def get_raw_relay_client() -> Optional["RelayClient"]:
    """
    Returns a low-level py-builder-relayer-client RelayClient.
    Useful for advanced operations (Safe deployment, custom batches, etc.)
    that are not covered by the higher-level PolymarketGaslessWeb3Client.

    Credential requirements (Builder vs remote signer patterns):
    - PK + RELAYER_API_KEY (or BUILDER_API_KEY) are always required.
    - For the supported local Builder pattern (preferred): also provide
      RELAYER_API_SECRET + RELAYER_API_PASSPHRASE. This populates
      local_builder_creds=BuilderApiKeyCreds(...) in BuilderConfig.
    - The legacy remote-builder fallback (RemoteBuilderConfig) previously
      used a non-functional hardcoded placeholder URL and has been cleaned up.
    - Remote signer pattern requires an explicit, valid production signer URL
      configured by the user (no placeholder or example.com allowed).

    Returns None if core prerequisites are absent (graceful, consistent with
    other get_* helpers). Raises a clear actionable error (caught + logged)
    for misconfigured remote fallback attempts.

    Most callers should use get_gasless_client() / require_gasless_client()
    unless low-level RelayClient access is explicitly needed for custom flows.
    For setup details call polymarket_alpha_setup_guide(platform='hermes').
    """
    if not _HAS_BUILDER_RELAYER or RelayClient is None:
        return None

    pk = _get_first_env(OFFICIAL_PK_VARS)
    relayer_key = _get_env("RELAYER_API_KEY") or _get_env("BUILDER_API_KEY")
    secret = _get_env("RELAYER_API_SECRET") or _get_env("BUILDER_SECRET")
    passphrase = _get_env("RELAYER_API_PASSPHRASE") or _get_env("BUILDER_PASSPHRASE")
    relayer_url = get_relayer_url()
    chain_id = get_chain_id()

    if not pk or not relayer_key:
        return None

    try:
        if secret and passphrase:
            builder_config = BuilderConfig(
                local_builder_creds=BuilderApiKeyCreds(
                    key=relayer_key,
                    secret=secret,
                    passphrase=passphrase,
                )
            )
        else:
            # Remote builder / remote signer fallback deliberately removed.
            # No more dummy URL. Raise clear guidance pointing to correct patterns.
            raise RuntimeError(
                "get_raw_relay_client remote-builder fallback is unavailable (placeholder URL removed).\n\n"
                "Correct setup (Builder vs remote signer patterns):\n"
                "  1. Builder pattern (recommended): Provide RELAYER_API_SECRET + RELAYER_API_PASSPHRASE\n"
                "     together with RELAYER_API_KEY (or BUILDER_API_KEY) + PK. This enables the\n"
                "     local_builder_creds path used by both gasless client and raw relay client.\n"
                "  2. Remote signer pattern: Explicitly supply a real remote signer service URL\n"
                "     (your production signing endpoint). Never use example/placeholder domains.\n\n"
                "Call polymarket_alpha_setup_guide(platform='hermes') for the exact env vars and config.\n"
                "Prefer higher-level require_gasless_client() for standard advanced gasless operations."
            )

        client = RelayClient(
            relayer_url=relayer_url,
            chain_id=chain_id,
            private_key=pk,
            builder_config=builder_config,
            relay_tx_type=RelayerTxType.SAFE,
        )
        return client
    except Exception as e:
        print(f"[relayer] Failed to create raw RelayClient: {e}")
        return None


def get_relayer_creds() -> Optional[dict]:
    """
    Return current relayer/builder credentials (if present).
    SECURITY: This is INTERNAL only. Callers (health, gasless_status, wallet_info) MUST
    only expose booleans/presence flags — never the raw key/secret/passphrase values.
    Never log or return real secrets from any public tool path.
    """
    key = _get_env("RELAYER_API_KEY") or _get_env("BUILDER_API_KEY")
    secret = _get_env("RELAYER_API_SECRET") or _get_env("BUILDER_SECRET")
    passphrase = _get_env("RELAYER_API_PASSPHRASE") or _get_env("BUILDER_PASSPHRASE")
    address = _get_env("RELAYER_API_KEY_ADDRESS")

    if key and address:
        return {
            "key": key,
            "secret": secret,
            "passphrase": passphrase,
            "address": address,
            "builder_code": _get_env("BUILDER_CODE"),
            "signature_type": _get_env("POLY_SIGNATURE_TYPE") or "3",
        }
    return None

