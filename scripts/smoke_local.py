from __future__ import annotations

from datetime import date, timedelta
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rzd_api import Config, RzdClient, Station, TrainRoute


class FakeApi:
    def __init__(self) -> None:
        self.closed = False

    def find_stations(self, **_: Any) -> list[Station]:
        return [Station("MOSCOW", "2000000", {}), Station("SAINT PETERSBURG", "2004000", {})]

    def get_train_routes(self, **_: Any) -> list[TrainRoute]:
        return [
            TrainRoute(
                number="001A",
                display_number="001A",
                origin_name="MOSCOW",
                destination_name="SAINT PETERSBURG",
                departure_time="2099-01-01T10:00:00",
                arrival_time="2099-01-01T18:00:00",
                min_price=1000.0,
                available_places=3,
                car_groups=[],
                raw={"source": "local-smoke"},
            )
        ]

    def close(self) -> None:
        self.closed = True


def main() -> int:
    config = Config(connect_timeout=1, read_timeout=1)
    fake_api = FakeApi()
    with RzdClient(config, _api=fake_api) as client:  # type: ignore[arg-type]
        departure = date.today() + timedelta(days=14)
        routes = client.search_tickets("2000000", "2004000", departure)
        stations = client.find_stations("Mo")

    assert fake_api.closed is True
    assert isinstance(routes, list) and routes[0].number == "001A"
    assert stations[0].code == "2000000"
    print("LOCAL SMOKE: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
