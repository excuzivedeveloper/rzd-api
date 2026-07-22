from __future__ import annotations

import os
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rzd_api import Config, RzdClient, RzdError


def main() -> int:
    http_attempt_limit = 2
    if os.getenv("RZD_LIVE_TEST") != "1":
        print("LIVE SMOKE: SKIPPED (set RZD_LIVE_TEST=1 explicitly)")
        print(f"HTTP ATTEMPT LIMIT: {http_attempt_limit}")
        return 2

    departure = date.today() + timedelta(days=14)
    config = Config(connect_timeout=5, read_timeout=20, retry_total=0)
    library_call_count = 0

    try:
        with RzdClient(config) as client:
            stations = client.find_stations("Москва")
            library_call_count += 1
            routes = client.search_tickets(
                "2000000",
                "2004000",
                departure,
                only_with_seats=False,
            )
            library_call_count += 1
    except RzdError as exc:
        print(f"LIVE SMOKE: FAIL ({exc})")
        print(f"LIBRARY CALL COUNT: {library_call_count}")
        print(f"HTTP ATTEMPT LIMIT: {http_attempt_limit}")
        return 1

    if not stations or not isinstance(routes, list):
        print("LIVE SMOKE: FAIL (unexpected empty response)")
        print(f"LIBRARY CALL COUNT: {library_call_count}")
        print(f"HTTP ATTEMPT LIMIT: {http_attempt_limit}")
        return 1

    print("LIVE SMOKE: PASS")
    print(f"LIBRARY CALL COUNT: {library_call_count}")
    print(f"HTTP ATTEMPT LIMIT: {http_attempt_limit}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
