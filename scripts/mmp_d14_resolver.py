"""D14-only GET resolver and private, single-consumption prospective fixture.

No POST exists in this entrypoint. Production client and POST-only guard stay
unchanged. CLI use requires independent code/command review and D14 preflight.
"""

from __future__ import annotations

# Output must be protected before importing dependencies, including preflight.
# ruff: noqa: E402
# isort: off
import os
import sys

_OUTPUT: int | None = None
_COUNTER: Any = None
_PROCESS: Any = None
_STARTED: float | None = None
_SAFE_STOP = (
    b'{"guard_verdict":"STOP","stop_reason":"internal",'
    b'"GET_SENDS":COUNT,"POST_SENDS":0,"fixture_ready":false}\n'
)


def _emergency(*_: object) -> None:
    try:
        if _PROCESS is not None and _PROCESS.is_alive():
            _PROCESS.kill()
        count = _COUNTER.value if _COUNTER is not None else 0
        value = str(count).encode() if type(count) is int and 0 <= count <= 2 else b'"UNKNOWN"'
        os.write(_OUTPUT if _OUTPUT is not None else 1, _SAFE_STOP.replace(b"COUNT", value))
    except BaseException:
        pass
    os._exit(2)


if __name__ == "__main__":
    try:
        _OUTPUT = os.dup(1)
        _null = os.open(os.devnull, os.O_WRONLY)
        os.dup2(_null, 1)
        os.dup2(_null, 2)
        os.close(_null)
    except BaseException:
        _emergency()
    sys.excepthook = _emergency
    if sys.platform == "linux":
        import signal
        import time

        class _Deadline(BaseException):
            pass

        def _expire(*_: object) -> None:
            raise _Deadline

        _STARTED = time.monotonic()
        signal.signal(signal.SIGALRM, _expire)
        signal.setitimer(signal.ITIMER_REAL, 30)

# isort: on
import hashlib
import json
import multiprocessing as mp
import stat
import time
from pathlib import Path
from typing import Any, cast
from urllib.parse import parse_qsl, urlsplit

import requests
import urllib3
from requests.adapters import HTTPAdapter
from requests.utils import get_environ_proxies, select_proxy

from rzd_api.api import RzdApi
from rzd_api.config import Config
from rzd_api.exceptions import RzdHTTPError, RzdSchemaError, RzdTransportError
from rzd_api.models import Station, TransferSearchRequest
from rzd_api.query import RzdTransport
from scripts.mmp_one_shot import silence, source_gate
from scripts.mmp_stage_trace import attach_stage_trace

ENDPOINT = "https://ticket.rzd.ru/api/v1/suggests"
CITIES = ("Бискамжа", "Новосибирск")
REGIONS = ("Республика Хакасия", "Новосибирская область")
DATE = "2026-11-08"
_REASONS = {
    "none",
    "preflight",
    "transport",
    "http_status",
    "schema",
    "identity",
    "handoff",
    "deadline",
    "internal",
}
_FIELDS = {"name", "code", "node_id", "city_id", "node_type", "transport_type", "region", "country"}
_BLOCKED_ENV = {
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "no_proxy",
    "requests_ca_bundle",
    "curl_ca_bundle",
    "ssl_cert_file",
    "ssl_cert_dir",
    "sslkeylogfile",
}


class Halt(Exception):
    def __init__(self, reason: str = "preflight") -> None:
        self.reason = reason if reason in _REASONS else "internal"
        super().__init__()  # no raw exception or input attached


def check(value: Any, reason: str = "preflight") -> None:
    if not value:
        raise Halt(reason)


def report(count: int | str, reason: str = "preflight", *, ready: bool = False) -> dict[str, Any]:
    return {
        "guard_verdict": "PASS" if ready else "STOP",
        "stop_reason": reason,
        "GET_SENDS": count,
        "POST_SENDS": 0,
        "fixture_ready": ready,
    }


def validate_report(value: Any) -> dict[str, Any]:
    check(type(value) is dict and set(value) == set(report(0)), "internal")
    count = value["GET_SENDS"]
    check((type(count) is int and 0 <= count <= 2) or count == "UNKNOWN", "internal")
    check(type(value["POST_SENDS"]) is int and value["POST_SENDS"] == 0, "internal")
    check(value["stop_reason"] in _REASONS, "internal")
    check(type(value["fixture_ready"]) is bool, "internal")
    if value["fixture_ready"]:
        check(
            value["guard_verdict"] == "PASS" and count == 2 and value["stop_reason"] == "none",
            "internal",
        )
    else:
        check(value["guard_verdict"] == "STOP" and value["stop_reason"] != "none", "internal")
    return value


def lookup_params(index: int) -> dict[str, Any]:
    check(index in (0, 1))
    return {
        "Query": CITIES[index],
        "TransportType": "rail",
        "GroupResults": "true",
        "RailwaySortPriority": "true",
        "SynonymOn": 1,
        "Language": "ru",
    }


def prepared_guard(request: requests.PreparedRequest, index: int) -> None:
    check(request.method == "GET" and type(request.url) is str)
    parsed, target = urlsplit(cast(str, request.url)), urlsplit(ENDPOINT)
    check(
        (parsed.scheme, parsed.netloc, parsed.path) == (target.scheme, target.netloc, target.path)
    )
    check(not parsed.fragment)
    expected = {k: str(v) for k, v in lookup_params(index).items()}
    pairs = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
    check(len(pairs) == len(expected) and dict(pairs) == expected)
    check(request.body is None and not request.hooks.get("response"))
    check("Authorization" not in request.headers and "Cookie" not in request.headers)


class ResolverSession(requests.Session):
    def __init__(self) -> None:
        super().__init__()
        self.requests_count = 0
        self.sends_count = 0

    def request(self, method: str, url: str, **kwargs: Any) -> requests.Response:  # type: ignore[override]
        check(self.requests_count < 2 and self.sends_count == self.requests_count)
        check(method == "GET" and url == ENDPOINT)
        check(kwargs.get("params") == lookup_params(self.requests_count))
        check(kwargs.get("allow_redirects", False) is False)
        check(kwargs.get("timeout") == (5.0, 12.0))
        self.requests_count += 1
        kwargs["allow_redirects"] = False  # stock transport omits this keyword
        return super().request(method, url, **kwargs)

    def send(self, request: requests.PreparedRequest, **kwargs: Any) -> requests.Response:
        check(self.sends_count < 2 and self.requests_count == self.sends_count + 1)
        check(kwargs.get("allow_redirects") is False)
        prepared_guard(request, self.sends_count)
        self.sends_count += 1
        return super().send(request, **kwargs)


def ambient_guard() -> None:
    check(not any(v for k, v in os.environ.items() if k.lower() in _BLOCKED_ENV))
    check(not get_environ_proxies(ENDPOINT))


def config_guard(settings: Any) -> Config:
    check(requests.__version__ == "2.34.2" and urllib3.__version__ == "2.8.0")
    check(
        all(
            os.getenv(k) == "1"
            for k in (
                "RZD_LIVE_TEST",
                "RZD_MMP_PREFLIGHT_OK",
                "RZD_MMP_BUDGET_OK",
                "RZD_D14_RESOLVER_OK",
            )
        )
    )
    check(
        type(settings) is dict and set(settings) == {"proxy", "ca_bundle", "user_agent", "referer"}
    )
    ambient_guard()
    check(type(settings["proxy"]) is str and type(settings["ca_bundle"]) is str)
    proxy = urlsplit(settings["proxy"])
    check(proxy.scheme == "http" and proxy.hostname and proxy.port)
    check(not proxy.path and not proxy.query and not proxy.fragment)
    check(settings["ca_bundle"] and Path(settings["ca_bundle"]).is_file())
    check(not os.getenv("RZD_CA_BUNDLE") or os.getenv("RZD_CA_BUNDLE") == settings["ca_bundle"])
    return Config(**settings, language="ru", retry_total=0, connect_timeout=5, read_timeout=12)


def _normalize_city_name(value: str) -> str:
    return " ".join(value.strip().casefold().split())


def _select_city_backed_rail_station(query: str, stations: list[Station]) -> Station | None:
    # Verbatim algorithm (types/format adapted only) from WayFound direct_hub.py
    # d713b49e1e4c0cf922a0ac6eb8c8818037392252:3299-3321. D14 strictness below
    # runs BEFORE its by-code deduplication; production WayFound is not changed.
    exact = [
        s
        for s in stations
        if _normalize_city_name(s.name) == _normalize_city_name(query) and s.code
    ]
    if not exact:
        return None
    by_code: dict[str, Station] = {}
    for station in exact:
        code = str(station.code).strip()
        if code:
            by_code.setdefault(code, station)
    candidates = tuple(by_code.values())
    if len(candidates) == 1:
        return candidates[0]
    city_nodes = [s for s in candidates if (s.node_type or "").strip().casefold() == "city"]
    if len(city_nodes) == 1:
        return city_nodes[0]
    return None


def select_station(index: int, stations: list[Station]) -> Station:
    exact = [
        s for s in stations if _normalize_city_name(s.name) == _normalize_city_name(CITIES[index])
    ]
    # Count OBJECTS, never a set of ids/codes; even identical duplicates STOP.
    check(len(exact) == 1, "identity")
    selected = _select_city_backed_rail_station(CITIES[index], stations)
    check(selected is exact[0], "identity")
    check(selected is not None, "identity")
    selected = cast(Station, selected)
    check((selected.node_type or "").strip().casefold() == "city", "identity")
    check(selected.city_id and (selected.node_id or selected.city_id), "identity")
    check(
        _normalize_city_name(selected.region or "") == _normalize_city_name(REGIONS[index]),
        "identity",
    )
    check(_normalize_city_name(selected.country or "") in {"ru", "россия"}, "identity")
    check((selected.transport_type or "").strip().casefold() == "rail", "identity")
    return selected


def fixture_for(stations: list[Station]) -> dict[str, Any]:
    check(len(stations) == 2, "identity")
    nodes = [select_station(i, [s]).node_id or s.city_id for i, s in enumerate(stations)]
    check(nodes[0] != nodes[1], "identity")
    request = TransferSearchRequest(
        origin=cast(str, nodes[0]),
        destination=cast(str, nodes[1]),
        departure_date=DATE,
        min_trips=1,
        max_trips=3,
        max_results=5,
    )
    body = RzdApi._transfer_request_body(request)
    # Independent fixed D14 contract, in addition to the stock builder.
    check(
        body
        == {
            "max_results": 5,
            "min_trips_in_leg": 1,
            "max_trips_in_leg": 3,
            "system_params": {
                "search_via_ar": "SVA_DONT_SEARCH",
                "search_via_graph": "SVG_SEARCH_WITH_DETAIL",
                "detailed_location": True,
                "debug": False,
            },
            "start_location": {"city": {"key": nodes[0]}, "type": "city"},
            "finish_location": {"city": {"key": nodes[1]}, "type": "city"},
            "start_datetime_range": {"from": "2026-11-08T00:00:00", "to": "2026-11-08T23:59:59"},
            "filters": [
                {
                    "param_name": "route.provider.key",
                    "exact_filter": {"param_values": ["b2brails", "cbdpr"]},
                }
            ],
        },
        "handoff",
    )
    return {"body": body}


def canonical_digest(fixture: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(fixture, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def make_handoff(settings: dict[str, Any], stations: list[Station]) -> dict[str, Any]:
    fixture = fixture_for(stations)
    return {
        "version": "D14",
        "config": settings,
        "fixture": fixture,
        "fixture_sha256": canonical_digest(fixture),
        "stations": [{key: getattr(s, key) for key in sorted(_FIELDS)} for s in stations],
    }


def validate_handoff(value: Any) -> tuple[dict[str, Any], str]:
    check(
        type(value) is dict
        and set(value) == {"version", "config", "fixture", "fixture_sha256", "stations"},
        "handoff",
    )
    check(
        value["version"] == "D14"
        and type(value["stations"]) is list
        and len(value["stations"]) == 2,
        "handoff",
    )
    stations = []
    for entry in value["stations"]:
        check(type(entry) is dict and set(entry) == _FIELDS, "handoff")
        check(all(type(v) is str or v is None for v in entry.values()), "handoff")
        stations.append(Station(**entry))
    check(value["fixture"] == fixture_for(stations), "handoff")
    check(value["fixture_sha256"] == canonical_digest(value["fixture"]), "handoff")
    check(
        type(value["config"]) is dict
        and set(value["config"]) == {"proxy", "ca_bundle", "user_agent", "referer"},
        "handoff",
    )
    return {"config": value["config"], "fixture": value["fixture"]}, value["fixture_sha256"]


def resolve(data: Any, counter: Any, deadline_at: float) -> tuple[dict[str, Any], Any]:
    session = None
    try:
        check(type(data) is dict and set(data) == {"config", "handoff_path"})
        destination(data["handoff_path"])
        config = config_guard(data["config"])
        session = ResolverSession()
        transport = RzdTransport(config, session=session)
        stages: set[str] = set()

        def before_stage(stage: str) -> None:
            check(stage not in stages, "transport")
            stages.add(stage)

        attach_stage_trace(transport, before_stage=before_stage)
        adapter = cast(HTTPAdapter, session.get_adapter(ENDPOINT))
        original_send = adapter.send

        def guarded_send(request: requests.PreparedRequest, **kwargs: Any) -> requests.Response:
            check(time.monotonic() < deadline_at, "deadline")
            check(type(counter.value) is int and counter.value < 2)
            prepared_guard(request, counter.value)
            check(session is not None and session.trust_env is True)
            check(session.requests_count == session.sends_count == counter.value + 1)
            check(
                all(
                    getattr(adapter.max_retries, k) == 0
                    for k in ("total", "connect", "read", "status")
                )
            )
            check(kwargs.get("timeout") == (5, 12) and kwargs.get("verify") == config.ca_bundle)
            check(kwargs.get("cert") is None and kwargs.get("stream") is False)
            check(select_proxy(request.url or "", kwargs.get("proxies") or {}) == config.proxy)
            ambient_guard()
            stages.clear()
            counter.value += 1
            return original_send(request, **kwargs)

        adapter.send = guarded_send  # type: ignore[method-assign, assignment]
        # A prepared scheme mutation must not select the unguarded stock HTTP
        # adapter after Session.send validation. Both mounts fail through the
        # same exact-HTTPS guard; this never permits an HTTP request.
        session.mount("http://", adapter)
        selected = []
        for index in (0, 1):
            payload = transport.request_json("GET", ENDPOINT, params=lookup_params(index))
            nodes = RzdApi._suggestion_nodes(payload)
            check(RzdApi._station_nodes_match_schema(nodes), "schema")
            # Parse before find_stations' normal dedup, preserving ambiguity.
            selected.append(select_station(index, list(RzdApi._parse_station_nodes(nodes))))
        check(counter.value == session.requests_count == session.sends_count == 2)
        handoff = make_handoff(data["config"], selected)
        validate_handoff(handoff)
        return report(2, "none", ready=True), handoff
    except Halt as error:
        return report(counter.value, error.reason), None
    except RzdHTTPError:
        return report(counter.value, "http_status"), None
    except RzdTransportError:
        return report(counter.value, "transport"), None
    except RzdSchemaError:
        return report(counter.value, "schema"), None
    except BaseException:
        return report(counter.value, "internal"), None
    finally:
        if session is not None:
            session.close()


def destination(value: Any) -> Path:
    check(sys.platform == "linux" and type(value) is str, "handoff")
    path = Path(value)
    check(
        path.is_absolute() and path.parent.resolve() == path.parent and not path.exists(), "handoff"
    )
    check(path.name not in {"", ".", ".."}, "handoff")
    info = path.parent.stat()
    check(info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == 0o700, "handoff")
    root = Path(__file__).resolve().parents[1]
    check(root != path.parent and root not in path.parent.parents, "handoff")
    return path


def publish(value: Any, handoff: dict[str, Any]) -> None:
    path = destination(value)
    validate_handoff(handoff)
    encoded = json.dumps(handoff, separators=(",", ":")).encode()
    check(len(encoded) <= 16384, "handoff")
    created = False
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        created = True
        with os.fdopen(descriptor, "wb") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        if created:
            path.unlink(missing_ok=True)
        raise


def consume_handoff(value: str) -> tuple[dict[str, Any], str]:
    """Read ONCE and unlink before parsing/use; never print returned private data.

    A separately reviewed OWNER driver may pass this validated input + fresh
    digest to the unchanged POST-only runner. This function sends no request.
    """
    path = Path(value)
    check(sys.platform == "linux" and path.is_absolute(), "handoff")
    check(path.parent.resolve() == path.parent, "handoff")
    parent = path.parent.stat()
    check(parent.st_uid == os.getuid() and stat.S_IMODE(parent.st_mode) == 0o700, "handoff")
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        check(stat.S_ISREG(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o600, "handoff")
        check(info.st_uid == os.getuid() and info.st_nlink == 1, "handoff")
        # Ownership proven: consume even malformed/oversized input on failure.
        path.unlink()
        check(info.st_size <= 16384, "handoff")
        encoded = stream.read(16385)
    return validate_handoff(json.loads(encoded))


def worker(data: Any, connection: Any, counter: Any, deadline_at: float) -> None:
    silence()
    result = report(0)
    handoff = None
    try:
        check(time.monotonic() < deadline_at, "deadline")
        if sys.platform == "linux":
            import signal

            signal.signal(signal.SIGALRM, lambda *_: os._exit(2))
            signal.setitimer(signal.ITIMER_REAL, max(0.001, deadline_at - time.monotonic()))
        source_gate()  # exact reviewed source/clean checkout/venv; never skipped
        result, handoff = resolve(data, counter, deadline_at)
    except BaseException:
        result, handoff = report(counter.value, "internal"), None
    encoded = json.dumps({"report": validate_report(result), "handoff": handoff}).encode()
    if len(encoded) > 4096:
        encoded = json.dumps({"report": report(counter.value, "handoff"), "handoff": None}).encode()
    connection.send_bytes(encoded)
    connection.close()


def supervise(
    data: Any, *, target: Any = worker, seconds: float = 30
) -> tuple[dict[str, Any], Any]:
    global _COUNTER, _PROCESS
    started = time.monotonic()
    if _STARTED is not None:
        seconds = min(seconds, max(0, 30 - (started - _STARTED)))
    context = mp.get_context("spawn")
    receive, send = context.Pipe(duplex=False)
    _COUNTER = context.Value("i", 0, lock=False)
    _PROCESS = context.Process(
        target=target, args=(data, send, _COUNTER, started + seconds), daemon=True
    )
    try:
        _PROCESS.start()
        send.close()
        _PROCESS.join(max(0, seconds - (time.monotonic() - started)))
        if _PROCESS.is_alive():
            _PROCESS.kill()
            _PROCESS.join(1)
            return report(_COUNTER.value, "deadline"), None
        check(_PROCESS.exitcode == 0 and receive.poll(), "internal")
        payload = json.loads(receive.recv_bytes(4096))
        check(type(payload) is dict and set(payload) == {"report", "handoff"}, "internal")
        result = validate_report(payload["report"])
        check(result["GET_SENDS"] == _COUNTER.value, "internal")
        if result["fixture_ready"]:
            validate_handoff(payload["handoff"])
        else:
            check(payload["handoff"] is None, "internal")
        return result, payload["handoff"]
    except BaseException:
        return report(_COUNTER.value, "internal"), None
    finally:
        if _PROCESS.is_alive():
            _PROCESS.kill()
            _PROCESS.join(1)
        receive.close()
        send.close()


def main() -> int:
    output = _OUTPUT if _OUTPUT is not None else os.dup(1)
    silence()
    result = report(0)
    try:
        check(sys.platform == "linux" and len(sys.argv) == 1)
        info = os.fstat(0)
        check(stat.S_ISREG(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o600)
        check(info.st_uid == os.getuid() and info.st_nlink == 1 and info.st_size <= 16384)
        data = json.loads(sys.stdin.read(16385))
        check(type(data) is dict and set(data) == {"config", "handoff_path"})
        destination(data["handoff_path"])  # fail before spending any GET budget
        result, handoff = supervise(data)
        if result["fixture_ready"]:
            publish(data["handoff_path"], handoff)
    except Halt as error:
        result = report(_COUNTER.value if _COUNTER is not None else 0, error.reason)
    except BaseException:
        result = report(_COUNTER.value if _COUNTER is not None else 0, "internal")
    if sys.platform == "linux":
        import signal

        signal.setitimer(signal.ITIMER_REAL, 0)
    os.write(output, (json.dumps(validate_report(result), sort_keys=True) + "\n").encode())
    os.close(output)
    return 0 if result["fixture_ready"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
