"""
Robust configuration for polymarket-alpha (V2 CLOB + Gamma stack).

Supports:
- Simple mode: POLYMARKET_PRIVATE_KEY only (CLOB trading via py-clob-client-v2)
- Full gasless mode: Private key + Relayer/Builder credentials (defaults to signature_type=3)

Uses current production endpoints:
- CLOB: https://clob.polymarket.com (py-clob-client-v2)
- Gamma: https://gamma-api.polymarket.com

The MCP is the single source of truth. Agents should call get_capabilities()
or polymarket_alpha_setup_guide() when confused.
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


def get_auth_status() -> AuthStatus:
    pk = _get_env("POLYMARKET_PRIVATE_KEY")
    relayer_key = _get_env("RELAYER_API_KEY") or _get_env("BUILDER_API_KEY")
    relayer_address = _get_env("RELAYER_API_KEY_ADDRESS")
    sig_type_str = _get_env("POLY_SIGNATURE_TYPE")

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
    pk = _get_env("POLYMARKET_PRIVATE_KEY")
    if not pk:
        raise PermissionError(
            "This action requires POLYMARKET_PRIVATE_KEY.\n"
            "Call polymarket_alpha_setup_guide(platform='hermes') or 'openclaw' for exact instructions."
        )
    return pk


def get_clob_host() -> str:
    # Using py-clob-client-v2 against the current production CLOB endpoint (V2 stack)
    return _get_env("POLYMARKET_CLOB_HOST") or "https://clob.polymarket.com"


def get_chain_id() -> int:
    return int(_get_env("POLYMARKET_CHAIN_ID") or "137")


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
    Returns a fully authenticated ClobClient using L1 private key + derived L2 creds.

    This follows the official recommended pattern from py-clob-client-v2 examples.
    Raises clear PermissionError if POLYMARKET_PRIVATE_KEY is missing.
    """
    global _authenticated_clob_client
    if _authenticated_clob_client is not None:
        return _authenticated_clob_client

    private_key = require_private_key()

    host = get_clob_host()
    chain_id = get_chain_id()

    # Step 1: Create temp client to derive L2 API credentials from L1
    temp_client = ClobClient(host=host, chain_id=chain_id, key=private_key)
    creds_dict = temp_client.create_or_derive_api_key()

    creds = ClobApiCreds(
        api_key=creds_dict["apiKey"],
        api_secret=creds_dict["secret"],
        api_passphrase=creds_dict["passphrase"],
    )

    # Step 2: Full client (modern users usually use signature_type=3 + funder for deposit wallets)
    # This is the V2 CLOB authenticated client path.
    # For simplicity and broad compatibility, we start with basic EOA flow.
    # Advanced users can extend this later.
    client = ClobClient(
        host=host,
        chain_id=chain_id,
        key=private_key,
        creds=creds,
    )

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
        print("  (POLYMARKET_PRIVATE_KEY + RELAYER credentials detected)")
        print("  Signature type defaults to 3 (Deposit wallets).\n")
    else:
        print("⚠️  Trading / Gasless features are LIMITED or DISABLED.")
        print("   The agent should call: polymarket_alpha_setup_guide(platform=\"hermes\")\n")

    print("For Hermes users: You must correctly configure this MCP in:")
    print("  1. ~/.hermes/.env          ← store the actual secret values")
    print("  2. ~/.hermes/config.yaml   ← tell Hermes how to launch this MCP")
    print("\nCall polymarket_alpha_setup_guide(platform=\"hermes\") for the EXACT copy-paste configuration.\n")
    print("="*60 + "\n")


# =============================================================================
# Address derivation (used for positions + gasless)
# =============================================================================

def get_user_address() -> Optional[str]:
    """Derive checksum address from POLYMARKET_PRIVATE_KEY if present."""
    pk = _get_env("POLYMARKET_PRIVATE_KEY")
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

    pk = _get_env("POLYMARKET_PRIVATE_KEY")
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
            "Gasless operations require POLYMARKET_PRIVATE_KEY + RELAYER_API_KEY (or BUILDER_API_KEY).\n"
            "POLY_SIGNATURE_TYPE is now optional — the system will auto-detect or default to 3 (Deposit wallets).\n"
            f"Current auth: {status.mode}. Call polymarket_alpha_setup_guide() for Builder Program instructions."
        )
    return client


def get_raw_relay_client() -> Optional["RelayClient"]:
    """
    Returns a low-level py-builder-relayer-client RelayClient.
    Useful for advanced operations (Safe deployment, custom batches, etc.)
    that are not covered by the higher-level PolymarketGaslessWeb3Client.
    """
    if not _HAS_BUILDER_RELAYER or RelayClient is None:
        return None

    pk = _get_env("POLYMARKET_PRIVATE_KEY")
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
            # Fallback to remote-style if only key is present (less common)
            builder_config = BuilderConfig(
                remote_builder_config=RemoteBuilderConfig(url="https://your-signer.example.com")  # placeholder
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
    """Return current relayer/builder credentials (if present)."""
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

