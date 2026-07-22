# Server Testing

Use a disposable Linux user and a clean virtual environment. Do not run these
checks on a WayFound production host, do not copy WayFound `.env` files, and do
not grant access to production databases, Docker socket, or unrelated SSH keys.

## Offline Preparation

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
python -m pip install pytest
python scripts/smoke_local.py
python scripts/verify_fork.py --run-tests
```

Expected properties:

- base install imports `rzd_api`;
- `mcp`, `uvicorn`, and `starlette` are not needed for base library use;
- `verify=False` is absent;
- `InsecureRequestWarning` is not globally suppressed;
- unit tests run without live RZD network access and without MCP tests.

## Optional Live Smoke

Run live smoke only once, manually, after the offline checks pass:

```sh
export RZD_LIVE_TEST=1
python scripts/smoke_live.py
unset RZD_LIVE_TEST
```

Expected library call count: 2. The script disables transport retries with
`retry_total=0`, so the HTTP attempt limit is 2: one station lookup and one
direct train search using public station codes. It does not book, buy,
authenticate, or use passenger personal data.

## Restrictions

- Do not start `rzd-mcp-server`.
- Do not install `.[mcp]` for base library validation.
- Do not publish port `8000`.
- Do not disable TLS verification.
- Do not set `RZD_CA_BUNDLE` to a secret-bearing file.
- Do not run repeated live smoke loops.
