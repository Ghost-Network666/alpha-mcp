"""
polymarket-alpha

Agent-first MCP server for Polymarket.

Designed so Hermes, OpenClaw, Claude, and other agents get a complete,
self-documenting experience with zero guessing.

Easy setup is a first-class feature.
"""

from .config import (
    get_auth_status,
    log_startup_status,
    get_raw_relay_client,
)

__version__ = "0.3.1"

__all__ = [
    "get_auth_status",
    "log_startup_status",
    "get_raw_relay_client",
]
