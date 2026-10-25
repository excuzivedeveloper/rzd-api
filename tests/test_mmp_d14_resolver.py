"""Synthetic D14 tests: never call an external provider."""

import copy
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests
from requests.adapters import HTTPAdapter
from test_mmp_stage_trace import Origin, Proxy
from test_mmp_stage_trace import loopback as _loopback

from rzd_api.api import RzdApi
from rzd_api.models import Station
from scripts import mmp_d14_resolver as resolver

loopback = _loopback


def test_fixed_query_has_no_redirect_kwarg_requirement():
    assert resolver.lookup_params(0)["Query"] == "Бискамжа"
    assert resolver.lookup_params(1)["Query"] == "Новосибирск"


def test_report_cannot_leak_identifiers():
    with pytest.raises(resolver.Halt):
        resolver.validate_report({**resolver.report(0), "stop_reason": "SECRET_NODE"})


def station(index):
    return Station(
        name=resolver.CITIES[index],
        code=f"unused-railway-code-{index}",
        node_id=f"synthetic-node-{index}",
        city_id=f"synthetic-city-{index}",
        node_type="city",
        transport_type="rail",
        region=resolver.REGIONS[index],
        country="RU",
    )


def payload(index):
    s = station(index)
    return [
        {
            "name": s.name,
            "code": s.code,
            "nodeId": s.node_id,
            "cityId": s.city_id,
            "nodeType": s.node_type,
            "transportType": s.transport_type,
            "region": s.region,
            "country": s.country,
        }
    ]


@pytest.fixture
def approved(monkeypatch, tmp_path):
    for key in list(os.environ):
        if key.lower() in resolver._BLOCKED_ENV | {"rzd_ca_bundle"}:
            monkeypatch.delenv(key)
    for key in (
        "RZD_LIVE_TEST",
        "RZD_MMP_PREFLIGHT_OK",
        "RZD_MMP_BUDGET_OK",
        "RZD_D14_RESOLVER_OK",
    ):
        monkeypatch.setenv(key, "1")
    ca = tmp_path / "synthetic-ca.pem"
    ca.write_text("mocked HTTPAdapter; no TLS bypass")
    monkeypatch.setattr(resolver, "destination", lambda value: Path(value))
    return {
        "config": {
            "proxy": "http://127.0.0.1:9",
            "ca_bundle": str(ca),
            "user_agent": None,
            "referer": None,
        },
        "handoff_path": str(tmp_path / "private-handoff.json"),
    }


def resolve(data):
    count = SimpleNamespace(value=0)
    result, private = resolver.resolve(data, count, time.monotonic() + 30)
    assert result["GET_SENDS"] == count.value
    assert result["POST_SENDS"] == 0
    return result, private


def responder(monkeypatch, statuses=(200, 200), payloads=None):
    calls = []

    def send(adapter, request, **kwargs):
        index = len(calls)
        calls.append((request, kwargs))
        result = requests.Response()
        result.request = request
        result.url = request.url
        result.status_code = statuses[index]
        result._content = json.dumps(
            payload(index) if payloads is None else payloads[index]
        ).encode()
        if result.status_code == 302:
            result.headers["Location"] = "https://forbidden.invalid/SECRET"
        return result

    monkeypatch.setattr(HTTPAdapter, "send", send)
    return calls


def test_success_forces_redirect_false_and_preserves_stock_shape(approved, monkeypatch):
    calls = responder(monkeypatch)
    actual_send = requests.Session.send

    def observe(session, request, **kwargs):
        assert kwargs["allow_redirects"] is False
        return actual_send(session, request, **kwargs)

    monkeypatch.setattr(requests.Session, "send", observe)
    result, private = resolve(approved)
    assert result == resolver.report(2, "none", ready=True)
    data, digest = resolver.validate_handoff(private)
    assert digest == resolver.canonical_digest(data["fixture"])
    assert "RZD_MMP_FIXTURE_SHA256" not in dict(approved)  # no historical hash input
    assert len(calls) == 2
    for index, (request, kwargs) in enumerate(calls):
        expected = requests.Request(
            "GET", resolver.ENDPOINT, params=resolver.lookup_params(index)
        ).prepare()
        assert request.url == expected.url
        assert request.body is None and "Cookie" not in request.headers
        assert kwargs["verify"] == approved["config"]["ca_bundle"]
        assert kwargs["timeout"] == (5, 12)
    assert data["fixture"]["body"] == RzdApi._transfer_request_body(
        resolver.TransferSearchRequest(
            origin="synthetic-node-0",
            destination="synthetic-node-1",
            departure_date="2026-11-08",
            max_results=5,
        )
    )


@pytest.mark.parametrize("status", [301, 302, 307, 400, 500])
def test_http_error_stops_without_redirect_retry_or_second_city(approved, monkeypatch, status):
    calls = responder(monkeypatch, statuses=(status, 200))
    result, private = resolve(approved)
    assert result == resolver.report(1, "http_status") and private is None
    assert len(calls) == 1


@pytest.mark.parametrize(
    "failure",
    [
        requests.ConnectTimeout,
        requests.ReadTimeout,
        requests.exceptions.SSLError,
        requests.ConnectionError,
    ],
)
def test_transport_failure_stops_no_second_city(approved, monkeypatch, failure):
    calls = []

    def fail(*args, **kwargs):
        calls.append(1)
        raise failure("SECRET_PROXY_URL")

    monkeypatch.setattr(HTTPAdapter, "send", fail)
    result, private = resolve(approved)
    assert result == resolver.report(1, "transport") and private is None
    assert calls == [1] and "SECRET" not in json.dumps(result)


@pytest.mark.parametrize(
    "change", ["host", "scheme", "path", "query", "duplicate", "extra", "auth", "cookie", "method"]
)
@pytest.mark.parametrize("boundary", ["session", "adapter"])
def test_prepared_request_tampering_rejected_at_both_boundaries(
    approved, monkeypatch, change, boundary
):
    calls = responder(monkeypatch)
    original = resolver.ResolverSession.send if boundary == "session" else requests.Session.send

    def tamper(session, request, **kwargs):
        if change == "host":
            request.url = request.url.replace("ticket.rzd.ru", "evil.invalid")
        elif change == "scheme":
            request.url = request.url.replace("https:", "http:")
        elif change == "path":
            request.url = request.url.replace("/suggests", "/other")
        elif change == "query":
            request.url += "&Query=OTHER"
        elif change == "duplicate":
            request.url += "&Language=ru"
        elif change == "extra":
            request.url += "&EXTRA=1"
        elif change == "auth":
            request.headers["Authorization"] = "SECRET"
        elif change == "cookie":
            request.headers["Cookie"] = "SECRET"
        else:
            request.method = "POST"
        return original(session, request, **kwargs)

    monkeypatch.setattr(
        resolver.ResolverSession if boundary == "session" else requests.Session, "send", tamper
    )
    result, private = resolve(approved)
    assert result["guard_verdict"] == "STOP" and result["GET_SENDS"] == 0 and private is None
    assert calls == []


@pytest.mark.parametrize("change", ["proxy", "ca", "retry"])
def test_actual_adapter_policy_cannot_be_overridden(approved, monkeypatch, change):
    calls = responder(monkeypatch)
    original = requests.Session.send

    def override(session, request, **kwargs):
        if change == "proxy":
            kwargs["proxies"]["https://ticket.rzd.ru"] = "http://evil.invalid:9"
        elif change == "ca":
            kwargs["verify"] = False
        else:
            session.get_adapter(request.url).max_retries = resolver.urllib3.Retry(total=1)
        return original(session, request, **kwargs)

    monkeypatch.setattr(requests.Session, "send", override)
    result, _ = resolve(approved)
    assert result["guard_verdict"] == "STOP" and result["GET_SENDS"] == 0 and calls == []


def test_netrc_authorization_is_rejected(approved, monkeypatch):
    calls = responder(monkeypatch)
    monkeypatch.setattr(requests.sessions, "get_netrc_auth", lambda *args: ("SECRET", "PASSWORD"))
    result, _ = resolve(approved)
    assert result["guard_verdict"] == "STOP" and result["GET_SENDS"] == 0 and calls == []


@pytest.mark.parametrize("key", sorted(resolver._BLOCKED_ENV))
def test_ambient_collision_rejected_without_fixing_environment(approved, monkeypatch, key):
    calls = responder(monkeypatch)
    monkeypatch.setenv(key.upper(), "SECRET")
    result, _ = resolve(approved)
    assert result["guard_verdict"] == "STOP" and result["GET_SENDS"] == 0 and calls == []
    assert os.environ[key.upper()] == "SECRET"


@pytest.mark.parametrize(
    "kind",
    [
        "missing",
        "duplicate",
        "same_node",
        "same_code",
        "wrong_type",
        "wrong_region",
        "wrong_city",
        "wrong_transport",
        "wrong_country",
        "wrong_name",
    ],
)
def test_strict_candidate_objects_no_id_collapse(kind):
    s = station(0)
    choices = [s]
    if kind == "missing":
        choices = []
    elif kind in {"duplicate", "same_node", "same_code"}:
        other = copy.deepcopy(s)
        if kind == "same_node":
            other.code = "another-code"
        if kind == "same_code":
            other.node_id = "another-node"
        choices.append(other)
    elif kind == "wrong_type":
        s.node_type = "station"
    elif kind == "wrong_region":
        s.region = "wrong-region"
    elif kind == "wrong_city":
        s.city_id = None
    elif kind == "wrong_transport":
        s.transport_type = "bus"
    elif kind == "wrong_country":
        s.country = "other"
    else:
        s.name = resolver.CITIES[1]
    with pytest.raises(resolver.Halt):
        resolver.select_station(0, choices)


def test_city_id_fallback_never_uses_station_code():
    s = station(0)
    s.node_id = None
    fixture = resolver.fixture_for([s, station(1)])
    assert fixture["body"]["start_location"]["city"]["key"] == s.city_id
    assert s.code not in json.dumps(fixture)


@pytest.mark.parametrize(
    "malformed", [None, 123, {}, {"city": "invalid"}, [{"name": "missing code"}]]
)
def test_schema_failure_no_handoff_or_second_get(approved, monkeypatch, malformed):
    calls = responder(monkeypatch, payloads=[malformed])
    result, private = resolve(approved)
    assert result["guard_verdict"] == "STOP" and private is None and len(calls) == 1


def test_duplicate_objects_are_preserved_before_api_dedup(approved, monkeypatch):
    calls = responder(monkeypatch, payloads=[payload(0) * 2])
    result, private = resolve(approved)
    assert result == resolver.report(1, "identity") and private is None and len(calls) == 1


@pytest.mark.parametrize("field", ["date", "provider", "node", "digest", "shape"])
def test_prospective_handoff_detects_contract_tampering(approved, field):
    private = resolver.make_handoff(approved["config"], [station(0), station(1)])
    if field == "date":
        private["fixture"]["body"]["start_datetime_range"]["from"] = "2099-01-01T00:00:00"
    elif field == "provider":
        private["fixture"]["body"]["filters"] = []
    elif field == "node":
        private["fixture"]["body"]["start_location"]["city"]["key"] = "guessed"
    elif field == "digest":
        private["fixture_sha256"] = "historical-B12B"
    else:
        private["fixture"]["extra"] = True
    # Rehashing a changed body is not sufficient to pass the fixed contract.
    if field != "digest":
        private["fixture_sha256"] = resolver.canonical_digest(private["fixture"])
    with pytest.raises(resolver.Halt):
        resolver.validate_handoff(private)


@pytest.mark.skipif(sys.platform != "linux", reason="POSIX private single-use handoff")
def test_private_handoff_consumed_once_and_removed_even_when_invalid(tmp_path, monkeypatch):
    directory = tmp_path / "private"
    directory.mkdir(mode=0o700)
    path = directory / "fixture.json"
    settings = {
        "proxy": "http://127.0.0.1:9",
        "ca_bundle": "/synthetic",
        "user_agent": None,
        "referer": None,
    }
    private = resolver.make_handoff(settings, [station(0), station(1)])
    resolver.publish(str(path), private)
    assert path.stat().st_mode & 0o777 == 0o600
    data, digest = resolver.consume_handoff(str(path))
    assert not path.exists() and data["fixture"] == private["fixture"]
    assert digest == private["fixture_sha256"]
    from scripts import mmp_one_shot as post

    ca = directory / "synthetic-ca.pem"
    ca.write_text("offline preflight only; no request")
    data["config"]["ca_bundle"] = str(ca)
    for key in list(os.environ):
        if key.lower() in resolver._BLOCKED_ENV | {"rzd_ca_bundle"}:
            monkeypatch.delenv(key)
    for key in ("RZD_LIVE_TEST", "RZD_MMP_PREFLIGHT_OK", "RZD_MMP_BUDGET_OK"):
        monkeypatch.setenv(key, "1")
    monkeypatch.setenv("RZD_MMP_FIXTURE_SHA256", digest)
    _, body = post.preflight(data)
    assert body == private["fixture"]["body"]  # unchanged POST guard consumes it
    with pytest.raises(FileNotFoundError):
        resolver.consume_handoff(str(path))
    path.write_text('{"SECRET":"invalid"}')
    path.chmod(0o600)
    with pytest.raises(resolver.Halt):
        resolver.consume_handoff(str(path))
    assert path.exists()  # unknown task protocol; leave the file untouched
    path.write_bytes(resolver._HANDOFF_MAGIC + b'{"SECRET":"invalid"}')
    with pytest.raises(resolver.Halt):
        resolver.consume_handoff(str(path))
    assert not path.exists()


@pytest.mark.skipif(sys.platform != "linux", reason="POSIX owner/mode/symlink checks")
def test_handoff_never_overwrites_and_refuses_symlink_or_loose_permissions(tmp_path):
    directory = tmp_path / "private"
    directory.mkdir(mode=0o700)
    original = directory / "original"
    original.write_text("KEEP")
    with pytest.raises(resolver.Halt):
        resolver.destination(str(original))
    symlink = directory / "link"
    symlink.symlink_to(original)
    with pytest.raises(OSError):
        resolver.consume_handoff(str(symlink))
    assert original.read_text() == "KEEP"
    original.chmod(0o644)
    with pytest.raises(resolver.Halt):
        resolver.consume_handoff(str(original))
    assert original.exists()


def hanging_worker(data, connection, counter, deadline_at):
    counter.value = 1
    time.sleep(10)


def wrong_count_worker(data, connection, counter, deadline_at):
    connection.send_bytes(
        json.dumps({"report": resolver.report(1, "transport"), "handoff": None}).encode()
    )


def test_supervisor_deadline_and_unverifiable_count_stop():
    result, private = resolver.supervise({}, target=hanging_worker, seconds=3)
    assert result == resolver.report(1, "deadline") and private is None
    result, private = resolver.supervise({}, target=wrong_count_worker)
    assert result == resolver.report("UNKNOWN", "internal") and private is None


@pytest.mark.parametrize("point", ["imports", "dup", "open", "preflight", "lookup"])
def test_all_output_and_exception_text_are_redacted(point):
    code = """
import os, runpy, sys, time
from importlib.abc import MetaPathFinder
if POINT in {"imports", "dup", "open"}:
    class Fail(MetaPathFinder):
        def find_spec(self, fullname, path, target=None):
            if fullname == "requests": raise ImportError("SECRET_IMPORT")
    sys.meta_path.insert(0, Fail())
    def fd_failure(*args): raise OSError("SECRET_FD_PATH")
    if POINT == "dup": os.dup = fd_failure
    if POINT == "open": os.open = fd_failure
    runpy.run_module("scripts.mmp_d14_resolver", run_name="__main__")
else:
    from scripts import mmp_d14_resolver as r
    from types import SimpleNamespace
    out = os.dup(1)
    class Channel:
        def send_bytes(self, data): os.write(out, data)
        def close(self): pass
    def fail(*args, **kwargs):
        print("SECRET_STDOUT", flush=True)
        os.write(2, b"SECRET_STDERR")
        raise RuntimeError("SECRET_PATH")
    if POINT == "preflight": r.source_gate = fail
    else:
        r.source_gate = lambda: None
        r.resolve = fail
    r.worker({}, Channel(), SimpleNamespace(value=0), time.monotonic() + 5)
""".replace("POINT", repr(point))
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, timeout=10)
    assert result.stderr == b"" and b"SECRET" not in result.stdout
    value = json.loads(result.stdout)
    safe = value if point in {"imports", "dup", "open"} else value["report"]
    assert safe["guard_verdict"] == "STOP" and safe["POST_SENDS"] == 0


def test_expired_budget_blocks_first_get(approved, monkeypatch):
    calls = responder(monkeypatch)
    result, private = resolver.resolve(approved, SimpleNamespace(value=0), time.monotonic() - 1)
    assert result == resolver.report(0, "deadline") and private is None and calls == []


@pytest.mark.parametrize("point", ["request", "session_send", "adapter_send"])
def test_third_get_cannot_spend_another_attempt(approved, monkeypatch, point):
    calls = responder(monkeypatch)
    original = resolver.RzdTransport.request_json

    def extra(transport, *args, **kwargs):
        result = original(transport, *args, **kwargs)
        session = transport.session
        if session.requests_count == 2:
            if point == "request":
                original(transport, *args, **kwargs)
            else:
                prepared = requests.Request(
                    "GET", resolver.ENDPOINT, params=resolver.lookup_params(1)
                ).prepare()
                if point == "session_send":
                    session.send(prepared, allow_redirects=False)
                else:
                    session.get_adapter(prepared.url).send(prepared)
        return result

    monkeypatch.setattr(resolver.RzdTransport, "request_json", extra)
    result, private = resolve(approved)
    assert result["guard_verdict"] == "STOP" and result["GET_SENDS"] == 2
    assert private is None and len(calls) == 2


def test_explicit_redirect_true_is_rejected_before_request():
    session = resolver.ResolverSession()
    with pytest.raises(resolver.Halt):
        session.request(
            "GET",
            resolver.ENDPOINT,
            params=resolver.lookup_params(0),
            timeout=(5, 12),
            allow_redirects=True,
        )
    assert session.requests_count == session.sends_count == 0
    session.close()


def test_query_contract_matches_stock_api(approved, monkeypatch):
    calls = responder(monkeypatch)
    result, _ = resolve(approved)
    assert result["fixture_ready"]
    for index, (request, _) in enumerate(calls):
        captured = []

        class StockTransport:
            def request_json(self, method, url, _captured=captured, _index=index, **kwargs):
                _captured.append((method, url, kwargs))
                return payload(_index)

        stock = RzdApi(resolver.Config(**approved["config"]), transport=StockTransport())
        stock.find_stations(query=resolver.CITIES[index], transport_type="rail", group_results=True)
        method, url, kwargs = captured[0]
        prepared = requests.Request(method, url, params=kwargs["params"]).prepare()
        assert prepared.url == request.url and prepared.body == request.body


def test_real_localhost_https_gets_and_private_builder(approved, monkeypatch, loopback):
    from urllib.parse import parse_qs, urlsplit

    base, proxy, ca = loopback
    monkeypatch.setattr(resolver, "ENDPOINT", base + "/api/v1/suggests")
    seen = []

    def respond(handler):
        query = parse_qs(urlsplit(handler.path).query)["Query"][0]
        index = resolver.CITIES.index(query)
        seen.append(handler.command)
        body = json.dumps(payload(index)).encode()
        handler.send_response(200)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)

    monkeypatch.setattr(Origin, "respond", respond)
    approved["config"].update(proxy=proxy, ca_bundle=ca)
    result, private = resolve(approved)
    assert result == resolver.report(2, "none", ready=True)
    assert seen == ["GET", "GET"] and Proxy.connect_count == 2
    assert private["fixture_sha256"] == resolver.canonical_digest(private["fixture"])


@pytest.mark.parametrize("field", ["guard", "version", "ca", "proxy", "legacy_digest"])
def test_preflight_configuration_and_fresh_digest(approved, monkeypatch, field):
    calls = responder(monkeypatch)
    if field == "guard":
        monkeypatch.delenv("RZD_D14_RESOLVER_OK")
    elif field == "version":
        monkeypatch.setattr(resolver.urllib3, "__version__", "unsupported")
    elif field == "ca":
        approved["config"]["ca_bundle"] = None
    elif field == "proxy":
        approved["config"]["proxy"] = "https://127.0.0.1:9"
    else:
        monkeypatch.setenv("RZD_MMP_FIXTURE_SHA256", "legacy-B12B")
    result, private = resolve(approved)
    if field == "legacy_digest":
        assert result["fixture_ready"] and len(calls) == 2
        assert private["fixture_sha256"] != "legacy-B12B"
        assert os.environ["RZD_MMP_FIXTURE_SHA256"] == "legacy-B12B"
    else:
        assert result["guard_verdict"] == "STOP" and calls == [] and private is None


@pytest.mark.skipif(sys.platform != "linux", reason="POSIX private publication cleanup")
def test_failed_publication_removes_only_new_task_file(tmp_path, monkeypatch):
    directory = tmp_path / "private"
    directory.mkdir(mode=0o700)
    path = directory / "fixture.json"
    settings = {
        "proxy": "http://127.0.0.1:9",
        "ca_bundle": "/synthetic",
        "user_agent": None,
        "referer": None,
    }
    private = resolver.make_handoff(settings, [station(0), station(1)])

    def fail(*args):
        raise OSError("SECRET_STORAGE_PATH")

    monkeypatch.setattr(resolver.os, "fsync", fail)
    with pytest.raises(OSError):
        resolver.publish(str(path), private)
    assert not path.exists()


def test_cli_invalid_input_has_only_fixed_stop_output(tmp_path):
    source = tmp_path / "input.json"
    source.write_text('{"SECRET":"must-not-print"}')
    source.chmod(0o600)
    with source.open("rb") as stream:
        result = subprocess.run(
            [sys.executable, "-B", "-m", "scripts.mmp_d14_resolver"],
            stdin=stream,
            capture_output=True,
            timeout=10,
        )
    assert result.returncode == 2 and result.stderr == b"" and b"SECRET" not in result.stdout
    assert json.loads(result.stdout)["POST_SENDS"] == 0
