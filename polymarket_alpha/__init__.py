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

__version__ = "0.5.0"

# Public surface for power users who `import polymarket_alpha` directly
# (beyond the MCP server). Key helpers, shapes, and registers for advanced composition.
from .realtime_helpers import (
    parse_ws_event,
    MONITOR_STATUS_SHAPE,
    get_ws_event_types_help,
    get_event_driven_trading_patterns,
    get_copy_paste_realtime_loops,
    get_realtime_story_summary,
)

# Register functions (advanced: for building custom FastMCP servers or testing)
from .meta import register_meta_tools
from .gamma import register_gamma_tools
from .clob_public import register_clob_public_tools
from .analysis import register_analysis_tools
from .clob_authenticated import register_authenticated_tools
from .gasless import register_gasless_tools
from .websocket import register_websocket_tools

__all__ = [
    "__version__",
    # Core status / helpers
    "get_auth_status",
    "log_startup_status",
    "get_raw_relay_client",
    # Realtime power helpers (critical for agent WS consumption loops)
    "parse_ws_event",
    "MONITOR_STATUS_SHAPE",
    "get_ws_event_types_help",
    "get_event_driven_trading_patterns",
    "get_copy_paste_realtime_loops",
    "get_realtime_story_summary",
    # Advanced register hooks (for custom MCP composition / harness authors)
    "register_meta_tools",
    "register_gamma_tools",
    "register_clob_public_tools",
    "register_analysis_tools",
    "register_authenticated_tools",
    "register_gasless_tools",
    "register_websocket_tools",
]
