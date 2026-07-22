from __future__ import annotations

import argparse
import importlib.util
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE_SUFFIXES = {".py", ".toml", ".md", ".yml", ".yaml", ".txt"}
MCP_PACKAGES = ("mcp", "uvicorn", "starlette")
SCAN_ROOTS = (
    ROOT / "rzd_api",
    ROOT / "mcp_server",
    ROOT / "pyproject.toml",
    ROOT / "constraints.txt",
    ROOT / "Dockerfile",
    ROOT / "Makefile",
    ROOT / ".github",
)


def scan_text(patterns: tuple[str, ...]) -> bool:
    candidates: list[Path] = []
    for root in SCAN_ROOTS:
        if root.is_file():
            candidates.append(root)
        elif root.is_dir():
            candidates.extend(path for path in root.rglob("*") if path.is_file())

    for path in candidates:
        if path.suffix not in SOURCE_SUFFIXES:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if any(pattern in text for pattern in patterns):
            return True
    return False


def run_command(args: list[str]) -> bool:
    result = subprocess.run(args, cwd=ROOT, text=True)
    return result.returncode == 0


def package_path() -> str:
    import rzd_api

    return str(Path(rzd_api.__file__).resolve())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-tests", action="store_true", help="Run offline pytest suite.")
    args = parser.parse_args()

    sys.path.insert(0, str(ROOT))

    import_pass = False
    local_smoke_pass = False
    unit_tests_pass = None
    installed_path = ""
    try:
        installed_path = package_path()
        import_pass = True
    except Exception as exc:
        print(f"IMPORT ERROR: {exc}")

    verify_false_found = scan_text(("verify=False", "verify = False", ".verify = False"))
    tls_warnings_suppressed = scan_text(("disable_warnings", "InsecureRequestWarning"))
    live_enabled = os.getenv("RZD_LIVE_TEST") == "1"
    mcp_installed = any(importlib.util.find_spec(name) is not None for name in MCP_PACKAGES)

    if import_pass:
        local_smoke_pass = run_command([sys.executable, "scripts/smoke_local.py"])
    if args.run_tests:
        unit_tests_pass = run_command(
            [
                sys.executable,
                "-m",
                "pytest",
                "tests/",
                "-m",
                "not integration and not mcp",
                "-q",
                "-p",
                "no:cacheprovider",
            ]
        )

    tls_verification_pass = not verify_false_found and not tls_warnings_suppressed
    ready_for_live = import_pass and local_smoke_pass and tls_verification_pass and not live_enabled
    critical_ok = import_pass and local_smoke_pass and tls_verification_pass
    if unit_tests_pass is not None:
        critical_ok = critical_ok and unit_tests_pass

    print(f"PYTHON: {sys.version.split()[0]}")
    print(f"PACKAGE PATH: {installed_path or 'unavailable'}")
    print(f"INSTALL: {'PASS' if import_pass else 'FAIL'}")
    print(f"IMPORT: {'PASS' if import_pass else 'FAIL'}")
    print(
        "UNIT TESTS: "
        + ("NOT RUN" if unit_tests_pass is None else "PASS" if unit_tests_pass else "FAIL")
    )
    print(f"LOCAL SMOKE: {'PASS' if local_smoke_pass else 'FAIL'}")
    print(f"TLS VERIFICATION: {'PASS' if tls_verification_pass else 'FAIL'}")
    print(f"VERIFY_FALSE FOUND: {'YES' if verify_false_found else 'NO'}")
    print(f"TLS WARNINGS SUPPRESSED: {'YES' if tls_warnings_suppressed else 'NO'}")
    print(f"MCP INSTALLED: {'YES' if mcp_installed else 'NO'}")
    print(f"LIVE TEST ENABLED: {'YES' if live_enabled else 'NO'}")
    print(f"READY FOR LIVE TEST: {'YES' if ready_for_live else 'NO'}")

    return 0 if critical_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
