"""
WebSocket support for Polymarket (Market + User + Sports channels).

This module provides managed WebSocket connections that can run in the background
inside the MCP process. Events are buffered and can be polled by agents.

Features (enhanced):
- Robust auto-reconnect with exponential backoff + jitter for all channels.
- Automatic re-subscription to previous assets/markets/leagues after reconnect.
- Improved message buffer: timestamps, content-based deduplication (short TTL),
  optional max-age pruning for memory safety.
- Connection health reporting (latency, last message, reconnect count, etc.).
- Enhanced listen tool supporting immediate return and wait-for-specific-event-type.
- Dynamic live subscription management: update_*_subscription( "subscribe"|"unsubscribe", ids )
- Pause/resume_websocket(channel): pause buffering while keeping connection alive (resource saver).
- Richer error surfacing: get_connection_health + get_websocket_status now include uptime,
  paused state, and a small recent_errors ring buffer (with ages).
- Batteries-included starter: start_realtime_market_watcher(...) returns ready-to-consume
  subscription handle + consumption recipes.

Market Channel: Public real-time orderbook, prices, trades.
User Channel: Authenticated real-time orders and trades for the connected API key.
Sports Channel: Public sports scores and updates.
"""

import asyncio
import hashlib
import json
import random
import time
from typing import Any, Optional
from collections import deque

import websockets
from websockets.exceptions import ConnectionClosed

from .config import get_official_credentials
from .gamma import _gamma_get  # for flexible resolution
from . import realtime_helpers  # high-level patterns + monitor helpers (patterns surfaced as tool)

MARKET_WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
USER_WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/user"
SPORTS_WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/sports"  # Based on docs pattern

def _resolve_token_ids(identifiers: list[str]) -> list[str]:
    """
    Flexible resolver: accepts slugs, condition_ids, or token_ids and returns clean list of clobTokenIds.
    Uses direct Gamma calls for speed inside the WS module.
    """
    token_ids = []
    for ident in identifiers:
        ident = ident.strip()
        if not ident:
            continue

        # If it looks like a token ID (long number), use as-is
        if ident.isdigit() or (ident.startswith("0x") and len(ident) > 60):
            token_ids.append(ident)
            continue

        # Try as slug or condition_id via Gamma
        try:
            if ident.startswith("0x"):  # condition_id
                data = _gamma_get("/markets", {"condition_id": ident})
            else:  # slug
                data = _gamma_get(f"/markets/slug/{ident}")

            if isinstance(data, list) and data:
                market = data[0]
                raw = market.get("clobTokenIds") or market.get("clob_token_ids")
                if isinstance(raw, str):
                    import json
                    parsed = json.loads(raw)
                    token_ids.extend(parsed)
                elif isinstance(raw, list):
                    token_ids.extend(raw)
            elif isinstance(data, dict) and "clobTokenIds" in data:
                raw = data["clobTokenIds"]
                if isinstance(raw, str):
                    import json
                    token_ids.extend(json.loads(raw))
                elif isinstance(raw, list):
                    token_ids.extend(raw)
        except Exception:
            # If resolution fails, keep the original (might be a raw token id)
            token_ids.append(ident)

    # Deduplicate while preserving order
    seen = set()
    return [t for t in token_ids if not (t in seen or seen.add(t))]


def _ws_error(msg: str, suggestion: Optional[str] = None, **extra: Any) -> dict:
    """
    Consistent error shape for all WebSocket tools.
    Always includes status + error; optionally a human-actionable 'suggestion'
    plus any extra context. This improves agent error recovery dramatically.
    """
    result: dict = {"status": "error", "error": msg}
    if suggestion:
        result["suggestion"] = suggestion
    if extra:
        result.update(extra)
    return result


# Global managers (simple singleton pattern for the MCP process)
_market_ws: Optional["ManagedMarketWebSocket"] = None
_user_ws: Optional["ManagedUserWebSocket"] = None
_sports_ws: Optional["ManagedSportsWebSocket"] = None


class ManagedMarketWebSocket:
    """
    Managed WebSocket for the public Market channel with robust auto-reconnect,
    enhanced buffering, and health reporting.
    """
    def __init__(self):
        self.ws: Optional[websockets.WebSocketClientProtocol] = None
        self.task: Optional[asyncio.Task] = None
        self.subscribed_assets: set[str] = set()
        self.messages: deque[dict] = deque(maxlen=500)  # Ring buffer
        self.running = False
        self.last_error: Optional[str] = None

        # === Enhanced state for reconnect, health, and buffering ===
        self.reconnect_count: int = 0
        self.last_message_time: float = 0.0
        self.latency_ms: Optional[float] = None
        self._reconnect_delay: float = 1.0
        self._max_reconnect_delay: float = 60.0
        self.max_buffer_age_seconds: float = 300.0  # 5 minutes default; prune old messages
        self._recent_hashes: dict[str, float] = {}   # fingerprint -> timestamp for dedup
        self._hash_ttl: float = 60.0                 # dedup window in seconds
        self._connect_params: dict[str, Any] = {}    # remembered args for clean reconnects

        # Pause/resume for low-resource mode (connection stays alive, but no buffering new messages)
        self.paused: bool = False
        # Connection start time for uptime calculation (set on successful connect/reconnect)
        self._connect_time: float = 0.0
        # Ring buffer of recent errors for improved diagnostics (timestamp + message)
        self._error_history: deque[dict] = deque(maxlen=8)

    async def connect(self, token_ids: list[str], initial_dump: bool = True, level: int = 2):
        """
        Connect (or update subscription on existing connection).
        Stores parameters for automatic re-subscription on reconnect.
        """
        if self.running:
            await self.update_subscription("subscribe", token_ids)
            return {"status": "already_connected", "updated_subscription": True}

        # Remember parameters for resilient reconnects
        self._connect_params = {
            "token_ids": list(token_ids),
            "initial_dump": initial_dump,
            "level": level,
        }

        try:
            self.ws = await websockets.connect(MARKET_WS_URL, ping_interval=10, ping_timeout=20)
            self.running = True
            self.paused = False
            self.reconnect_count = 0
            self._reconnect_delay = 1.0
            self.last_error = None
            self._connect_time = time.time()
            self._error_history.clear()

            # Initial subscribe
            sub_msg = {
                "assets_ids": token_ids,
                "type": "market",
                "initial_dump": initial_dump,
                "level": level,
                "custom_feature_enabled": True,  # Enable best_bid_ask etc.
            }
            await self.ws.send(json.dumps(sub_msg))
            self.subscribed_assets.update(token_ids)

            # Launch the resilient connection manager (handles auto-reconnect internally)
            self.task = asyncio.create_task(self._connection_loop())
            return {"status": "connected", "subscribed": token_ids}

        except Exception as e:
            self._record_error(str(e))
            return {"status": "error", "error": str(e)}

    async def update_subscription(self, operation: str, token_ids: list[str]):
        """
        Dynamic subscription management on an already-connected Market channel.

        operation: "subscribe" or "unsubscribe"
        token_ids: list of clobTokenIds (or will be treated as raw asset ids)

        This is the low-level method; prefer the MCP tool update_market_subscription()
        for agent use. Automatically updates the internal subscribed_assets set for
        correct re-subscription behavior after reconnects.
        """
        if not self.ws or not self.running:
            return _ws_error(
                "No active connection. Use connect_market_websocket first.",
                suggestion="Call connect_market_websocket(identifiers=[...]) or a high-level starter like start_full_market_monitor / watch_market_by_slug first."
            )
        if operation not in ("subscribe", "unsubscribe"):
            return _ws_error(
                "operation must be exactly 'subscribe' or 'unsubscribe'",
                suggestion="Use update_market_subscription / update_user_subscription / update_sports_subscription with valid operation."
            )

        msg = {
            "operation": operation,
            "assets_ids": token_ids,
            "level": 2,
            "custom_feature_enabled": True,
        }
        try:
            await self.ws.send(json.dumps(msg))
            if operation == "subscribe":
                self.subscribed_assets.update(token_ids)
            else:
                self.subscribed_assets.difference_update(token_ids)
            return {"status": "ok", "operation": operation, "updated": token_ids, "subscribed_count": len(self.subscribed_assets)}
        except Exception as e:
            self._record_error(f"update_subscription_failed: {e}")
            return {"status": "error", "error": str(e)}

    def _record_error(self, error: str):
        """Internal helper: set last_error and append to the small ring buffer for diagnostics."""
        self.last_error = error
        ts = time.time()
        self._error_history.append({
            "timestamp": ts,
            "error": error
        })

    # -------------------------------------------------------------------------
    # Robust Reconnect + Buffering (Market channel)
    # -------------------------------------------------------------------------

    async def _connection_loop(self):
        """
        Resilient main loop for the Market WS.
        - Establishes / re-establishes the WS connection.
        - On disconnect, performs exponential backoff + jitter.
        - Automatically re-subscribes using the subscribed_assets set.
        - Delegates actual message receiving to _recv_loop.
        """
        while self.running:
            try:
                if self.ws is None:
                    try:
                        self.ws = await websockets.connect(
                            MARKET_WS_URL, ping_interval=10, ping_timeout=20
                        )
                        # Re-subscribe to everything we were tracking before the drop
                        if self.subscribed_assets:
                            sub_msg = {
                                "assets_ids": list(self.subscribed_assets),
                                "type": "market",
                                "initial_dump": self._connect_params.get("initial_dump", True),
                                "level": self._connect_params.get("level", 2),
                                "custom_feature_enabled": True,
                            }
                            await self.ws.send(json.dumps(sub_msg))
                        self.reconnect_count += 1
                        self.last_error = None
                        self._connect_time = time.time()
                        self._reconnect_delay = 1.0  # reset backoff on success
                    except Exception as conn_e:
                        self._record_error(f"reconnect_failed: {conn_e}")
                        await self._backoff_sleep()
                        continue

                # Run inner receive loop (exits cleanly on disconnect)
                await self._recv_loop()

            except asyncio.CancelledError:
                raise
            except Exception as e:
                self._record_error(str(e))
            finally:
                # Tear down this socket instance; outer loop will decide whether to retry
                if self.ws:
                    try:
                        await self.ws.close()
                    except Exception:
                        pass
                    self.ws = None

                if self.running:
                    await self._backoff_sleep()

    async def _backoff_sleep(self):
        """Exponential backoff with jitter to avoid thundering herd."""
        delay = min(self._reconnect_delay, self._max_reconnect_delay)
        jittered = delay * (0.75 + 0.5 * random.random())  # +/- ~25% jitter
        await asyncio.sleep(jittered)
        # Increase for next attempt (capped)
        self._reconnect_delay = min(self._reconnect_delay * 2.0, self._max_reconnect_delay)

    async def _recv_loop(self):
        """
        Tight receive loop. Exits on any terminal condition so that
        _connection_loop can perform reconnect.
        """
        try:
            while self.running and self.ws:
                try:
                    raw = await asyncio.wait_for(self.ws.recv(), timeout=30)
                    msg = json.loads(raw)
                    if not self.paused:
                        self._buffer_message(msg)
                    # If paused, message is consumed (keeps WS healthy + pings working) but not buffered
                except asyncio.TimeoutError:
                    # Manual keep-alive ping
                    if self.ws:
                        try:
                            await self.ws.send("PING")
                        except Exception:
                            break
                except ConnectionClosed:
                    self._record_error("ConnectionClosed")
                    break
                except Exception as recv_e:
                    self._record_error(str(recv_e))
                    break
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self._record_error(str(e))

    def _buffer_message(self, msg: dict):
        """
        Improved buffering:
        - Always records high-resolution _received_at and _buffered_at timestamps.
        - Content-based deduplication using a short-lived fingerprint (event_type + key fields).
        - Optional automatic pruning of messages older than max_buffer_age_seconds.
        """
        if self.paused:
            return  # Respect pause flag even if called directly
        if not isinstance(msg, dict):
            msg = {"raw": str(msg)}

        ts = time.time()

        # Build a stable fingerprint from semantically meaningful fields
        try:
            core = {}
            for k in ("event_type", "type", "asset_id", "assetId", "market", "price", "size",
                      "lastTradePrice", "best_bid", "best_ask", "bid", "ask"):
                if k in msg:
                    core[k] = msg[k]
            fp_src = json.dumps(core, sort_keys=True, default=str)
            fp = hashlib.md5(fp_src.encode("utf-8")).hexdigest()
        except Exception:
            fp = hashlib.md5(str(msg)[:400].encode("utf-8", errors="ignore")).hexdigest()

        now = ts
        # Evict expired fingerprints
        self._recent_hashes = {h: t for h, t in self._recent_hashes.items() if now - t < self._hash_ttl}
        if fp in self._recent_hashes:
            return  # duplicate within the dedup window

        self._recent_hashes[fp] = now

        # Store a copy with timestamps
        msg = dict(msg)
        msg["_received_at"] = ts
        msg["_buffered_at"] = ts

        self.messages.append(msg)
        self.last_message_time = ts

        # Age-based buffer hygiene (keeps memory bounded even with long-lived connections)
        self._prune_old_messages()

    def _prune_old_messages(self):
        """Drop messages older than max_buffer_age_seconds from the left of the deque."""
        if self.max_buffer_age_seconds <= 0 or not self.messages:
            return
        cutoff = time.time() - self.max_buffer_age_seconds
        while self.messages and self.messages[0].get("_received_at", 0) < cutoff:
            self.messages.popleft()

    async def disconnect(self):
        """Explicit disconnect: stops reconnect attempts and clears all state."""
        self.running = False
        if self.task:
            self.task.cancel()
            try:
                await asyncio.wait_for(self.task, timeout=2.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass
            except Exception:
                pass
        if self.ws:
            try:
                await self.ws.close()
            except Exception:
                pass
        self.ws = None
        self.subscribed_assets.clear()
        self.messages.clear()
        self._recent_hashes.clear()
        self._connect_params.clear()
        self.reconnect_count = 0
        self.last_error = None
        self.last_message_time = 0.0
        self.paused = False
        self._connect_time = 0.0
        self._error_history.clear()

    def get_recent_messages(self, limit: int = 50) -> list[dict]:
        return list(self.messages)[-limit:]

    def get_connection_health(self) -> dict:
        """
        Returns rich health metrics for monitoring and diagnostics.
        Used by the get_connection_health MCP tool.

        Enhanced with:
        - paused: whether message processing is paused (connection still alive)
        - uptime_seconds: how long the current connection session has been up
        - recent_errors: last few errors (ring buffer) with relative ages for surfacing issues
        """
        now = time.time()
        last_age = (now - self.last_message_time) if self.last_message_time > 0 else None
        uptime = (now - self._connect_time) if self._connect_time > 0 else None

        # Build recent errors with computed ages (most recent first)
        recent_errors = []
        for entry in reversed(list(self._error_history)):
            age = round(now - entry.get("timestamp", now), 1)
            recent_errors.append({
                "error": entry.get("error"),
                "age_seconds": age
            })

        return {
            "connected": bool(self.running and self.ws is not None),
            "paused": self.paused,
            "reconnect_count": self.reconnect_count,
            "uptime_seconds": round(uptime, 1) if uptime is not None else None,
            "last_message_time": self.last_message_time or None,
            "last_message_age_seconds": round(last_age, 2) if last_age is not None else None,
            "buffered_messages": len(self.messages),
            "last_error": self.last_error,
            "recent_errors": recent_errors,
            "subscribed_count": len(self.subscribed_assets),
            "latency_ms": self.latency_ms,
            "max_buffer_age_seconds": self.max_buffer_age_seconds,
            "current_backoff_delay": self._reconnect_delay,
            "channel": "market",
        }

    async def pause(self):
        """
        Pause message buffering/processing for this channel.
        The WebSocket connection and keep-alive pings remain active (low resource use,
        no disconnect). New incoming messages are drained but discarded from buffer.
        Use resume() to continue normal operation.
        """
        self.paused = True
        return {"status": "paused", "channel": "market", "note": "Connection remains open for keep-alive"}

    async def resume(self):
        """Resume normal message buffering after a pause()."""
        self.paused = False
        return {"status": "resumed", "channel": "market"}

    def get_subscribed_assets(self) -> list[str]:
        """Convenience accessor for current subscriptions (useful after dynamic updates)."""
        return list(self.subscribed_assets)


class ManagedUserWebSocket:
    """
    Managed authenticated WebSocket for the User channel (orders, trades, fills).
    Includes full auto-reconnect with credential re-auth + re-subscribe on recovery.
    """
    def __init__(self):
        self.ws: Optional[websockets.WebSocketClientProtocol] = None
        self.task: Optional[asyncio.Task] = None
        self.messages: deque[dict] = deque(maxlen=500)
        self.running = False
        self.last_error: Optional[str] = None
        self.subscribed_markets: set[str] = set()

        # === Enhanced state (reconnect + buffering + health) ===
        self.reconnect_count: int = 0
        self.last_message_time: float = 0.0
        self.latency_ms: Optional[float] = None
        self._reconnect_delay: float = 1.0
        self._max_reconnect_delay: float = 60.0
        self.max_buffer_age_seconds: float = 300.0
        self._recent_hashes: dict[str, float] = {}
        self._hash_ttl: float = 60.0
        self._connect_params: dict[str, Any] = {}  # stores markets for resub on reconnect

        # Pause/resume + enhanced error/uptime (same as Market)
        self.paused: bool = False
        self._connect_time: float = 0.0
        self._error_history: deque[dict] = deque(maxlen=8)

    async def connect(self, markets: Optional[list[str]] = None):
        if self.running:
            if markets:
                await self.update_subscription("subscribe", markets)
            return {"status": "already_connected"}

        creds = get_official_credentials()
        if not (creds["clob_api_key"] and creds["clob_secret"] and creds["clob_passphrase"]):
            return _ws_error(
                "CLOB_API_KEY / SECRET / PASSPHRASE required for user channel",
                suggestion="User channel requires fresh CLOB creds; did you call check_clob_auth(include_raw=true) recently? Ensure the three CLOB_* variables (or PK for auto-derivation) are set in your MCP environment and restart the server."
            )

        # Persist subscription intent for reconnects
        if markets:
            self._connect_params["markets"] = list(markets)
            self.subscribed_markets.update(markets)
        else:
            self._connect_params["markets"] = None

        try:
            self.ws = await websockets.connect(USER_WS_URL, ping_interval=10)
            self.running = True
            self.reconnect_count = 0
            self._reconnect_delay = 1.0
            self.last_error = None

            sub_msg = {
                "auth": {
                    "apiKey": creds["clob_api_key"],
                    "secret": creds["clob_secret"],
                    "passphrase": creds["clob_passphrase"],
                },
                "type": "user",
            }
            if markets:
                sub_msg["markets"] = markets

            await self.ws.send(json.dumps(sub_msg))
            self.paused = False
            self._connect_time = time.time()
            self._error_history.clear()
            self.task = asyncio.create_task(self._connection_loop())
            return {"status": "connected", "markets": markets or "all"}

        except Exception as e:
            self._record_error(str(e))
            return {"status": "error", "error": str(e)}

    async def update_subscription(self, operation: str, markets: list[str]):
        """
        Dynamic subscription management on an already-connected User channel.

        operation: "subscribe" or "unsubscribe"
        markets: list of market/condition IDs to (un)subscribe

        Updates internal subscribed_markets for resilient reconnects.
        """
        if not self.ws or not self.running:
            return _ws_error(
                "No active connection. Use connect_user_websocket first.",
                suggestion="Call connect_user_websocket() (or with markets filter). For auth issues: call check_clob_auth(include_raw=true) first and verify CLOB_* credentials."
            )
        if operation not in ("subscribe", "unsubscribe"):
            return _ws_error(
                "operation must be exactly 'subscribe' or 'unsubscribe'",
                suggestion="Use update_market_subscription / update_user_subscription / update_sports_subscription with valid operation."
            )

        msg = {
            "operation": operation,
            "markets": markets,
        }
        try:
            await self.ws.send(json.dumps(msg))
            if operation == "subscribe":
                self.subscribed_markets.update(markets)
            else:
                self.subscribed_markets.difference_update(markets)
            return {"status": "ok", "operation": operation, "updated": markets, "subscribed_count": len(self.subscribed_markets)}
        except Exception as e:
            self._record_error(f"update_subscription_failed: {e}")
            return {"status": "error", "error": str(e)}

    def _record_error(self, error: str):
        """Internal helper: set last_error and append to the small ring buffer for diagnostics."""
        self.last_error = error
        ts = time.time()
        self._error_history.append({
            "timestamp": ts,
            "error": error
        })

    # -------------------------------------------------------------------------
    # Robust Reconnect + Buffering (User channel)
    # -------------------------------------------------------------------------

    async def _connection_loop(self):
        """
        Resilient loop for authenticated User WS.
        Re-fetches credentials and re-authenticates on every reconnect.
        Re-applies previous market filters from subscribed_markets.
        """
        while self.running:
            try:
                if self.ws is None:
                    try:
                        creds = get_official_credentials()
                        if not (creds["clob_api_key"] and creds["clob_secret"] and creds["clob_passphrase"]):
                            self._record_error("missing_user_creds_on_reconnect")
                            await self._backoff_sleep()
                            continue

                        self.ws = await websockets.connect(USER_WS_URL, ping_interval=10)

                        sub_msg = {
                            "auth": {
                                "apiKey": creds["clob_api_key"],
                                "secret": creds["clob_secret"],
                                "passphrase": creds["clob_passphrase"],
                            },
                            "type": "user",
                        }
                        markets = self._connect_params.get("markets") or list(self.subscribed_markets)
                        if markets:
                            sub_msg["markets"] = markets
                            self.subscribed_markets.update(markets)

                        await self.ws.send(json.dumps(sub_msg))
                        self.reconnect_count += 1
                        self.last_error = None
                        self._connect_time = time.time()
                        self._reconnect_delay = 1.0
                    except Exception as conn_e:
                        self._record_error(f"reconnect_failed: {conn_e}")
                        await self._backoff_sleep()
                        continue

                await self._recv_loop()

            except asyncio.CancelledError:
                raise
            except Exception as e:
                self._record_error(str(e))
            finally:
                if self.ws:
                    try:
                        await self.ws.close()
                    except Exception:
                        pass
                    self.ws = None

                if self.running:
                    await self._backoff_sleep()

    async def _backoff_sleep(self):
        """Exponential backoff with jitter (shared pattern)."""
        delay = min(self._reconnect_delay, self._max_reconnect_delay)
        jittered = delay * (0.75 + 0.5 * random.random())
        await asyncio.sleep(jittered)
        self._reconnect_delay = min(self._reconnect_delay * 2.0, self._max_reconnect_delay)

    async def _recv_loop(self):
        """Inner receive loop; exits on disconnect to allow outer reconnect."""
        try:
            while self.running and self.ws:
                try:
                    raw = await asyncio.wait_for(self.ws.recv(), timeout=30)
                    msg = json.loads(raw)
                    if not self.paused:
                        self._buffer_message(msg)
                    # If paused: drain but skip buffering (connection stays alive)
                except asyncio.TimeoutError:
                    if self.ws:
                        try:
                            await self.ws.send("PING")
                        except Exception:
                            break
                except ConnectionClosed:
                    self._record_error("ConnectionClosed")
                    break
                except Exception as recv_e:
                    self._record_error(str(recv_e))
                    break
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self._record_error(str(e))

    def _buffer_message(self, msg: dict):
        """Identical improved buffering logic as Market channel (timestamps + dedup + age prune)."""
        if self.paused:
            return
        if not isinstance(msg, dict):
            msg = {"raw": str(msg)}

        ts = time.time()
        try:
            core = {}
            for k in ("event_type", "type", "market", "asset_id", "assetId", "order_id", "trade_id",
                      "status", "price", "size", "side"):
                if k in msg:
                    core[k] = msg[k]
            fp_src = json.dumps(core, sort_keys=True, default=str)
            fp = hashlib.md5(fp_src.encode("utf-8")).hexdigest()
        except Exception:
            fp = hashlib.md5(str(msg)[:400].encode("utf-8", errors="ignore")).hexdigest()

        now = ts
        self._recent_hashes = {h: t for h, t in self._recent_hashes.items() if now - t < self._hash_ttl}
        if fp in self._recent_hashes:
            return

        self._recent_hashes[fp] = now

        msg = dict(msg)
        msg["_received_at"] = ts
        msg["_buffered_at"] = ts
        self.messages.append(msg)
        self.last_message_time = ts
        self._prune_old_messages()

    def _prune_old_messages(self):
        if self.max_buffer_age_seconds <= 0 or not self.messages:
            return
        cutoff = time.time() - self.max_buffer_age_seconds
        while self.messages and self.messages[0].get("_received_at", 0) < cutoff:
            self.messages.popleft()

    async def disconnect(self):
        """Explicit disconnect: stops reconnect attempts and clears state."""
        self.running = False
        if self.task:
            self.task.cancel()
            try:
                await asyncio.wait_for(self.task, timeout=2.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass
            except Exception:
                pass
        if self.ws:
            try:
                await self.ws.close()
            except Exception:
                pass
        self.ws = None
        self.messages.clear()
        self.subscribed_markets.clear()
        self._recent_hashes.clear()
        self._connect_params.clear()
        self.reconnect_count = 0
        self.last_error = None
        self.last_message_time = 0.0
        self.paused = False
        self._connect_time = 0.0
        self._error_history.clear()

    def get_recent_messages(self, limit: int = 50) -> list[dict]:
        return list(self.messages)[-limit:]

    def get_connection_health(self) -> dict:
        """
        Rich health metrics (user channel).

        Enhanced (see Market for full details): includes paused, uptime_seconds, recent_errors ring buffer.
        """
        now = time.time()
        last_age = (now - self.last_message_time) if self.last_message_time > 0 else None
        uptime = (now - self._connect_time) if self._connect_time > 0 else None

        recent_errors = []
        for entry in reversed(list(self._error_history)):
            age = round(now - entry.get("timestamp", now), 1)
            recent_errors.append({
                "error": entry.get("error"),
                "age_seconds": age
            })

        return {
            "connected": bool(self.running and self.ws is not None),
            "paused": self.paused,
            "reconnect_count": self.reconnect_count,
            "uptime_seconds": round(uptime, 1) if uptime is not None else None,
            "last_message_time": self.last_message_time or None,
            "last_message_age_seconds": round(last_age, 2) if last_age is not None else None,
            "buffered_messages": len(self.messages),
            "last_error": self.last_error,
            "recent_errors": recent_errors,
            "subscribed_count": len(self.subscribed_markets),
            "latency_ms": self.latency_ms,
            "max_buffer_age_seconds": self.max_buffer_age_seconds,
            "current_backoff_delay": self._reconnect_delay,
            "channel": "user",
        }

    async def pause(self):
        """Pause message buffering/processing (connection + keepalives stay alive)."""
        self.paused = True
        return {"status": "paused", "channel": "user", "note": "Connection remains open for keep-alive"}

    async def resume(self):
        """Resume normal message buffering."""
        self.paused = False
        return {"status": "resumed", "channel": "user"}

    def get_subscribed_markets(self) -> list[str]:
        """Convenience accessor for current user subscriptions."""
        return list(self.subscribed_markets)


class ManagedSportsWebSocket:
    """
    Managed WebSocket for the public Sports channel.
    Lightweight but now includes the same robust auto-reconnect,
    improved buffering, and health reporting as the other channels.
    """
    def __init__(self):
        self.ws: Optional[websockets.WebSocketClientProtocol] = None
        self.task: Optional[asyncio.Task] = None
        self.messages: deque[dict] = deque(maxlen=300)
        self.running = False
        self.last_error: Optional[str] = None

        # Sports-specific subscription tracking + enhanced state
        self.subscribed_leagues: set[str] = set()

        # === Enhanced state (reconnect + buffering + health) ===
        self.reconnect_count: int = 0
        self.last_message_time: float = 0.0
        self.latency_ms: Optional[float] = None
        self._reconnect_delay: float = 1.0
        self._max_reconnect_delay: float = 60.0
        self.max_buffer_age_seconds: float = 300.0
        self._recent_hashes: dict[str, float] = {}
        self._hash_ttl: float = 60.0
        self._connect_params: dict[str, Any] = {}

        # Pause/resume + error ring + uptime (consistent across channels)
        self.paused: bool = False
        self._connect_time: float = 0.0
        self._error_history: deque[dict] = deque(maxlen=8)

    async def connect(self, leagues: Optional[list[str]] = None):
        if self.running:
            # Dynamic incremental updates now supported externally via update_sports_subscription tool
            # (which calls the new update_subscription method). Reconnect always uses full tracked set.
            return {"status": "already_connected"}

        # Remember for reconnect
        if leagues:
            self._connect_params["leagues"] = list(leagues)
            self.subscribed_leagues.update(leagues)
        else:
            self._connect_params["leagues"] = None

        try:
            self.ws = await websockets.connect(SPORTS_WS_URL, ping_interval=15)
            self.running = True
            self.reconnect_count = 0
            self._reconnect_delay = 1.0
            self.last_error = None

            sub = {"type": "sports"}
            if leagues:
                sub["leagues"] = leagues

            await self.ws.send(json.dumps(sub))
            self.paused = False
            self._connect_time = time.time()
            self._error_history.clear()
            self.task = asyncio.create_task(self._connection_loop())
            return {"status": "connected", "leagues": leagues or "all"}
        except Exception as e:
            self._record_error(str(e))
            return {"status": "error", "error": str(e)}

    async def update_subscription(self, operation: str, leagues: list[str]):
        """
        Dynamic subscription management for Sports channel (if supported by backend).

        operation: "subscribe" | "unsubscribe"
        leagues: e.g. ["NBA", "NFL", "EPL"]

        On success updates the tracked subscribed_leagues (used for auto re-sub on reconnect).
        Note: initial connect uses a different message shape; this uses the operation form
        for consistency with Market/User. Reconnect logic always sends full current set.
        """
        if not self.ws or not self.running:
            return _ws_error(
                "No active connection. Use connect_sports_websocket first.",
                suggestion="Call connect_sports_websocket(leagues=[...]) or connect_sports_websocket() for all leagues."
            )
        if operation not in ("subscribe", "unsubscribe"):
            return _ws_error(
                "operation must be exactly 'subscribe' or 'unsubscribe'",
                suggestion="Use update_market_subscription / update_user_subscription / update_sports_subscription with valid operation."
            )

        msg = {
            "operation": operation,
            "leagues": leagues,
            "type": "sports",
        }
        try:
            await self.ws.send(json.dumps(msg))
            if operation == "subscribe":
                self.subscribed_leagues.update(leagues)
            else:
                self.subscribed_leagues.difference_update(leagues)
            return {"status": "ok", "operation": operation, "updated": leagues, "subscribed_count": len(self.subscribed_leagues)}
        except Exception as e:
            self._record_error(f"update_subscription_failed: {e}")
            return {"status": "error", "error": str(e)}

    def _record_error(self, error: str):
        """Internal helper: set last_error and append to the small ring buffer for diagnostics."""
        self.last_error = error
        ts = time.time()
        self._error_history.append({
            "timestamp": ts,
            "error": error
        })

    # -------------------------------------------------------------------------
    # Robust Reconnect + Buffering (Sports channel)
    # -------------------------------------------------------------------------

    async def _connection_loop(self):
        """Resilient loop with backoff + automatic league re-subscribe on recovery."""
        while self.running:
            try:
                if self.ws is None:
                    try:
                        self.ws = await websockets.connect(SPORTS_WS_URL, ping_interval=15)

                        sub = {"type": "sports"}
                        leagues = self._connect_params.get("leagues") or list(self.subscribed_leagues)
                        if leagues:
                            sub["leagues"] = leagues
                            self.subscribed_leagues.update(leagues)

                        await self.ws.send(json.dumps(sub))
                        self.reconnect_count += 1
                        self.last_error = None
                        self._connect_time = time.time()
                        self._reconnect_delay = 1.0
                    except Exception as conn_e:
                        self._record_error(f"reconnect_failed: {conn_e}")
                        await self._backoff_sleep()
                        continue

                await self._recv_loop()

            except asyncio.CancelledError:
                raise
            except Exception as e:
                self._record_error(str(e))
            finally:
                if self.ws:
                    try:
                        await self.ws.close()
                    except Exception:
                        pass
                    self.ws = None

                if self.running:
                    await self._backoff_sleep()

    async def _backoff_sleep(self):
        """Exponential backoff + jitter (identical pattern)."""
        delay = min(self._reconnect_delay, self._max_reconnect_delay)
        jittered = delay * (0.75 + 0.5 * random.random())
        await asyncio.sleep(jittered)
        self._reconnect_delay = min(self._reconnect_delay * 2.0, self._max_reconnect_delay)

    async def _recv_loop(self):
        """Inner receive loop for sports (longer idle tolerance)."""
        try:
            while self.running and self.ws:
                try:
                    raw = await asyncio.wait_for(self.ws.recv(), timeout=45)
                    msg = json.loads(raw) if raw else {}
                    if not self.paused:
                        self._buffer_message(msg)
                    # Paused: drain socket (keep connection) but discard from buffer
                except asyncio.TimeoutError:
                    if self.ws:
                        try:
                            await self.ws.send("PING")
                        except Exception:
                            break
                except ConnectionClosed:
                    self._record_error("ConnectionClosed")
                    break
                except Exception as recv_e:
                    self._record_error(str(recv_e))
                    break
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self._record_error(str(e))

    def _buffer_message(self, msg: dict):
        """Improved buffering with dedup, timestamps, and age pruning."""
        if self.paused:
            return
        if not isinstance(msg, dict):
            msg = {"raw": str(msg)}

        ts = time.time()
        try:
            core = {}
            for k in ("event_type", "type", "league", "sport", "match_id", "score", "status"):
                if k in msg:
                    core[k] = msg[k]
            fp_src = json.dumps(core, sort_keys=True, default=str)
            fp = hashlib.md5(fp_src.encode("utf-8")).hexdigest()
        except Exception:
            fp = hashlib.md5(str(msg)[:400].encode("utf-8", errors="ignore")).hexdigest()

        now = ts
        self._recent_hashes = {h: t for h, t in self._recent_hashes.items() if now - t < self._hash_ttl}
        if fp in self._recent_hashes:
            return

        self._recent_hashes[fp] = now

        msg = dict(msg)
        msg["_received_at"] = ts
        msg["_buffered_at"] = ts
        self.messages.append(msg)
        self.last_message_time = ts
        self._prune_old_messages()

    def _prune_old_messages(self):
        if self.max_buffer_age_seconds <= 0 or not self.messages:
            return
        cutoff = time.time() - self.max_buffer_age_seconds
        while self.messages and self.messages[0].get("_received_at", 0) < cutoff:
            self.messages.popleft()

    async def disconnect(self):
        """Explicit disconnect: stops reconnects and clears state."""
        self.running = False
        if self.task:
            self.task.cancel()
            try:
                await asyncio.wait_for(self.task, timeout=2.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass
            except Exception:
                pass
        if self.ws:
            try:
                await self.ws.close()
            except Exception:
                pass
        self.ws = None
        self.messages.clear()
        self.subscribed_leagues.clear()
        self._recent_hashes.clear()
        self._connect_params.clear()
        self.reconnect_count = 0
        self.last_error = None
        self.last_message_time = 0.0
        self.paused = False
        self._connect_time = 0.0
        self._error_history.clear()

    def get_recent_messages(self, limit: int = 30) -> list[dict]:
        return list(self.messages)[-limit:]

    def get_connection_health(self) -> dict:
        """
        Rich health metrics (sports channel).

        Enhanced (see Market): paused flag, uptime_seconds, recent_errors ring buffer.
        """
        now = time.time()
        last_age = (now - self.last_message_time) if self.last_message_time > 0 else None
        uptime = (now - self._connect_time) if self._connect_time > 0 else None

        recent_errors = []
        for entry in reversed(list(self._error_history)):
            age = round(now - entry.get("timestamp", now), 1)
            recent_errors.append({
                "error": entry.get("error"),
                "age_seconds": age
            })

        return {
            "connected": bool(self.running and self.ws is not None),
            "paused": self.paused,
            "reconnect_count": self.reconnect_count,
            "uptime_seconds": round(uptime, 1) if uptime is not None else None,
            "last_message_time": self.last_message_time or None,
            "last_message_age_seconds": round(last_age, 2) if last_age is not None else None,
            "buffered_messages": len(self.messages),
            "last_error": self.last_error,
            "recent_errors": recent_errors,
            "subscribed_count": len(self.subscribed_leagues),
            "latency_ms": self.latency_ms,
            "max_buffer_age_seconds": self.max_buffer_age_seconds,
            "current_backoff_delay": self._reconnect_delay,
            "channel": "sports",
        }

    async def pause(self):
        """Pause message buffering for Sports (keeps WS connection alive)."""
        self.paused = True
        return {"status": "paused", "channel": "sports", "note": "Connection remains open for keep-alive"}

    async def resume(self):
        """Resume message buffering for Sports."""
        self.paused = False
        return {"status": "resumed", "channel": "sports"}

    def get_subscribed_leagues(self) -> list[str]:
        """Convenience accessor for current sports league subscriptions."""
        return list(self.subscribed_leagues)


# =============================================================================
# Public diagnostic helpers (pure, for get_mcp_health_report + reuse)
# These mirror the logic of the MCP tools but are importable/callable directly.
# =============================================================================

def get_all_websocket_status() -> dict:
    """
    Lightweight cross-channel WS status snapshot (pure function).
    Used by health report and the MCP get_websocket_status tool.
    """
    now = time.time()
    status = {}

    if _market_ws:
        uptime = (now - _market_ws._connect_time) if _market_ws._connect_time > 0 else None
        recent_errs = len(_market_ws._error_history)
        status["market"] = {
            "connected": _market_ws.running,
            "paused": _market_ws.paused,
            "subscribed_assets": list(_market_ws.subscribed_assets),
            "last_error": _market_ws.last_error,
            "buffered_messages": len(_market_ws.messages),
            "reconnect_count": _market_ws.reconnect_count,
            "last_message_age": round(now - _market_ws.last_message_time, 1) if _market_ws.last_message_time else None,
            "uptime_seconds": round(uptime, 1) if uptime is not None else None,
            "recent_error_count": recent_errs,
        }
    else:
        status["market"] = {"connected": False}

    if _user_ws:
        uptime = (now - _user_ws._connect_time) if _user_ws._connect_time > 0 else None
        recent_errs = len(_user_ws._error_history)
        status["user"] = {
            "connected": _user_ws.running,
            "paused": _user_ws.paused,
            "subscribed_markets": list(_user_ws.subscribed_markets),
            "last_error": _user_ws.last_error,
            "buffered_messages": len(_user_ws.messages),
            "reconnect_count": _user_ws.reconnect_count,
            "last_message_age": round(now - _user_ws.last_message_time, 1) if _user_ws.last_message_time else None,
            "uptime_seconds": round(uptime, 1) if uptime is not None else None,
            "recent_error_count": recent_errs,
        }
    else:
        status["user"] = {"connected": False}

    if _sports_ws:
        uptime = (now - _sports_ws._connect_time) if _sports_ws._connect_time > 0 else None
        recent_errs = len(_sports_ws._error_history)
        status["sports"] = {
            "connected": _sports_ws.running,
            "paused": _sports_ws.paused,
            "subscribed_leagues": list(_sports_ws.subscribed_leagues),
            "last_error": _sports_ws.last_error,
            "buffered_messages": len(_sports_ws.messages),
            "reconnect_count": _sports_ws.reconnect_count,
            "last_message_age": round(now - _sports_ws.last_message_time, 1) if _sports_ws.last_message_time else None,
            "uptime_seconds": round(uptime, 1) if uptime is not None else None,
            "recent_error_count": recent_errs,
        }
    else:
        status["sports"] = {"connected": False}

    return status


def get_detailed_connection_health(channel: str = "market") -> dict:
    """
    Detailed per-channel health (pure). Delegates to the Managed* impl when active.
    Used by health report + the MCP get_connection_health tool.
    """
    if channel == "market" and _market_ws:
        return _market_ws.get_connection_health()
    if channel == "user" and _user_ws:
        return _user_ws.get_connection_health()
    if channel == "sports" and _sports_ws:
        return _sports_ws.get_connection_health()
    return _ws_error(
        f"No active {channel} websocket",
        suggestion="Connect first with connect_*_websocket or one of the high-level watch/start_full_market_monitor tools. Then retry get_connection_health / health report.",
        channel=channel,
        connected=False,
    )


# =============================================================================
# MCP Tool Registration
# =============================================================================

def register_websocket_tools(mcp):
    """Register WebSocket management tools with the FastMCP server."""

    @mcp.tool
    async def connect_market_websocket(
        identifiers: list[str],
        initial_dump: bool = True,
        level: int = 2,
    ) -> dict:
        """
        Connect to the public Market WebSocket for real-time data.

        identifiers: Flexible list — can be mix of:
            - clobTokenIds (recommended for speed)
            - market slugs (e.g. "will-trump-win-2024")
            - condition_ids (0x...)

        The tool will automatically resolve them to token IDs using Gamma.
        """
        resolved = _resolve_token_ids(identifiers)
        if not resolved:
            return _ws_error(
                "Could not resolve any valid token IDs from the provided identifiers",
                suggestion="Pass valid slugs (e.g. 'will-trump-win-2024'), condition_ids (0x...), or raw clobTokenIds. Try search_markets or get_clob_token_ids first to discover good identifiers."
            )

        global _market_ws
        if _market_ws is None:
            _market_ws = ManagedMarketWebSocket()

        result = await _market_ws.connect(resolved, initial_dump, level)
        result["resolved_token_ids"] = resolved
        return result

    # -------------------------------------------------------------------------
    # High-level Gamma + WS convenience tools (Gamma + WebSocket integration)
    # -------------------------------------------------------------------------

    @mcp.tool
    async def watch_market_by_slug(
        slug: str,
        initial_dump: bool = True,
        level: int = 2,
    ) -> dict:
        """
        High-level convenience: resolve a market by slug via Gamma and subscribe it to the Market WS.

        - slug: Polymarket market slug (e.g. "will-trump-win-2024", "bitcoin-above-100k-june")
        - Delegates fully to connect_market_websocket (and its internal _resolve_token_ids + Gamma calls).
        - Starts (or augments) the real-time market data stream for that market's tokens.
        - Returns the same shape as connect_market_websocket, including "resolved_token_ids".

        Use this when you know the exact slug. Agent-friendly one-liner to start watching.
        Combine with listen_for_ws_events or get_latest_ws_messages to consume data.
        """
        if not slug or not isinstance(slug, str) or not slug.strip():
            return _ws_error(
                "slug is required (string, e.g. 'will-trump-win-2024')",
                suggestion="Use exact Polymarket market slug. Discover via search_markets(query=...) or get_events()."
            )

        # Under the hood: existing resolution logic + subscription
        return await connect_market_websocket(
            identifiers=[slug.strip()],
            initial_dump=initial_dump,
            level=level,
        )

    @mcp.tool
    async def watch_markets_by_query(
        query: str,
        limit: int = 5,
    ) -> dict:
        """
        High-level convenience: search Gamma (via /public-search, like search_markets / get_events),
        discover relevant active markets, then auto-subscribe their tokens to the Market WebSocket.

        - query: free-text search (e.g. "election", "bitcoin", "will harris win")
        - limit: max number of markets to subscribe (default 5, hard cap 20)

        Internally extracts slugs/condition_ids from the Gamma response (handles events[].markets shape),
        then calls connect_market_websocket under the hood for resolution + subscription.

        Returns connect result augmented with "query", "matched_identifiers_before_resolve", etc.
        Good errors + suggestions on failure.

        Ideal for agents that want "live data on anything matching X" without manual slug hunting.
        """
        q = (query or "").strip()
        if not q:
            return _ws_error(
                "query is required (non-empty search string)",
                suggestion="Provide natural language or keywords, e.g. 'election', 'bitcoin above 100k'. See watch_markets_by_query docs."
            )

        n = max(1, min(int(limit or 5), 20))

        try:
            params = {"q": q, "limit": n, "active": "true"}
            data = _gamma_get("/public-search", params)

            if isinstance(data, dict) and "error" in data:
                return {
                    "status": "error",
                    "error": f"Gamma search failed: {data.get('error')}",
                    "details": data,
                }

            # Robustly extract usable identifiers (slugs > condition ids) from common Gamma shapes
            identifiers: list[str] = []
            seen: set[str] = set()

            def _add(val: Any) -> None:
                if isinstance(val, str):
                    v = val.strip()
                    if v and v not in seen:
                        seen.add(v)
                        identifiers.append(v)

            containers: list[Any] = []
            if isinstance(data, dict):
                containers.extend(data.get("events", []) or [])
                containers.extend(data.get("results", []) or [])
                if isinstance(data.get("markets"), list):
                    containers.append({"markets": data["markets"]})
                _add(data.get("slug"))
                _add(data.get("conditionId") or data.get("condition_id"))
            elif isinstance(data, list):
                containers.extend(data)

            for item in containers:
                if not isinstance(item, dict):
                    continue
                _add(item.get("slug"))
                _add(item.get("conditionId") or item.get("condition_id"))
                for m in item.get("markets") or []:
                    if isinstance(m, dict):
                        _add(m.get("slug"))
                        _add(m.get("conditionId") or m.get("condition_id"))

            # Flat list fallback (some responses)
            if not identifiers and isinstance(data, list):
                for m in data:
                    if isinstance(m, dict):
                        _add(m.get("slug"))
                        _add(m.get("conditionId") or m.get("condition_id"))

            if not identifiers:
                return {
                    "status": "error",
                    "error": f"No active markets matched query '{q}'",
                    "suggestion": "Broaden the query or call search_markets / get_events directly first to explore.",
                }

            # Delegate to existing connect_market_websocket (does Gamma resolution + WS sub)
            result = await connect_market_websocket(
                identifiers=identifiers[:n],
                initial_dump=True,
                level=2,
            )
            result["query"] = q
            result["searched_limit"] = n
            result["matched_identifiers_before_resolve"] = identifiers[:n]
            return result

        except Exception as exc:
            return _ws_error(
                f"watch_markets_by_query failed: {str(exc)}",
                suggestion="Check Gamma connectivity or broaden query. Also see get_gamma_docs() and search_markets as alternatives."
            )

    @mcp.tool
    async def auto_subscribe_popular_markets(
        category: Optional[str] = None,
        limit: int = 10,
    ) -> dict:
        """
        High-level convenience for agents: instantly subscribe the Market WS to popular/hot markets.

        - category: optional tag filter (e.g. "crypto", "politics", "sports", "entertainment").
          Use get_tags() from Gamma to discover valid categories. None = all active popular.
        - limit: how many top markets to subscribe (default 10, capped at 30)

        Implementation:
        - Queries Gamma /markets (active only, with optional tag) — same style as get_active_markets.
        - Falls back to /events if needed.
        - Heuristically ranks by volume/liquidity fields when present.
        - Extracts slugs/condition ids and calls connect_market_websocket under the hood.

        Returns augmented connect result with "selected_identifiers", "category", "note".
        Excellent for quick real-time dashboards: "just give me live data on whatever is hot right now".
        """
        n = max(1, min(int(limit or 10), 30))
        cat = (category or "").strip() if category else None

        try:
            params: dict[str, Any] = {
                "active": "true",
                "closed": "false",
                "limit": str(n * 2),  # overfetch for better ranking
            }
            if cat:
                params["tag"] = cat

            data = _gamma_get("/markets", params)

            if isinstance(data, dict) and "error" in data:
                # Fallback using events style
                ev_params = {"active": "true", "limit": str(n * 2)}
                if cat:
                    ev_params["tag"] = cat
                data = _gamma_get("/events", ev_params)
                if isinstance(data, dict) and "error" in data:
                    return _ws_error(
                        f"Gamma popular query failed: {data.get('error')}",
                        suggestion="Try without category filter or use search_markets + start_full_market_monitor instead."
                    )

            # Normalize to list of market dicts (handles /markets list or events with .markets)
            markets: list[dict] = []
            if isinstance(data, list):
                markets = [m for m in data if isinstance(m, dict)]
            elif isinstance(data, dict):
                if isinstance(data.get("markets"), list):
                    markets = [m for m in data["markets"] if isinstance(m, dict)]
                else:
                    for ev in (data.get("events", []) or data.get("results", []) or []):
                        if isinstance(ev, dict):
                            markets.extend([m for m in (ev.get("markets") or []) if isinstance(m, dict)])

            if not markets:
                return _ws_error(
                    "Gamma returned no active markets for popular subscription (try without category or check connectivity)",
                    suggestion="Call get_active_markets() or search_markets() manually first; or omit category."
                )

            # Rank by common popularity/volume signals (descending)
            def _pop_score(m: dict) -> float:
                for key in ("volume", "volume24hr", "liquidity", "volumeNum", "totalVolume", "volume_num"):
                    val = m.get(key)
                    if isinstance(val, (int, float)):
                        return float(val)
                    if isinstance(val, str):
                        try:
                            return float(val)
                        except Exception:
                            continue
                return 0.0

            markets.sort(key=_pop_score, reverse=True)

            # Build identifier list for delegation (prefer human-readable slugs)
            identifiers: list[str] = []
            seen: set[str] = set()
            for m in markets:
                ident = (
                    m.get("slug")
                    or m.get("conditionId")
                    or m.get("condition_id")
                    or (str(m.get("id")) if m.get("id") is not None else None)
                )
                if isinstance(ident, str):
                    ident = ident.strip()
                    if ident and ident not in seen:
                        seen.add(ident)
                        identifiers.append(ident)
                if len(identifiers) >= n:
                    break

            if not identifiers:
                # Last resort
                identifiers = [
                    (m.get("slug") or m.get("conditionId") or str(m.get("id", ""))).strip()
                    for m in markets[:n]
                    if m.get("slug") or m.get("conditionId") or m.get("id")
                ]
                identifiers = [i for i in identifiers if i]

            if not identifiers:
                return _ws_error(
                    "Failed to extract any usable market identifiers from Gamma popular results",
                    suggestion="Use search_markets or get_events to find markets, then pass slugs to start_full_market_monitor."
                )

            # Under the hood delegation — full resolution + WS connect
            result = await connect_market_websocket(
                identifiers=identifiers[:n],
                initial_dump=True,
                level=2,
            )
            result["category"] = cat
            result["limit"] = n
            result["selected_identifiers"] = identifiers[:n]
            result["note"] = "Auto-subscribed via volume/liquidity ranking of active markets (Gamma)"
            return result

        except Exception as exc:
            return _ws_error(
                f"auto_subscribe_popular_markets failed: {str(exc)}",
                suggestion="Verify Gamma /markets endpoint reachable. Alternative: use watch_markets_by_query or start_full_market_monitor with your own queries."
            )

    # -------------------------------------------------------------------------
    # Ultimate high-level entrypoint: start_full_market_monitor (Gamma discovery + WS)
    # -------------------------------------------------------------------------

    @mcp.tool
    async def start_full_market_monitor(
        slugs_or_queries: list[str],
        max_per_query: int = 6,
        initial_dump: bool = True,
        level: int = 2,
    ) -> dict:
        """
        HIGH-LEVEL RECOMMENDED ENTRYPOINT for real-time monitoring.

        One-call "Gamma discovery + connect Market WS" powerhouse.
        Accepts a mixed list of:
          - exact market slugs (e.g. "will-trump-win-2024")
          - free-text queries (e.g. "bitcoin", "election 2024 harris", "will fed cut rates")

        For each query-like item it internally uses watch_markets_by_query (Gamma /public-search + auto-sub).
        For clean slugs it uses watch_market_by_slug.
        Everything funnels into a single connect_market_websocket call for efficiency.

        Returns a rich "monitor" status object (see realtime_helpers.MONITOR_STATUS_SHAPE)
        with subscribed tokens, next-step hints, and consumption guidance.

        This + listen_for_ws_events / get_latest_ws_messages is the fastest path
        from "I want to watch X" to live data in an agent.
        """
        if not slugs_or_queries or not isinstance(slugs_or_queries, (list, tuple)):
            return _ws_error(
                "slugs_or_queries must be a non-empty list of strings (slugs or search queries)",
                suggestion="Example: ['will-trump-win-2024', 'bitcoin', 'election 2024']. Mix of exact slugs and free-text queries works."
            )

        all_identifiers: list[str] = []
        seen: set[str] = set()
        query_results: list[dict] = []
        slug_results: list[dict] = []

        for item in slugs_or_queries:
            if not isinstance(item, str):
                continue
            s = item.strip()
            if not s:
                continue

            # Heuristic: treat as query if it contains spaces or looks like natural language
            looks_like_query = (" " in s) or len(s) > 40 or any(w in s.lower() for w in ("will ", "is ", "above", "below", "election", "bitcoin", "crypto", "fed", "trump", "harris"))

            try:
                if looks_like_query:
                    res = await watch_markets_by_query(query=s, limit=max_per_query)
                    query_results.append({"query": s, "result": res})
                    # The watch tool already performed resolution + subscription; collect what it resolved if present
                    if isinstance(res, dict):
                        for tid in (res.get("resolved_token_ids") or []):
                            if tid not in seen:
                                seen.add(tid)
                                all_identifiers.append(tid)
                else:
                    res = await watch_market_by_slug(slug=s, initial_dump=initial_dump, level=level)
                    slug_results.append({"slug": s, "result": res})
                    if isinstance(res, dict):
                        for tid in (res.get("resolved_token_ids") or []):
                            if tid not in seen:
                                seen.add(tid)
                                all_identifiers.append(tid)
            except Exception as e:
                # Continue; we'll report partial success
                query_results.append({"query": s, "error": str(e)})

        # Final status aggregation (the individual watch calls already mutated the WS subscription)
        status = get_websocket_status().get("market", {})
        health = get_connection_health("market") if "market" in get_websocket_status() else {}

        monitor = {
            "status": "monitoring" if status.get("connected") else "partial",
            "monitor_type": "full_market",
            "discovered_via": "mixed slugs + gamma_queries",
            "identifiers_used": list(slugs_or_queries),
            "resolved_token_ids_sample": all_identifiers[:12],
            "subscribed_count": status.get("subscribed_count") or len(status.get("subscribed_assets", [])),
            "channels_active": ["market"],
            "how_to_consume": realtime_helpers.get_realtime_story_summary(),
            "recommended_next_calls": realtime_helpers.get_recommended_monitor_workflow()[:5],
            "query_breakdown": {"queries_processed": len(query_results), "slugs_processed": len(slug_results)},
            "health_snapshot": {
                "connected": status.get("connected"),
                "reconnect_count": status.get("reconnect_count"),
                "last_message_age": status.get("last_message_age"),
                "buffered_messages": status.get("buffered_messages"),
            },
            "note": "Fully managed background WS with auto-reconnect. Use listen_for_ws_events for push-like consumption or get_latest_ws_messages for instant polls. Combine with CLOB tools on interesting events.",
        }

        # Also surface the underlying connect-style result for the last action if useful
        monitor["raw_watch_results_sample"] = (slug_results + query_results)[:3]

        return monitor

    @mcp.tool
    def get_realtime_helper_patterns() -> dict:
        """
        Call get_polymarket_llms_txt() + get_mcp_health_report() first for any session.
        Returns copy-paste-ready async patterns and recipes for common real-time loops.

        These patterns are the official recommended way for agents to consume the
        managed WebSocket firehose together with Gamma + CLOB.

        Sourced from polymarket_alpha.realtime_helpers — the single source for
        high-level realtime usage examples in this MCP.
        """
        return {
            "summary": realtime_helpers.get_realtime_story_summary(),
            "monitor_status_shape": realtime_helpers.get_monitor_object_template(),
            "recommended_workflow_steps": realtime_helpers.get_recommended_monitor_workflow(),
            "copy_paste_loops": realtime_helpers.get_copy_paste_realtime_loops(),
            "parse_helpers": {
                "parse_ws_event": "Call realtime_helpers.parse_ws_event(raw_msg_from_listen_or_get_latest) to get normalized {event_type, normalized: {...}, specific: {...}, raw}. Handles all market/user/sports shapes + common field variants. Production essential for clean agent logic.",
                "get_ws_event_types_help": "Reference for supported event types and usage. Call for quick docs.",
                "example": "parsed = realtime_helpers.parse_ws_event(event); price = parsed['normalized'].get('price')"
            },
            "event_driven_trading_patterns": "NEW focused surface: call get_ws_event_driven_patterns() for the dedicated on_price_move_then_place_limit / on_my_fill_then_risk_check / multi_asset_book_delta_watcher (and friends) + full library. These are the premium event-reaction recipes.",
            "usage": "Import or call this tool, then adapt the snippets. All examples assume prior call to start_full_market_monitor, watch_*, or connect_*_websocket. Use the parse_helpers (parse_ws_event) on every message from listen_for_ws_events / get_latest_ws_messages for robust field access. For trading reactions specifically: get_ws_event_driven_patterns().",
        }

    @mcp.tool
    def get_ws_event_driven_patterns() -> dict:
        """
        Call get_polymarket_llms_txt() + get_mcp_health_report() first for any session.
        Lightweight tool surfacing the focused set of event-driven trading patterns
        (on_price_move_then_place_limit, on_my_fill_then_risk_check, multi_asset_book_delta_watcher, ...)
        plus the full existing library for convenience.

        These are the highest-leverage "react to WS event X then do Y" copy-paste recipes.
        Use together with get_realtime_helper_patterns() and get_realtime_trading_guide().
        """
        from . import realtime_helpers
        return {
            "title": "WS Event-Driven Trading Patterns (copy-paste ready)",
            "description": "Specialized async reaction patterns for price moves, your own fills, and multi-asset book/liquidity watching. All assume you have wired a channel with start_full_market_monitor / watch_* / connect_* first, then consume via listen_for_ws_events + parse_ws_event.",
            "new_focused_event_driven_patterns": realtime_helpers.get_event_driven_trading_patterns(),
            "full_copy_paste_library": realtime_helpers.get_copy_paste_realtime_loops(),
            "parse_helper_note": "Always pipe raw events through realtime_helpers.parse_ws_event() (or the exposed parse in patterns) for clean 'normalized' + 'specific' fields before your reaction logic.",
            "quick_start": "Call get_realtime_trading_guide() + start_full_market_monitor(...) + this tool, then drop the desired async def into your agent loop.",
            "related_tools": [
                "get_realtime_helper_patterns()",
                "get_realtime_trading_guide()",
                "listen_for_ws_events + get_latest_ws_messages",
                "parse_ws_event (via realtime_helpers or helper patterns)"
            ]
        }

    @mcp.tool
    async def disconnect_market_websocket() -> dict:
        """Disconnect from the Market WebSocket."""
        global _market_ws
        if _market_ws:
            await _market_ws.disconnect()
            _market_ws = None
            return {"status": "disconnected"}
        return {"status": "not_connected"}

    @mcp.tool
    async def connect_user_websocket(markets: Optional[list[str]] = None) -> dict:
        """
        Connect to the authenticated User WebSocket for real-time order and trade updates.

        Requires valid CLOB_API_KEY / SECRET / PASSPHRASE.
        markets: Optional list of condition IDs to filter (default = all your markets).
        """
        global _user_ws
        if _user_ws is None:
            _user_ws = ManagedUserWebSocket()

        return await _user_ws.connect(markets)

    @mcp.tool
    async def disconnect_user_websocket() -> dict:
        """Disconnect from the User WebSocket."""
        global _user_ws
        if _user_ws:
            await _user_ws.disconnect()
            _user_ws = None
            return {"status": "disconnected"}
        return {"status": "not_connected"}

    # -------------------------------------------------------------------------
    # Dynamic subscription management tools (operate on already-connected channels)
    # -------------------------------------------------------------------------

    @mcp.tool
    async def update_market_subscription(
        operation: str,
        identifiers: list[str],
    ) -> dict:
        """
        Dynamically subscribe or unsubscribe markets on an *already connected* Market WebSocket.

        This is the primary clean tool for live subscription changes without full reconnect.

        operation: "subscribe" or "unsubscribe"
        identifiers: list of slugs, condition_ids, or raw clobTokenIds (auto-resolved via Gamma)

        Returns result including updated subscribed count. The internal subscribed set
        is kept in sync for automatic re-subscription on any future reconnects.

        Example agent usage:
            await update_market_subscription("subscribe", ["will-trump-2028", "some-condition-id"])
            await update_market_subscription("unsubscribe", ["old-token-123..."])
        """
        global _market_ws
        if _market_ws is None or not _market_ws.running:
            return _ws_error(
                "Market WebSocket not connected. Call connect_market_websocket (or watch_*) first.",
                suggestion="Use start_full_market_monitor, watch_market_by_slug, or connect_market_websocket before dynamic updates."
            )
        if operation not in ("subscribe", "unsubscribe"):
            return {"status": "error", "error": "operation must be exactly 'subscribe' or 'unsubscribe'"}

        resolved = _resolve_token_ids(identifiers)
        if not resolved:
            return _ws_error(
                "Could not resolve any valid token IDs from identifiers",
                suggestion="Use slugs, condition_ids or token ids. See get_clob_token_ids or search_markets for valid values."
            )

        return await _market_ws.update_subscription(operation, resolved)

    @mcp.tool
    async def update_user_subscription(
        operation: str,
        markets: list[str],
    ) -> dict:
        """
        Dynamically subscribe or unsubscribe specific markets on an *already connected* User WebSocket.

        operation: "subscribe" | "unsubscribe"
        markets: list of condition IDs (or market identifiers understood by the user channel)

        Keeps internal state consistent for reconnect resilience.
        """
        global _user_ws
        if _user_ws is None or not _user_ws.running:
            return _ws_error(
                "User WebSocket not connected. Call connect_user_websocket first.",
                suggestion="connect_user_websocket() first. Remember: requires valid CLOB creds — call check_clob_auth() if auth errors appear."
            )
        if operation not in ("subscribe", "unsubscribe"):
            return {"status": "error", "error": "operation must be exactly 'subscribe' or 'unsubscribe'"}

        return await _user_ws.update_subscription(operation, markets)

    @mcp.tool
    async def update_sports_subscription(
        operation: str,
        leagues: list[str],
    ) -> dict:
        """
        Dynamically subscribe or unsubscribe leagues on an *already connected* Sports WebSocket.

        operation: "subscribe" | "unsubscribe"
        leagues: e.g. ["NBA", "NFL"]

        Uses the operation protocol (best-effort; reconnects always re-send full current set).
        """
        global _sports_ws
        if _sports_ws is None or not _sports_ws.running:
            return _ws_error(
                "Sports WebSocket not connected. Call connect_sports_websocket first.",
                suggestion="Call connect_sports_websocket(leagues=['NBA', 'NFL']) or without args for all."
            )
        if operation not in ("subscribe", "unsubscribe"):
            return {"status": "error", "error": "operation must be exactly 'subscribe' or 'unsubscribe'"}

        return await _sports_ws.update_subscription(operation, leagues)

    # -------------------------------------------------------------------------
    # Pause / Resume helpers (keep connection alive, stop buffering for lower resource use)
    # -------------------------------------------------------------------------

    @mcp.tool
    async def pause_websocket(channel: str = "market") -> dict:
        """
        Pause message processing/buffering on a channel while KEEPING the WebSocket connection
        and keep-alive pings alive. Useful for temporarily reducing CPU/memory/IO during idle periods
        without incurring reconnect cost/latency later.

        Call resume_websocket(channel) to resume normal buffering.
        Connection health / uptime tracking continues.
        """
        target = None
        if channel == "market":
            target = _market_ws
        elif channel == "user":
            target = _user_ws
        elif channel == "sports":
            target = _sports_ws

        if not target or not target.running:
            return _ws_error(
                f"No active {channel} websocket to pause",
                suggestion="Connect the channel first (connect_*_websocket or high-level watch/start tools).",
                channel=channel
            )

        return await target.pause()

    @mcp.tool
    async def resume_websocket(channel: str = "market") -> dict:
        """
        Resume message buffering on a previously paused channel.
        """
        target = None
        if channel == "market":
            target = _market_ws
        elif channel == "user":
            target = _user_ws
        elif channel == "sports":
            target = _sports_ws

        if not target or not target.running:
            return _ws_error(
                f"No active {channel} websocket to resume",
                suggestion="Connect the channel first (connect_*_websocket or high-level watch/start tools).",
                channel=channel
            )

        return await target.resume()

    # -------------------------------------------------------------------------
    # High-value batteries-included convenience tool
    # -------------------------------------------------------------------------

    @mcp.tool
    async def start_realtime_market_watcher(
        identifiers: list[str],
        on_event_types: Optional[list[str]] = None,
        initial_dump: bool = False,
        level: int = 2,
    ) -> dict:
        """
        Batteries-included one-shot starter for real-time market monitoring.

        Connects (or augments) the Market WebSocket to the given identifiers,
        then returns a ready-to-consume "subscription" handle + recommended
        consumption pattern.

        - identifiers: slugs / condition_ids / tokenIds (resolved automatically)
        - on_event_types: Optional filter hint for consumers, e.g. ["price_change", "last_trade_price", "book"].
          Stored in response; the listen/get_latest tools accept the same list for filtering.
        - initial_dump: whether to request full orderbook snapshot on (re)connect (default False for watcher)
        - level: book depth (2 recommended)

        Returns:
          - status, resolved_token_ids, subscribed, plus:
            - subscription_id: a stable handle you can log/reference (synthetic)
            - recommended_next_steps + example usage with listen_for_ws_events / get_latest_ws_messages
              pre-filtered to your on_event_types
            - health_hint: call get_connection_health("market") to monitor

        This is the "just start watching and tell me how to consume" high-level tool.
        Perfect for agents that want minimal boilerplate for live price/trade feeds.
        """
        global _market_ws
        if _market_ws is None:
            _market_ws = ManagedMarketWebSocket()

        # Delegate to the robust connect (handles already-running update automatically)
        connect_result = await connect_market_websocket(
            identifiers=identifiers,
            initial_dump=initial_dump,
            level=level,
        )

        if connect_result.get("status") == "error":
            return connect_result

        # Create a friendly subscription handle
        ts = int(time.time())
        sub_id = f"market_watcher_{ts}_{len(identifiers)}assets"

        event_types = on_event_types or ["price_change", "last_trade_price"]

        # Enrich response
        result = dict(connect_result)
        result.update({
            "subscription_id": sub_id,
            "watcher_for_event_types": event_types,
            "ready_to_consume": True,
            "recommended_next_steps": {
                "poll_latest": f"Call get_latest_ws_messages(channel='market', event_types={event_types}, limit=20)",
                "listen_blocking": f"Call listen_for_ws_events(channel='market', event_types={event_types}, timeout_seconds=10, wait_for_event_type='{event_types[0] if event_types else 'price_change'}')",
                "monitor_health": "Call get_connection_health('market') or get_websocket_status()",
                "dynamic_changes": "Use update_market_subscription(operation, identifiers) to add/remove live",
                "pause_when_idle": "Use pause_websocket('market') / resume_websocket('market') to save resources",
            },
            "note": "Connection is running in background. Use the subscription_id for your logs. Filter with the suggested event_types for clean streams.",
        })
        return result

    # -------------------------------------------------------------------------
    # NEW high-level convenience: get_realtime_market_snapshot
    # One-shot "connect/reuse WS + latest WS data + fresh CLOB public snapshots"
    # -------------------------------------------------------------------------

    @mcp.tool
    async def get_realtime_market_snapshot(identifiers: list[str]) -> dict:
        """
        HIGH-LEVEL CONVENIENCE: Get a combined real-time + public snapshot for markets.

        - identifiers: slugs, condition_ids, or clobTokenIds (auto-resolved)
        - Ensures (or re-uses) a Market WebSocket connection for the assets.
        - Pulls recent buffered WS events (book, price_change, trade, etc.) and parses them cleanly.
        - Fetches fresh public CLOB data (orderbook, best bid/ask/mid, spread, recent trades) for each resolved token.
        - Returns everything in one structured payload with parsed events + per-asset clob_snapshot.

        Perfect "single call" for agents that want current state + live context without managing separate steps.
        Complements (does not replace) the long-lived listen/get_latest patterns.

        Uses the new parse_ws_event helper internally for clean WS output.
        """
        resolved = _resolve_token_ids(identifiers)
        if not resolved:
            return _ws_error(
                "Could not resolve any valid token IDs from identifiers",
                suggestion="Provide slugs/condition_ids/token_ids. Use search_markets or get_clob_token_ids to discover."
            )

        global _market_ws
        if _market_ws is None:
            _market_ws = ManagedMarketWebSocket()

        # Connect or augment subscription (non-destructive for existing)
        try:
            conn_result = await connect_market_websocket(
                identifiers=resolved,
                initial_dump=True,
                level=2,
            )
            # Brief yield so initial_dump / recent messages have a chance to arrive
            if conn_result.get("status") != "error":
                await asyncio.sleep(0.65)
        except Exception as conn_exc:
            conn_result = _ws_error(f"WS connect attempt failed: {conn_exc}", suggestion="Network or resolution issue; check get_connection_health after.")

        # Pull recent WS activity (zero-wait)
        from . import realtime_helpers
        recent_raw = get_latest_ws_messages(channel="market", limit=40)
        # Filter to our assets where possible + parse
        parsed_events = []
        relevant_raw = []
        for m in recent_raw:
            aid = m.get("asset_id") or m.get("assetId") or m.get("market")
            if not aid or aid in resolved or any(aid == r for r in resolved):
                relevant_raw.append(m)
                try:
                    parsed = realtime_helpers.parse_ws_event(m)
                    parsed_events.append(parsed)
                except Exception:
                    parsed_events.append({"event_type": m.get("event_type") or m.get("type"), "normalized": {"asset_id": aid}, "raw": m})

        # Fresh CLOB public snapshots (lightweight, independent of WS)
        clob_snapshots: dict[str, dict] = {}
        try:
            # Local client for public data (avoids cross-module closure issues)
            from py_clob_client_v2 import ClobClient
            from .config import get_clob_host, get_chain_id
            clob = ClobClient(host=get_clob_host(), chain_id=get_chain_id())

            for tid in resolved[:12]:  # safety cap
                snap: dict[str, Any] = {}
                try:
                    snap["orderbook"] = clob.get_order_book(tid)
                except Exception as obe:
                    snap["orderbook_error"] = str(obe)
                try:
                    snap["price"] = {
                        "best_bid": clob.get_price(tid, "SELL"),
                        "best_ask": clob.get_price(tid, "BUY"),
                        "midpoint": clob.get_midpoint(tid),
                    }
                except Exception as pe:
                    snap["price_error"] = str(pe)
                try:
                    snap["spread"] = clob.get_spread(tid)
                except Exception:
                    pass
                try:
                    snap["recent_trades"] = (clob.get_trades(tid, limit=8) or [])[:8]
                except Exception:
                    pass
                clob_snapshots[tid] = snap
        except Exception as clob_exc:
            clob_snapshots = {"error": f"CLOB public snapshot failed: {clob_exc}", "suggestion": "Public endpoints may be temporarily unavailable; WS data is still fresh."}

        # Aggregate nice summary
        summary = {
            "num_resolved": len(resolved),
            "ws_connected": bool(_market_ws and _market_ws.running),
            "ws_recent_event_count": len(relevant_raw),
            "clob_tokens_snapped": len([k for k in clob_snapshots if not k.startswith("error")]),
        }

        return {
            "status": "ok",
            "identifiers_input": identifiers,
            "resolved_token_ids": resolved,
            "ws_connection": conn_result,
            "ws_latest_events_raw": relevant_raw[:15],
            "ws_parsed_events": parsed_events[:15],
            "clob_public_snapshots": clob_snapshots,
            "summary": summary,
            "usage_hint": "For ongoing monitoring use listen_for_ws_events + parse_ws_event on the results. Call get_connection_health('market') for health.",
            "timestamp": time.time(),
        }

    @mcp.tool
    def get_websocket_status() -> dict:
        """
        Lightweight status for all channels (now enriched).

        Includes for each active channel:
        - connected, paused (new), subscribed_*, last_error, buffered count,
          reconnect_count, last_message_age, uptime_seconds (new), recent_error_count (new)

        For full details (recent_errors ring buffer, precise uptime, health fields) use
        get_connection_health("market" | "user" | "sports").

        After using the high-level Gamma+WS convenience tools (watch_market_by_slug,
        watch_markets_by_query, auto_subscribe_popular_markets), inspect "subscribed_assets"
        here (and use listen_for_ws_events / get_latest_ws_messages to consume the stream).
        """
        # Delegate to pure helper (shared with get_mcp_health_report)
        return get_all_websocket_status()

    @mcp.tool
    def get_connection_health(channel: str = "market") -> dict:
        """
        Detailed connection health for a specific channel (now significantly richer).

        Each channel's health now includes:
        - paused, uptime_seconds (connection session duration)
        - recent_errors: small ring buffer (most recent last errors with age_seconds)
        - All prior fields (reconnect_count, last_message_age, buffer stats, backoff, etc.)

        Delegates to the Managed* class implementation. Primary diagnostic for reliability + error surfacing.
        Use get_websocket_status() for a quick cross-channel overview.
        """
        # Delegate to pure helper (shared with get_mcp_health_report)
        return get_detailed_connection_health(channel)

    @mcp.tool
    async def listen_for_ws_events(
        channel: str = "market",
        timeout_seconds: float = 8.0,
        event_types: Optional[list[str]] = None,
        wait_for_event_type: Optional[str] = None,
        return_immediately: bool = False,
    ) -> list[dict]:
        """
        Powerful listener for WebSocket events (enhanced).

        Supports multiple modes of operation:
        - Normal (default): wait up to timeout_seconds for any new message (count increase).
        - return_immediately=True: return buffered messages right away without waiting.
          Useful for quick polling of anything that has already arrived.
        - wait_for_event_type="price_change": block until a message whose "event_type" (or "type")
          matches the given value is observed (or timeout). Combines well with event_types filter.

        channel: "market", "user", or "sports"
        timeout_seconds: Max wait (recommended <= 30s for stdio MCP responsiveness)
        event_types: Optional post-filter list, e.g. ["price_change", "book", "last_trade_price"]
        wait_for_event_type: Wait specifically until at least one message of this type arrives.
        return_immediately: If True, do not wait — return whatever is currently buffered.
        """
        start = time.time()

        target = None
        if channel == "market" and _market_ws:
            target = _market_ws
        elif channel == "user" and _user_ws:
            target = _user_ws
        elif channel == "sports" and _sports_ws:
            target = _sports_ws
        else:
            return [_ws_error(
                f"No active {channel} websocket",
                suggestion="Call a connect_*/watch_*/start_full_market_monitor tool for this channel first. Run get_mcp_health_report() for complete diagnostics across WS + creds + Gamma.",
                channel=channel
            )]

        initial_count = len(target.messages)

        if return_immediately:
            # Fast path — no waiting
            pass
        else:
            found_specific = False
            poll_interval = 0.2
            while time.time() - start < timeout_seconds:
                await asyncio.sleep(poll_interval)
                current_len = len(target.messages)

                if current_len > initial_count:
                    if not wait_for_event_type:
                        break
                    # Inspect the tail for the desired event type
                    recent = target.get_recent_messages(30)
                    for m in reversed(recent):
                        et = m.get("event_type") or m.get("type")
                        if et == wait_for_event_type:
                            found_specific = True
                            break
                    if found_specific:
                        break

        # Gather candidates
        msgs = target.get_recent_messages(200)

        # Apply type filter(s)
        if event_types:
            event_set = set(event_types)
            msgs = [m for m in msgs if (m.get("event_type") or m.get("type")) in event_set]

        if wait_for_event_type:
            # When a specific type was requested, we can further narrow but still return context
            # (the waiting already ensured it existed). Keep behavior consistent.
            pass

        # Only messages that arrived on/after we started (with small tolerance)
        new_msgs = [m for m in msgs if m.get("_received_at", 0) >= start - 2.0]
        return new_msgs[-80:] if new_msgs else []

    @mcp.tool
    def get_latest_ws_messages(
        channel: str = "market",
        limit: int = 20,
        event_types: Optional[list[str]] = None,
        asset_id: Optional[str] = None,
    ) -> list[dict]:
        """
        Poll the most recent buffered messages from a WebSocket channel.

        channel: "market", "user", or "sports"
        event_types: Optional filter e.g. ["price_change", "book", "last_trade_price"]
        asset_id: Optional filter to one specific token/asset
        """
        if channel == "market" and _market_ws:
            msgs = _market_ws.get_recent_messages(200)
        elif channel == "user" and _user_ws:
            msgs = _user_ws.get_recent_messages(200)
        elif channel == "sports" and _sports_ws:
            msgs = _sports_ws.get_recent_messages(200)
        else:
            return [_ws_error(
                f"No active {channel} websocket",
                suggestion="Call a connect_*/watch_*/start_full_market_monitor tool for this channel first. Run get_mcp_health_report() for complete diagnostics across WS + creds + Gamma.",
                channel=channel
            )]

        if event_types:
            event_set = set(event_types)
            msgs = [m for m in msgs if (m.get("event_type") or m.get("type")) in event_set]

        if asset_id:
            msgs = [m for m in msgs if m.get("asset_id") == asset_id or m.get("assetId") == asset_id]

        return msgs[-limit:] if msgs else []

    @mcp.tool
    async def connect_sports_websocket(leagues: Optional[list[str]] = None) -> dict:
        """
        Connect to the Sports WebSocket channel (public).
        leagues: Optional list of leagues to subscribe to (e.g. ["NBA", "NFL"]).
        """
        global _sports_ws
        if _sports_ws is None:
            _sports_ws = ManagedSportsWebSocket()
        return await _sports_ws.connect(leagues)

    # -------------------------------------------------------------------------
    # NEW high-level multi-channel orchestration + deepened Sports WS tools
    # (Production patterns matching the existing Managed* auto-reconnect/ringbuf/health)
    # -------------------------------------------------------------------------

    @mcp.tool
    async def start_full_realtime_session(
        slugs_or_queries: list[str],
        include_user_ws: bool = True,
        include_sports: bool = False,
        sports_leagues: Optional[list[str]] = None,
    ) -> dict:
        """
        ULTIMATE "LAUNCH EVERYTHING" high-level orchestration tool.
        One call wires a full real-time trading/monitoring dashboard across channels:

        - Always starts market monitoring via start_full_market_monitor (Gamma discovery + Market WS)
        - Optionally wires authenticated User WS (your orders/fills) if include_user_ws
        - Optionally wires Sports WS (in-play scores) with provided leagues if include_sports

        Returns a RICH unified status dict containing:
          - per-channel handles/status (market, user, sports)
          - recommended_listen_calls: exact copy-paste ready calls per channel (with good defaults)
          - unified_health: snapshot of get_connection_health for active channels
          - next_steps, how_to_consume, parse note (always use parse_ws_event)

        This completes the realtime story for agents wanting multi-channel (market + user + sports) in one shot.
        Matches all existing production guarantees (auto-reconnect, buffers, pause support, etc.).

        Example: start_full_realtime_session(slugs_or_queries=["nfl", "election"], include_user_ws=True, include_sports=True, sports_leagues=["NFL","NBA"])
        """
        if not slugs_or_queries or not isinstance(slugs_or_queries, (list, tuple)):
            return _ws_error(
                "slugs_or_queries required (non-empty list of slugs or natural language queries)",
                suggestion="E.g. ['bitcoin', 'will-trump-win', 'nfl'] — mix exact + queries works."
            )

        result: dict[str, Any] = {
            "status": "full_realtime_session_launched",
            "session_type": "multi_channel",
            "channels_requested": [],
            "market": None,
            "user": None,
            "sports": None,
            "recommended_listen_calls": {},
            "unified_health": {},
            "parse_note": "Always pipe raw events from any listen/get_latest through realtime_helpers.parse_ws_event() for clean normalized + specific fields.",
            "timestamp": time.time(),
        }

        # 1. Market channel (core) — delegate to the flagship
        try:
            market_res = await start_full_market_monitor(
                slugs_or_queries=slugs_or_queries,
                max_per_query=5,
            )
            result["market"] = market_res
            result["channels_requested"].append("market")
            result["recommended_listen_calls"]["market"] = (
                "listen_for_ws_events(channel='market', timeout_seconds=6.0, event_types=['price_change', 'last_trade_price', 'trade', 'book'], wait_for_event_type='price_change')  # or get_latest_ws_messages(channel='market', limit=25, event_types=[...])"
            )
        except Exception as me:
            result["market"] = _ws_error(f"Market wiring failed: {me}", suggestion="Call get_mcp_health_report() and retry start_full_market_monitor directly.")

        # 2. User channel (optional, auth required)
        if include_user_ws:
            result["channels_requested"].append("user")
            try:
                global _user_ws
                if _user_ws is None:
                    _user_ws = ManagedUserWebSocket()
                user_res = await _user_ws.connect()
                result["user"] = user_res
                result["recommended_listen_calls"]["user"] = (
                    "listen_for_ws_events(channel='user', timeout_seconds=9.0, event_types=['trade', 'fill', 'order'], wait_for_event_type='trade')  # your live account activity"
                )
            except Exception as ue:
                result["user"] = _ws_error(f"User WS connect failed (check CLOB creds via check_clob_auth): {ue}")

        # 3. Sports channel (optional, for in-play)
        if include_sports:
            result["channels_requested"].append("sports")
            leagues = sports_leagues or ["NBA", "NFL", "MLB"]
            try:
                global _sports_ws
                if _sports_ws is None:
                    _sports_ws = ManagedSportsWebSocket()
                sports_res = await _sports_ws.connect(leagues)
                result["sports"] = sports_res
                result["recommended_listen_calls"]["sports"] = (
                    "listen_for_ws_events(channel='sports', timeout_seconds=15.0)  # or get_latest_ws_messages(channel='sports', limit=30); use get_sports_realtime_snapshot() for parsed scores + follow-ups"
                )
            except Exception as se:
                result["sports"] = _ws_error(f"Sports WS failed: {se}")

        # Unified health snapshot (one object)
        try:
            for ch in result.get("channels_requested", []):
                h = get_detailed_connection_health(ch)
                if isinstance(h, dict) and "error" not in h:
                    result["unified_health"][ch] = {
                        "connected": h.get("connected"),
                        "paused": h.get("paused"),
                        "buffered_messages": h.get("buffered_messages"),
                        "last_message_age_seconds": h.get("last_message_age_seconds"),
                        "reconnect_count": h.get("reconnect_count"),
                        "uptime_seconds": h.get("uptime_seconds"),
                    }
                else:
                    result["unified_health"][ch] = {"error": "health unavailable"}
        except Exception:
            result["unified_health"] = {"note": "health snapshot partial"}

        result["how_to_consume"] = "Use the per-channel recommended_listen_calls. Combine with get_realtime_helper_patterns() + get_realtime_sports_patterns() + parse_ws_event. For one-shot state: get_sports_realtime_snapshot() + get_realtime_market_snapshot()."
        result["note"] = "All channels use the production Managed* classes (auto-reconnect, dedup ring buffers, pause/resume, rich health). Connections persist for MCP lifetime. Call get_websocket_status() + get_connection_health per channel for live diagnostics."

        return result

    @mcp.tool
    async def auto_subscribe_sports_popular() -> dict:
        """
        High-level sports convenience: instantly subscribes the Sports WS to a curated set of
        popular/high-interest leagues for in-play coverage (NBA, NFL, MLB, EPL, NHL, etc.).

        Delegates to connect_sports_websocket under the hood (full ManagedSports lifecycle).
        Returns the connect result augmented with the chosen leagues and usage hints.

        Perfect companion to start_full_realtime_session(include_sports=True) when you
        don't want to pick leagues manually. Complements market popular auto-sub.
        """
        popular_leagues = ["NBA", "NFL", "MLB", "EPL", "NHL", "MLS", "UFC"]
        global _sports_ws
        if _sports_ws is None:
            _sports_ws = ManagedSportsWebSocket()

        res = await _sports_ws.connect(popular_leagues)
        if isinstance(res, dict):
            res["leagues_subscribed"] = popular_leagues
            res["note"] = "Auto-subscribed popular leagues for broad in-play sports coverage. Use get_sports_realtime_snapshot() or listen_for_ws_events(channel='sports') next. Pair with market monitors for correlated prediction markets."
            res["recommended_next"] = "get_sports_realtime_snapshot() or listen_for_ws_events(channel='sports', timeout_seconds=12)"
        return res

    @mcp.tool
    async def watch_sports_by_leagues(leagues: list[str]) -> dict:
        """
        High-level: subscribe specific leagues to the Sports WS (robust managed path).

        leagues: e.g. ["NFL", "NBA", "EPL"]
        Delegates to connect_sports_websocket (handles already-running case + reconnect tracking).

        Returns augmented result with subscribed_leagues and direct listen guidance.
        Use standalone or via start_full_realtime_session.
        """
        if not leagues:
            return _ws_error("leagues list required (e.g. ['NFL', 'NBA'])", suggestion="Call auto_subscribe_sports_popular() for a good default set, or pass specific leagues.")

        global _sports_ws
        if _sports_ws is None:
            _sports_ws = ManagedSportsWebSocket()

        res = await _sports_ws.connect(leagues)
        if isinstance(res, dict):
            res["requested_leagues"] = leagues
            res["subscribed_leagues"] = _sports_ws.get_subscribed_leagues() if _sports_ws else leagues
            res["recommended_consumption"] = "listen_for_ws_events(channel='sports', timeout_seconds=15.0) or get_latest_ws_messages(channel='sports'); follow score events with get_sports_realtime_snapshot() + search_markets for related poly markets."
        return res

    @mcp.tool
    async def get_sports_realtime_snapshot() -> dict:
        """
        HIGH-LEVEL SPORTS SNAPSHOT: Ensures Sports WS is live, pulls recent buffered events,
        parses them cleanly via parse_ws_event (sports section), and returns a compact
        "in-play state" view (recent scores, active leagues, status changes).

        Perfect one-call companion for agents monitoring live sports + correlated prediction markets.
        Safe to call frequently; reuses/creates the ManagedSportsWebSocket.

        Also surfaces recommended follow-up calls (market snapshots on discovered games etc.).
        """
        global _sports_ws
        if _sports_ws is None:
            _sports_ws = ManagedSportsWebSocket()
            # Best-effort connect to all for snapshot utility
            await _sports_ws.connect()

        # Give a moment for any fresh data if just connected
        await asyncio.sleep(0.4)

        from . import realtime_helpers
        raw_recent = get_latest_ws_messages(channel="sports", limit=50)
        parsed = []
        leagues_seen = set()
        for m in raw_recent:
            try:
                p = realtime_helpers.parse_ws_event(m)
                parsed.append(p)
                lg = (p.get("specific") or {}).get("league") or (p.get("normalized") or {}).get("league")
                if lg:
                    leagues_seen.add(lg)
            except Exception:
                parsed.append({"event_type": m.get("event_type") or m.get("type"), "raw": m})

        health = get_detailed_connection_health("sports") if _sports_ws and _sports_ws.running else {"connected": False}

        return {
            "status": "ok",
            "sports_ws_connected": bool(_sports_ws and _sports_ws.running),
            "subscribed_leagues": _sports_ws.get_subscribed_leagues() if _sports_ws else [],
            "recent_raw_count": len(raw_recent),
            "parsed_sports_events": parsed[:20],
            "leagues_in_buffer": sorted(list(leagues_seen)),
            "health": health,
            "recommended_next": [
                "listen_for_ws_events(channel='sports', timeout_seconds=12)",
                "If scores changed: search_markets(query='nfl live' or league) + get_realtime_market_snapshot on related tokens",
                "get_realtime_sports_patterns() for on_score_change_then_check_markets etc.",
            ],
            "usage_hint": "Pipe every sports event through realtime_helpers.parse_ws_event(). Combine with start_full_realtime_session(include_sports=True) for full multi-channel dashboards.",
            "timestamp": time.time(),
        }

    @mcp.tool
    def get_realtime_sports_patterns() -> dict:
        """
        Surfaces the dedicated sports + multi-channel event-driven patterns from realtime_helpers.
        Includes on_score_change_then_check_markets, on_sports_update_then_analyze_polymarkets,
        multi_league_sports_watcher (plus usage guidance).

        These are the copy-paste recipes for reacting to live sports WS data and bridging to
        prediction market actions. Complements (and is referenced by) get_ws_event_driven_patterns().
        """
        from . import realtime_helpers
        return realtime_helpers.get_realtime_sports_patterns()

    @mcp.tool
    async def disconnect_sports_websocket() -> dict:
        """Disconnect from the Sports WebSocket."""
        global _sports_ws
        if _sports_ws:
            await _sports_ws.disconnect()
            _sports_ws = None
            return {"status": "disconnected"}
        return {"status": "not_connected"}
