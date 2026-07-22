# Fork Validation Report

## Final Verdict

READY FOR SERVER TEST.

## Current Commit

Base upstream commit: `0f2490fa75183a7494043e2f9ff564506b5989cb`.

## Changed Files

- `rzd_api/config.py`
- `rzd_api/query.py`
- `pyproject.toml`
- `tests/test_query.py`
- `tests/test_config_models.py`
- `tests/test_base_install.py`
- `tests/test_mcp_protocol.py`
- `tests/test_mcp_server.py`
- `tests/integration/test_mcp_container.py`
- `scripts/smoke_local.py`
- `scripts/smoke_live.py`
- `scripts/verify_fork.py`
- `docs/SERVER_TESTING.md`
- `README.md`
- `FORK_VALIDATION_REPORT.md`

## TLS

PASS. Requests uses normal certificate verification by default. A custom CA
bundle can be supplied with `RZD_CA_BUNDLE` or `Config(ca_bundle=...)`.

## verify=False

NOT FOUND in the intended source after the fork patch.

## TLS Warning Suppression

NOT FOUND in the intended source after the fork patch.

## Base Install

Prepared. Validate with:

```sh
python -m pip install -e .
python -m pip install pytest
python scripts/verify_fork.py --run-tests
```

## MCP In Base Install

NO. MCP dependencies remain in the optional `mcp` and `dev` extras.

## Unit Tests

PASS.

Count: `90 passed, 3 skipped, 2 deselected`.

Command:

```sh
python scripts/verify_fork.py --run-tests
```

## Local Smoke Test

PASS: `python scripts/smoke_local.py`.

## Live Tests

PREPARED / NOT RUN. Live smoke requires `RZD_LIVE_TEST=1`.

Live smoke sets `retry_total=0`; expected library call count is 2 and HTTP
attempt limit is 2.

## Public API

Primary public API for WayFound-style integration:

- `Config`
- `RzdClient`
- `RzdClient.search_tickets`
- `RzdClient.find_stations`
- `RzdClient.resolve_station_code`
- `RzdClient.get_carriages`
- `RzdClient.get_train_availability`
- `RzdClient.get_minimal_prices`
- `RzdClient.get_car_scheme`
- `RzdClient.get_car_images`
- `RzdClient.get_route_stations`
- response dataclasses with `to_dict()`

## Supported Operations

- station lookup;
- direct train search;
- train availability calendar;
- minimal prices;
- carriage and seat availability metadata;
- route stations;
- carriage scheme and image metadata.

## API Limitations

- transfer routes are not supported;
- transport-type filtering is not supported by current pricing endpoint;
- live behavior depends on the unofficial `ticket.rzd.ru` API contract;
- no booking, purchase, authorization, or passenger personal-data workflow is
  implemented.

## Known Risks

- external RZD endpoints can change without notice;
- live smoke makes network requests to `ticket.rzd.ru`;
- live smoke disables retries, so transient timeout, TLS, connection, HTTP 429,
  and HTTP 5xx failures should be retried manually only after review;
- custom CA bundle paths must be managed by deployment configuration.
- Windows needs the base conditional dependency `tzdata` for
  `ZoneInfo("Europe/Moscow")`.

## VPS Checks

- clean base install;
- offline unit tests;
- local smoke;
- one explicit live smoke;
- confirm TLS connection succeeds without `verify=False`;
- record library call count, HTTP attempt limit, and the tested commit SHA.

## WayFound Readiness

AFTER LIVE TEST. Do not change WayFound until a separate server confirms install,
import, unit tests, TLS connection, one public RZD request, and parsing of the
real response.
