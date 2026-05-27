"""
Dedicated easy setup experience for agents.
"""

from .config import print_setup_guide


def register_setup_tools(mcp):
    @mcp.tool
    def polymarket_alpha_setup_guide() -> str:
        """
        Returns complete, platform-specific setup instructions so agents can self-configure.
        Call this tool if you are in read-only mode and want to enable trading.
        """
        import io
        from contextlib import redirect_stdout

        buf = io.StringIO()
        with redirect_stdout(buf):
            print_setup_guide()
        return buf.getvalue()
