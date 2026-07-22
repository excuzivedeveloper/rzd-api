from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_import_rzd_api_does_not_import_mcp_stack() -> None:
    code = """
import sys
import rzd_api

for name in ("mcp", "uvicorn", "starlette", "mcp_server"):
    assert name not in sys.modules, name
assert hasattr(rzd_api, "RzdClient")
"""
    result = subprocess.run([sys.executable, "-c", code], text=True)
    assert result.returncode == 0


def test_live_smoke_disables_transport_retries() -> None:
    script = Path("scripts/smoke_live.py").read_text(encoding="utf-8")
    assert "Config(connect_timeout=5, read_timeout=20, retry_total=0)" in script
    assert "HTTP ATTEMPT LIMIT: {http_attempt_limit}" in script
