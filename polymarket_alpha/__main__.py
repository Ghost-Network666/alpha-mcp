"""
Entry point: python -m polymarket_alpha  OR  polymarket-alpha (after pip install)

Designed for excellent first-run experience when launched by Hermes or OpenClaw.
ALWAYS prints the critical "call get_mcp_health_report first" banner on startup.
"""

import argparse
import sys

from . import __version__
from .config import log_startup_status
from .server import mcp


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="polymarket-alpha",
        description="polymarket-alpha MCP — full Polymarket native experience for agents (Hermes, OpenClaw, etc.). Zero static docs; everything via tools.",
        epilog="First actions after launch: ALWAYS call get_mcp_health_report() then get_capabilities(). Trading: also call check_clob_auth(include_raw=true). See polymarket_alpha_setup_guide() for credentials."
    )
    parser.add_argument("--version", action="version", version=f"polymarket-alpha {__version__}")
    parser.add_argument("--help-banner", action="store_true", help="Print the startup banner (with health-report-first guidance) and exit.")
    args = parser.parse_args()

    # Always log the authoritative banner (covers the required "call get_mcp_health_report first")
    log_startup_status()

    if args.help_banner:
        print("See above banner for immediate next steps. Run the MCP with no args for stdio server (normal agent harness use).")
        print(f"Version: {__version__}")
        sys.exit(0)

    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
