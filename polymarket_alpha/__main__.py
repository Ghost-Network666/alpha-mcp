"""
Entry point: python -m polymarket_alpha

Designed for excellent first-run experience when launched by Hermes or OpenClaw.
"""

from .config import log_startup_status
from .server import mcp


def main():
    log_startup_status()
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
