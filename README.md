# RZD API

Типизированный Python-клиент и MCP-сервер для неофициального API
[`ticket.rzd.ru`](https://ticket.rzd.ru). Проект не связан с ОАО «РЖД»; внутренние
endpoint и схема ответов могут изменяться без предупреждения.

TLS certificate verification is enabled by default. Set `RZD_CA_BUNDLE` only when a
custom CA bundle is required; the client never falls back to `verify=False`.

## Возможности

- поиск прямых поездов в одну сторону и туда-обратно;
- поиск станций по названию и синонимам;
- календарь доступности и минимальные цены по датам;
- информация о вагонах, местах, схемах, изображениях и станциях маршрута;
- dataclass-модели с полным исходным объектом в `raw`;
- MCP через `stdio` и защищённый `streamable-http`;
- retries, раздельные таймауты и кэш поиска станций.

Требуется Python 3.10–3.14.

## Установка

```sh
pip install rzd-api
```

С MCP-сервером:

```sh
pip install "rzd-api[mcp]"
```

## Python API

```python
from datetime import date, timedelta

from rzd_api import RoundTripResult, RzdClient

departure = date.today() + timedelta(days=14)
return_date = departure + timedelta(days=3)

with RzdClient() as client:
    result = client.search_tickets(
        "Москва",
        "Санкт-Петербург",
        departure,
        return_date=return_date,
        adults=1,
        children=0,
    )

    if isinstance(result, RoundTripResult):
        for train in result.forward:
            print(train.number, train.departure_time, train.min_price)
        for train in result.back:
            print(train.number, train.departure_time, train.min_price)
```

Все модели поддерживают `to_dict()` и содержат необработанный узел ответа в `raw`.

### Library-only usage without MCP

The base package is intended to work without MCP dependencies:

```sh
python -m pip install -e .
```

```python
from datetime import date, timedelta

import requests

from rzd_api import Config, RzdClient, RzdTransportError

config = Config(connect_timeout=5, read_timeout=20)
departure = date.today() + timedelta(days=14)

try:
    with RzdClient(config) as client:
        routes = client.search_tickets(
            "2000000",
            "2004000",
            departure,
            only_with_seats=False,
        )
except RzdTransportError as exc:
    if isinstance(exc.__cause__, requests.exceptions.SSLError):
        raise RuntimeError("RZD TLS certificate verification failed.") from exc
    raise

for route in routes:
    print(route.number, route.departure_time, route.min_price)
```

To use a custom CA bundle:

```sh
export RZD_CA_BUNDLE=/etc/ssl/certs/custom-rzd-ca.pem
```

Live requests call the external RZD service. MCP is not required for direct
library usage.

### Методы `RzdClient`

| Метод | Результат |
|---|---|
| `search_tickets(...)` | `list[TrainRoute]` или `RoundTripResult` |
| `search_transfers(from_node, to_node, departure_date, ...)` | `TransferSearchResult` |
| `find_stations(query, ...)` | `list[Station]` |
| `resolve_station_code(station)` | код станции |
| `get_carriages(...)` | `CarriageResult` |
| `get_train_availability(...)` | `TrainAvailabilityResult` |
| `get_minimal_prices(...)` | `MinimalPricingResult` |
| `get_car_scheme(...)` | `CarScheme` |
| `get_car_images(...)` | `CarImagesResult` |
| `get_route_stations(...)` | `RouteStationsResult` |

`get_carriages()` использует актуальный `CarPricing` и сразу возвращает все вагоны
поезда. `car_number` этому методу больше не передаётся. Значения `number`,
`car_sub_type`, `service_class`, `carrier` и `numeration` из выбранного `Carriage`
можно передать в `get_car_scheme()` и `get_car_images()`.

```python
with RzdClient() as client:
    carriages = client.get_carriages(
        "2001025", "2004001", departure, "00:48", "059Г"
    )
    car = carriages.cars[0]
    scheme = client.get_car_scheme(
        departure,
        "00:48",
        car.train_number or "059Г",
        car.number or "",
        car.car_sub_type or "",
        car.service_class or "",
        car.carrier or "",
        car_numeration=car.numeration or "FromHead",
    )
```

`only_with_seats=True` фильтрует по доступности мест из `CarGroups`. Современный
pricing endpoint не поддерживает маршруты с пересадками и фильтр типа транспорта,
поэтому `include_transfers=True` и `transport_type="trains"|"suburban"` у
`search_tickets()` явно возвращают `NotImplementedError`.

`search_transfers()` использует отдельный multimodal endpoint
`/apib2b/mmp/onewayRoutesStream/v2`, принимает NodeId из `find_stations()` и
по умолчанию ищет только железнодорожные и пригородные варианты (`b2brails`,
`cbdpr`). Ответ сохраняет typed routes, legs, trips, nested train pricing,
interstation transfer metadata, TTL and `incomplete` flags.
Transfer currency is populated only from an explicit provider `currency` or
`currency_code`; when it is absent or blank, the amount may remain populated while
currency is `None`.

### Конфигурация

```python
from rzd_api import Config, RzdClient

config = Config(
    language="ru",
    # По умолчанию выводится из base_url.
    b2b_base_url=None,
    connect_timeout=5,
    read_timeout=20,
    retry_total=3,
    retry_backoff=0.5,
    station_cache_ttl=3600,
    station_cache_size=256,
    proxy=None,
)
client = RzdClient(config)
```

Ошибки наследуются от `RzdError`: validation, transport, HTTP, API, schema,
station-not-found и ambiguous-station.

## MCP

Инструменты: `search_tickets`, `find_stations`, `get_carriages`,
`get_train_availability`, `get_minimal_prices`, `get_car_scheme`,
`get_car_images`, `get_route_stations`.

Локальный stdio:

```sh
rzd-mcp-server
```

Streamable HTTP на loopback без токена:

```sh
MCP_TRANSPORT=streamable-http MCP_HOST=127.0.0.1 rzd-mcp-server
```

При привязке к non-loopback адресу требуется Bearer-токен минимум из 32 символов:

```sh
export MCP_AUTH_TOKEN="replace-with-a-random-token-at-least-32-characters"
MCP_TRANSPORT=streamable-http MCP_HOST=0.0.0.0 rzd-mcp-server
```

Endpoint MCP: `http://localhost:8000/mcp`; healthcheck: `/health`. Лимит по
умолчанию — 60 запросов в минуту, настраивается через
`MCP_RATE_LIMIT_PER_MINUTE`. Допустимые Host headers можно перечислить через
`MCP_ALLOWED_HOSTS`.

## Docker

```sh
export MCP_AUTH_TOKEN="replace-with-a-random-token-at-least-32-characters"
docker compose up -d
curl http://127.0.0.1:8000/health
```

Контейнер запускается от UID 10001, без Linux capabilities, и публикует порт
только на loopback хоста.

## Разработка

```sh
python -m pip install -e ".[dev]"
make check
```

Live smoke test является opt-in:

```sh
RZD_LIVE_TEST=1 pytest tests/integration -m integration -v
```

Переход с 1.x описан в [MIGRATION.md](MIGRATION.md), изменения релизов — в
[CHANGELOG.md](CHANGELOG.md).

## Лицензия

MIT
