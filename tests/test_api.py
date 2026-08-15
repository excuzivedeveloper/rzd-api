from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from rzd_api.api import RzdApi
from rzd_api.config import Config
from rzd_api.exceptions import RzdSchemaError
from rzd_api.models import TransferProvider, TransferSearchRequest


class FakeTransport:
    def __init__(self, payloads: list[Any]) -> None:
        self.payloads = list(payloads)
        self.calls: list[dict[str, Any]] = []
        self.closed = False

    def request_json(self, method: str, url: str, **kwargs: Any) -> Any:
        self.calls.append({"method": method, "url": url, **kwargs})
        return self.payloads.pop(0)

    def close(self) -> None:
        self.closed = True


def make_api(*payloads: Any) -> tuple[RzdApi, FakeTransport]:
    transport = FakeTransport(list(payloads))
    return RzdApi(Config(), transport=transport), transport  # type: ignore[arg-type]


def load_fixture(name: str) -> Any:
    return json.loads((Path(__file__).parent / "fixtures" / name).read_text(encoding="utf-8"))


def test_train_routes_builds_current_request_and_parses_models() -> None:
    api, transport = make_api(
        {
            "data": {
                "trains": [
                    {
                        "TrainNumber": "001А",
                        "DisplayTrainNumber": "001А",
                        "OriginStationName": "МОСКВА",
                        "DestinationStationName": "С-ПЕТЕРБУРГ",
                        "DepartureDateTime": "2099-04-03T22:30:00",
                        "ArrivalDateTime": "2099-04-04T06:30:00",
                        "CarGroups": [
                            {
                                "CarType": "Compartment",
                                "MinPrice": 4200.5,
                                "TotalPlaceQuantity": 4,
                            },
                            {
                                "CarType": "ReservedSeat",
                                "MinPrice": "2500",
                                "LowerPlaceQuantity": 2,
                                "UpperPlaceQuantity": 3,
                            },
                        ],
                    }
                ]
            }
        }
    )

    routes = api.get_train_routes(
        origin="2000000",
        destination="2004000",
        departure_date="2099-04-03T00:00:00",
        adults=2,
        children=1,
    )

    assert routes[0].number == "001А"
    assert routes[0].available_places == 9
    assert routes[0].min_price == 2500.0
    assert routes[0].car_groups[0].car_type == "Compartment"
    call = transport.calls[0]
    assert call["method"] == "GET"
    assert call["url"].endswith("/railway-service/prices/train-pricing")
    assert call["params"]["adultPassengersQuantity"] == 2
    assert call["params"]["childrenPassengersQuantity"] == 1


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"data": {}},
        {"Trains": ["not-an-object"]},
        {"Trains": [{"OriginStationName": "missing number"}]},
        {"Trains": [{"TrainNumber": "1", "CarGroups": {}}]},
    ],
)
def test_train_routes_rejects_schema_drift(payload: Any) -> None:
    api, _ = make_api(payload)
    with pytest.raises(RzdSchemaError):
        api.get_train_routes(
            origin="1", destination="2", departure_date="2099-01-01T00:00:00", adults=1, children=0
        )


def test_train_route_marks_missing_car_groups_as_unknown_availability() -> None:
    api, _ = make_api({"Trains": [{"TrainNumber": "001А"}]})
    route = api.get_train_routes(
        origin="1", destination="2", departure_date="2099-01-01T00:00:00", adults=1, children=0
    )[0]
    assert route.available_places is None
    assert route.car_groups == []


def test_train_route_marks_unknown_availability() -> None:
    api, _ = make_api({"Trains": [{"TrainNumber": "001А", "CarGroups": [{"CarType": "Unknown"}]}]})
    route = api.get_train_routes(
        origin="1", destination="2", departure_date="2099-01-01T00:00:00", adults=1, children=0
    )[0]
    assert route.available_places is None


def test_find_stations_preserves_synonyms_and_grouped_nodes() -> None:
    api, _ = make_api(
        [
            {"group": "rail", "items": [{"n": "САНКТ-ПЕТЕРБУРГ", "c": "2004000"}]},
            {"n": "САНКТ-ПЕТЕРБУРГ", "c": "2004000"},
        ]
    )
    stations = api.find_stations(query="Питер", transport_type="rail", group_results=True)
    assert [(item.name, item.code) for item in stations] == [("САНКТ-ПЕТЕРБУРГ", "2004000")]


def test_find_stations_parses_current_category_response() -> None:
    api, _ = make_api(
        {
            "city": [
                {
                    "nodeId": "city-id",
                    "cityId": "city-id",
                    "expressCode": "2000000",
                    "name": "Москва",
                    "timezone": "Europe/Moscow",
                    "Codes": {"Railway": "2000000", "Cbdpr": "101"},
                    "nodeType": "city",
                    "transportType": "city",
                    "region": "Российская Федерация",
                    "country": "Россия",
                }
            ],
            "train": [
                {
                    "nodeId": "station-id",
                    "expressCode": "2000002",
                    "name": "Москва Ярославская",
                    "nodeType": "station",
                    "transportType": "train",
                    "region": "Москва, Российская Федерация",
                }
            ],
        }
    )
    stations = api.find_stations(query="Москва", transport_type="rail", group_results=True)
    assert [item.code for item in stations] == ["2000000", "2000002"]
    assert stations[0].node_id == "city-id"
    assert stations[0].city_id == "city-id"
    assert stations[0].timezone == "Europe/Moscow"
    assert stations[0].codes["Railway"] == "2000000"
    assert stations[0].codes["Cbdpr"] == "101"
    assert stations[0].country == "Россия"
    assert stations[1].transport_type == "train"


def test_transfer_search_builds_contract_and_parses_reference_fixture() -> None:
    api, transport = make_api(load_fixture("transfer-search.json"))
    result = api.search_transfers(
        TransferSearchRequest(
            origin="5a323c29340c7441a0a556bb",
            destination="5a13baf9340c745ca1e80436",
            departure_date="2026-09-11",
            providers=(TransferProvider.RAILS, TransferProvider.SUBURBAN),
        )
    )

    assert result.request_id == "prod:6d:20260730173315:3423006"
    assert len(result.routes) == 2
    first = result.routes[0]
    assert len(first.legs) == 2
    assert first.price == 5534.1
    assert first.max_price == 18562.3
    assert first.incomplete is False
    assert first.ttl_min_expire_time == "2026-07-30T18:21:42Z"
    assert first.departure_time == "2026-09-11T01:00:00+03:00"
    assert first.arrival_time == "2026-09-11T18:58:00+03:00"
    assert first.origin and first.origin.city_name == "Москва"
    assert first.destination and first.destination.name == "Исакогорка"
    assert first.legs[0].provider_type == "b2brails"
    assert first.legs[0].booking_system == "Express3"
    assert first.legs[0].transport_types == ["Train"]
    assert first.legs[0].trips[0].number == "002Э"
    assert first.legs[0].trips[0].transport_type == "Train"
    assert first.legs[0].trips[0].price == 1976.4
    assert first.legs[0].trips[0].distance_km == 280
    assert first.legs[0].trips[0].train_pricing[0].number == "002Э"
    product = first.legs[0].trips[0].products[0]
    assert product.price == 2682.9
    assert product.free_places == 77
    assert product.product_type == "Compartment"
    assert "2Э:ФПК" in product.service_classes
    assert product.carriers == ["ФПК"]
    assert product.ttl_expire_time == "2026-07-30T18:21:42Z"
    transfer = first.transfers[0]
    assert transfer.origin and transfer.origin.name == "Ярославль (Московский вокзал)"
    assert transfer.destination and transfer.destination.name == "Ярославль-Главный"
    assert transfer.price == 500.0
    assert transfer.duration_minutes == 13
    assert transfer.duration_seconds == 807

    call = transport.calls[0]
    assert call["method"] == "POST"
    assert call["url"].endswith("/apib2b/mmp/onewayRoutesStream/v2")
    assert call["headers"]["Cookie"] == "LANG_SITE=ru"
    body = call["json_body"]
    assert body["start_location"]["city"]["key"] == "5a323c29340c7441a0a556bb"
    assert body["finish_location"]["city"]["key"] == "5a13baf9340c745ca1e80436"
    assert body["start_datetime_range"]["from"] == "2026-09-11T00:00:00"
    assert body["start_datetime_range"]["to"] == "2026-09-11T23:59:59"
    assert body["min_trips_in_leg"] == 1
    assert body["max_trips_in_leg"] == 3
    assert body["max_results"] == 3
    assert body["system_params"]["detailed_location"] is True
    assert body["filters"][0]["exact_filter"]["param_values"] == ["b2brails", "cbdpr"]


def test_transfer_search_empty_incomplete_and_malformed_cases() -> None:
    api, _ = make_api({})
    assert api.search_transfers(TransferSearchRequest("a", "b", "2099-01-01")).routes == []

    payload = {"multi_modal_routes": [{"routes": [{"incomplete": True}], "transfers": []}]}
    api, _ = make_api(payload)
    result = api.search_transfers(TransferSearchRequest("a", "b", "2099-01-01"))
    assert result.routes[0].incomplete is True

    api, _ = make_api({"multi_modal_routes": {}})
    with pytest.raises(RzdSchemaError):
        api.search_transfers(TransferSearchRequest("a", "b", "2099-01-01"))


@pytest.mark.parametrize(
    "kwargs",
    [
        {"min_trips": 0},
        {"min_trips": 3, "max_trips": 2},
        {"max_results": 0},
        {"providers": ()},
    ],
)
def test_transfer_request_validation(kwargs: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        TransferSearchRequest("a", "b", "2099-01-01", **kwargs)


def test_find_stations_accepts_empty_and_rejects_unknown_nonempty_schema() -> None:
    api, _ = make_api([])
    assert api.find_stations(query="Нет", transport_type="rail", group_results=True) == []

    api, _ = make_api({"suggestions": []})
    assert api.find_stations(query="Нет", transport_type="rail", group_results=True) == []

    api, _ = make_api([{"group": "rail", "items": []}])
    assert api.find_stations(query="Нет", transport_type="rail", group_results=True) == []

    api, _ = make_api([{"unknown": "value"}])
    with pytest.raises(RzdSchemaError, match="unsupported station"):
        api.find_stations(query="Нет", transport_type="rail", group_results=True)

    api, _ = make_api({"city": {}})
    with pytest.raises(RzdSchemaError, match="must be a list"):
        api.find_stations(query="Нет", transport_type="rail", group_results=True)


def test_train_availability_parses_current_contract() -> None:
    api, transport = make_api(
        {
            "OriginCode": "2000000",
            "DestinationCode": "2004000",
            "AvailabilityItems": [{"Date": "2099-04-03T00:00:00"}],
        }
    )
    result = api.get_train_availability(
        origin="2000000", destination="2004000", date_from="2099-04-01", date_to="2099-04-30"
    )
    assert result.items[0].date == "2099-04-03T00:00:00"
    assert transport.calls[0]["params"]["originStationCode"] == "2000000"


def test_minimal_pricing_parses_current_contract_and_api_typo() -> None:
    api, _ = make_api(
        {
            "OriginStationCode": "2000000",
            "DestinationStationCode": "2004000",
            "PriceByDepartureDates": [
                {
                    "DepatureDate": "2099-04-03T00:00:00",
                    "MinPrice": 2597.2,
                    "DisabledPlaceMinPrice": 4263,
                    "Carriers": [{"CarrierName": "ФПК"}],
                }
            ],
        }
    )
    result = api.get_minimal_pricing(
        origin="2000000", destination="2004000", date_from="2099-04-03"
    )
    assert result.prices[0].date == "2099-04-03T00:00:00"
    assert result.prices[0].min_price == 2597.2
    assert result.prices[0].carriers[0]["CarrierName"] == "ФПК"


def test_carriages_use_current_car_pricing_contract() -> None:
    api, transport = make_api(
        {
            "OriginCode": "1",
            "DestinationCode": "2",
            "Cars": [
                {
                    "CarNumber": "03",
                    "CarType": "Compartment",
                    "CarSubType": "01К",
                    "CarTypeName": "Купе",
                    "ServiceClass": "2Э",
                    "RailwayCarSchemeId": 334,
                    "CarSchemeName": "01К",
                    "Carrier": "ФПК",
                    "CarDirection": "NoValue",
                    "CarNumeration": "FromHead",
                    "TrainNumber": "001А",
                    "MinPrice": "4500.25",
                    "MaxPrice": 5200,
                    "ServiceCost": 100,
                    "PlaceQuantity": 7,
                    "FreePlaces": "1, 2, 3",
                    "Services": ["Bedclothes"],
                    "HasImages": True,
                }
            ],
            "TrainInfo": {
                "TrainNumber": "001А",
                "DepartureDateTime": "2099-01-01T10:00:00",
            },
            "RoutePolicy": "Internal",
            "BookingSystem": "Express3",
            "AllowedDocumentTypes": ["RussianPassport"],
            "OriginRetrievalDate": "2098-12-01T00:00:00",
        }
    )
    result = api.get_carriages(
        origin="1",
        destination="2",
        departure_date="2099-01-01T10:00:00",
        train_number="001А",
        provider="P1",
    )
    assert result.cars[0].number == "03"
    assert result.cars[0].available_places == 7
    assert result.cars[0].scheme_id == 334
    assert result.cars[0].service_class == "2Э"
    assert result.cars[0].services == ["Bedclothes"]
    assert result.train_number == "001А"
    call = transport.calls[0]
    assert call["url"].endswith("/Railway/V1/Search/CarPricing")
    assert call["json_body"]["TrainNumber"] == "001А"
    assert "CarNumber" not in call["json_body"]
    assert "TariffType" not in call["json_body"]


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {},
        {"Cars": [1]},
        {"Cars": [], "TrainInfo": {}, "AllowedDocumentTypes": {}},
        {"Cars": [{"Services": {}}], "TrainInfo": {}, "AllowedDocumentTypes": []},
    ],
)
def test_carriages_reject_invalid_schema(payload: Any) -> None:
    api, _ = make_api(payload)
    with pytest.raises(RzdSchemaError):
        api.get_carriages(
            origin="1",
            destination="2",
            departure_date="2099-01-01T10:00:00",
            train_number="1",
            provider="P1",
        )


def test_car_scheme_and_images_parse_current_contracts() -> None:
    api, transport = make_api(
        {
            "SchemeId": 334,
            "CarSubType": "01Л",
            "PcSchemeFirstStorey": "/334/PcFirstStorey",
            "Direction": "Unknown",
        },
        {
            "SchemeId": 334,
            "CarSubType": "01Л",
            "Images": [
                {
                    "RailwayCarImageId": 757,
                    "TitleRu": "Интерьер купе",
                    "Preview": "/757/Preview",
                    "Content": "/757/Content",
                    "SequenceNumber": 1,
                }
            ],
        },
    )
    params = {
        "car_sub_type": "01Л",
        "car_number": "06",
        "service_class": "1Э",
        "carrier": "ФПК",
        "train_number": "059Г",
        "departure_date": "2099-01-01T10:00:00",
        "car_numeration": "FromHead",
    }
    scheme = api.get_car_scheme(**params)
    images = api.get_car_images(**params)
    assert scheme.first_storey == "/334/PcFirstStorey"
    assert images.images[0].image_id == 757
    assert transport.calls[0]["url"].endswith("/railway-service/carscheme")
    assert transport.calls[1]["url"].endswith("/railway-service/carimage/list")


def test_route_stations_use_current_train_route_contract() -> None:
    api, transport = make_api(
        {
            "Routes": [
                {
                    "Name": "Россия",
                    "OriginName": "МОСКВА",
                    "DestinationName": "С-ПЕТЕРБУРГ",
                    "TrainNumber": "054Г",
                    "RouteStops": [
                        {
                            "StationName": "МОСКВА",
                            "CityName": "Москва",
                            "StationCode": "2000000",
                            "DepartureDateTime": "2099-01-01T10:00:00",
                            "StopDuration": 5,
                            "DaysFromFormingStation": 0,
                            "ActualMovement": True,
                        }
                    ],
                }
            ]
        }
    )
    result = api.get_route_stations(
        origin="1",
        destination="2",
        departure_date="2099-01-01T10:00:00",
        train_number="054Г",
        provider="P1",
    )
    assert result.train_number == "054Г"
    assert result.stations[0].name == "МОСКВА"
    assert result.stations[0].city_name == "Москва"
    assert result.stations[0].actual_movement is True
    assert transport.calls[0]["url"].endswith("/Railway/V1/Search/TrainRoute")
    assert transport.calls[0]["params"]["GetNewRoute"] == "true"


@pytest.mark.parametrize("payload", [[], {}, {"Routes": {}}, {"Routes": [1]}, {"Routes": []}])
def test_route_stations_reject_invalid_schema(payload: Any) -> None:
    api, _ = make_api(payload)
    with pytest.raises(RzdSchemaError):
        api.get_route_stations(
            origin="1",
            destination="2",
            departure_date="2099-01-01T10:00:00",
            train_number="054Г",
            provider="P1",
        )


def test_api_close_delegates_to_transport() -> None:
    api, transport = make_api()
    api.close()
    assert transport.closed is True
