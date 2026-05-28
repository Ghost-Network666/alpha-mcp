"""
Main FastMCP server assembly for polymarket-alpha.

All tools are registered and visible (auth requirements clearly documented in each tool).
"""

from fastmcp import FastMCP

from .config import get_auth_status, log_startup_status
from .meta import register_meta_tools

mcp = FastMCP(
    name="polymarket-alpha",
    instructions=(
        "Polymarket is now fully native inside your agent.\n\n"
        "PRIMARY RULE: For any official Polymarket documentation, ALWAYS call "
        "get_polymarket_llms_txt() first (it fetches the live https://docs.polymarket.com/llms.txt).\n\n"
        "Then use get_gamma_docs() and get_clob_docs() for MCP-specific guidance.\n\n"
        "For SDK choice (unified `polymarket-client` vs current py-clob-client-v2): call get_unified_sdk_guidance().\n\n"
        "Start every session with get_mcp_health_report() (the comprehensive self-diagnostic — ALWAYS call this FIRST after startup) + get_capabilities() and check_clob_auth() (if trading)."
    ),
)

# Register mandatory meta tools
register_meta_tools(mcp)

# Register all tool groups (all tools visible even in read-only)
from .gamma import register_gamma_tools
from .clob_public import register_clob_public_tools

from .analysis import register_analysis_tools
from .simulation import register_simulation_tools

from .clob_authenticated import register_authenticated_tools
from .gasless import register_gasless_tools
from .websocket import register_websocket_tools

register_gamma_tools(mcp)
register_clob_public_tools(mcp)
register_analysis_tools(mcp)
register_simulation_tools(mcp)  # PAPER / SIMULATION ONLY layer (in-memory sessions + impact + WS replay). All tools explicitly labeled. Safe for strategy development & harness testing. See simulation.py + get_capabilities().
register_authenticated_tools(mcp)  # Always registered, clearly documents auth requirements in docstrings
register_gasless_tools(mcp)  # Gasless relayer tools (only functional when RELAYER + PK + signature_type provided)
register_websocket_tools(mcp)  # Full real-time: Market/User/Sports managed WS + ALL high-level (start_full_market_monitor, watch_*, start_realtime_market_watcher, update_*, pause/resume) + consumption (listen + get_latest snapshot) + status/health + get_realtime_helper_patterns (incl. parse_ws_event) + get_ws_event_driven_patterns (event-driven trading recipes) wired from realtime_helpers + get_realtime_trading_guide

# Additional tool groups will be added in full implementation
# from .clob_public import register_clob_public_tools
# from .analysis import register_analysis_tools
# etc.

# All tools will be registered so they are always visible.
# Auth requirements are documented inside each tool's docstring.

if __name__ == "__main__":
    log_startup_status()
    mcp.run(transport="stdio")
