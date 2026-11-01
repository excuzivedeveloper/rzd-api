"""Offline WF193 regressions; fixtures are synthetic, not private lab captures."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import requests

from rzd_api.api import RzdApi
from rzd_api.config import Config
from rzd_api.exceptions import RzdHTTPError, RzdSchemaError
from rzd_api.models import TransferSearchRequest
from rzd_api.query import RzdTransport


def payload(money: Any) -> dict[str, Any]:
    fixture = Path(__file__).parent / "fixtures" / "transfer-search.json"
    data = json.loads(fixture.read_text(encoding="utf-8"))
    data["multi_modal_routes"][0]["min_price"] = money
    return data


def test_cookie_ab_same_body_and_no_get_cookie_pollution(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []
    fixture = payload({"kopecks": "12345"})

    def respond(_session: requests.Session, **kwargs: Any) -> requests.Response:
        calls.append(kwargs)
        response = requests.Response()
        response.status_code = 200
        data: Any = []
        if kwargs["method"] == "POST":
            if (kwargs.get("headers") or {}).get("Cookie") == "LANG_SITE=ru":
                data = fixture
            else:
                response.status_code = 500
                data = {"error": "synthetic missing language cookie"}
        response._content = json.dumps(data).encode()
        return response

    monkeypatch.setattr(requests.Session, "request", respond)
    config = Config(retry_total=0)
    request = TransferSearchRequest("city-a", "city-c", "2099-02-01")
    # Independent sessions; negative control uses the same serializer/body.
    with RzdTransport(config) as negative:
        with pytest.raises(RzdHTTPError) as error:
            negative.request_json(
                "POST",
                "https://ticket.rzd.ru/apib2b/mmp/onewayRoutesStream/v2",
                json_body=RzdApi._transfer_request_body(request),
            )
        assert error.value.status_code == 500
    api = RzdApi(config)
    try:
        result = api.search_transfers(request)
        api.find_stations(query="synthetic", transport_type="rail", group_results=True)
        assert len(result.routes) == 2
        assert result.routes[0].price == 123.45
        assert result.routes[0].currency is None  # Cookie success is not currency proof.
        assert "Cookie" not in api.transport.session.headers
        assert not api.transport.session.cookies
        assert calls[0]["json"] == calls[1]["json"]
        assert calls[0]["url"] == calls[1]["url"]
        assert calls[0]["timeout"] == calls[1]["timeout"]
        assert calls[0]["headers"] is None
        assert calls[1]["headers"] == {"Cookie": "LANG_SITE=ru"}
        assert calls[2]["method"] == "GET"
        assert calls[2]["headers"] is None
        assert len(calls) == 3
    finally:
        api.close()


@pytest.mark.parametrize(
    "kopecks,expected",
    [
        ("12345", 123.45),
        (12345, 123.45),
        ("0", 0),
        (0, 0),
        ("-100", -1),
        (-100, -1),
        (None, None),
        ("", None),
        (True, None),
    ],
)
def test_kopecks_value_does_not_prove_currency(kopecks: Any, expected: float | None) -> None:
    route = RzdApi._parse_transfer_result(payload({"kopecks": kopecks})).routes[0]
    assert route.price == expected
    assert route.currency is None


@pytest.mark.parametrize("currency", ["RUB", "KZT", "USD", "EUR", "UAH"])
@pytest.mark.parametrize("key", ["currency", "currency_code"])
def test_only_explicit_currency_is_preserved(currency: str, key: str) -> None:
    route = RzdApi._parse_transfer_result(payload({"kopecks": 12345, key: currency})).routes[0]
    assert route.price == 123.45
    assert route.currency == currency  # Consumer decides its supported currencies.


@pytest.mark.parametrize("currency", [None, "", "   "])
def test_missing_or_blank_currency_stays_unknown(currency: Any) -> None:
    route = RzdApi._parse_transfer_result(payload({"kopecks": 12345, "currency": currency})).routes[
        0
    ]
    assert route.currency is None


@pytest.mark.parametrize("amount", ["not-money", "NaN", "Infinity", "-Infinity"])
@pytest.mark.parametrize("shape", ["kopecks", "amount", "scalar"])
def test_invalid_or_nonfinite_transfer_money_is_schema_error(amount: str, shape: str) -> None:
    money = amount if shape == "scalar" else {shape: amount, "currency": "RUB"}
    with pytest.raises(RzdSchemaError):
        RzdApi._parse_transfer_result(payload(money))


def test_leg_price_does_not_supply_missing_aggregate():
    fixture = payload(None)
    fixture["multi_modal_routes"][0]["routes"][0]["min_price"] = {
        "kopecks": "12345",
        "currency": "RUB",
    }
    route = RzdApi._parse_transfer_result(fixture).routes[0]
    assert route.price is None
    assert route.currency is None
    assert route.legs[0].currency == "RUB"
