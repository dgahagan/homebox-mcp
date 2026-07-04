#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["mcp>=1.10", "httpx>=0.27", "pillow>=10", "pillow-heif>=0.16"]
# ///
"""Compatibility shim — the server module is homebox_mcp.py.

Existing MCP registrations that point at server.py (via `uv run --script
server.py`) keep working through this shim. New setups should prefer
`uvx homebox-mcp` (PyPI) or point at homebox_mcp.py directly.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from homebox_mcp import main  # noqa: E402

if __name__ == "__main__":
    main()
