"""Offline safety contract; no provider traffic is permitted by these tests."""

import hashlib
import json
import os
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest
import requests
import urllib3
from requests.adapters import HTTPAdapter
from test_mmp_stage_trace import Origin, Proxy
from test_mmp_stage_trace import loopback as _loopback

from rzd_api.config import Config
from rzd_api.query import RzdTransport
from scripts import mmp_one_shot as probe

loopback = _loopback


def test_request_guard_refuses_default_redirects_and_second_request():
    session = probe.OneShotSession()
    with pytest.raises(probe.Stop):
        session.request("GET", probe.ENDPOINT)
    assert session.request_count == 0


def test_output_validator_rejects_raw_exception():
    report = probe.stopped(0)
    report["failure_family"] = "secret http://credentials.invalid"
    with pytest.raises(probe.Stop):
        probe.validate_report(report)


def test_unapproved_invocation_has_zero_attempts(monkeypatch):
    monkeypatch.delenv("RZD_LIVE_TEST", raising=False)
    monkeypatch.setattr(requests.Session, "request", lambda *a, **k: pytest.fail("network"))
    assert probe.run_once({}) == probe.stopped(0)


@pytest.fixture
def approved(monkeypatch):
    for key in list(os.environ):
        if key.lower() in {
            "http_proxy",
            "https_proxy",
            "all_proxy",
            "no_proxy",
            "requests_ca_bundle",
            "curl_ca_bundle",
            "ssl_cert_file",
            "ssl_cert_dir",
            "rzd_ca_bundle",
        }:
            monkeypatch.delenv(key)
    for key in ("RZD_LIVE_TEST", "RZD_MMP_PREFLIGHT_OK", "RZD_MMP_BUDGET_OK"):
        monkeypatch.setenv(key, "1")
    data = {
        "config": {
            "proxy": "http://127.0.0.1:9",
            "ca_bundle": None,
            "user_agent": None,
            "referer": None,
        },
        "fixture": {"body": {"synthetic": True}},
    }
    digest = hashlib.sha256(
        json.dumps(data["fixture"], sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    monkeypatch.setenv("RZD_MMP_FIXTURE_SHA256", digest)
    return data


def response(request, status=200):
    result = requests.Response()
    result.status_code = status
    result._content = b'{"synthetic":true}'
    result.request = request
    result.url = request.url
    if status == 302:
        result.headers["Location"] = "https://must-never-be-contacted.invalid/secret"
    return result


@pytest.mark.parametrize("status,verdict", [(200, "PASS"), (302, "STOP"), (500, "PASS")])
def test_status_no_retry_no_redirect(approved, monkeypatch, status, verdict):
    calls = []

    def fake_send(self, request, **kwargs):
        calls.append((request, kwargs))
        assert all(
            getattr(self.max_retries, key) == 0 for key in ("total", "connect", "read", "status")
        )
        return response(request, status)

    monkeypatch.setattr(HTTPAdapter, "send", fake_send)
    report = probe.run_once(approved)
    assert report["guard_verdict"] == verdict
    assert report["attempt_count"] == len(calls) == 1
    assert report["redirect_count"] == 0
    assert report["status_family"] == f"{status // 100}xx"
    request, kwargs = calls[0]
    assert request.method == "POST" and request.url == probe.ENDPOINT
    assert request.headers["Cookie"] == "LANG_SITE=ru"
    assert kwargs["verify"] is True and kwargs["timeout"] == (5.0, 12.0)


@pytest.mark.parametrize(
    "error,family,verdict",
    [
        (requests.ConnectTimeout("SECRET"), "connect_timeout", "PASS"),
        (requests.exceptions.SSLError("SECRET"), "tls", "PASS"),
        (requests.ReadTimeout("SECRET"), "read_timeout", "PASS"),
        (requests.ConnectionError("SECRET"), "connection", "PASS"),
        (ValueError("SECRET"), "other", "STOP"),
    ],
)
def test_errors_are_categorized_without_text(approved, monkeypatch, error, family, verdict):
    count = 0

    def fail(*args, **kwargs):
        nonlocal count
        count += 1
        raise error

    monkeypatch.setattr(HTTPAdapter, "send", fail)
    report = probe.run_once(approved)
    assert count == report["attempt_count"] == 1
    assert report["failure_family"] == family
    assert report["guard_verdict"] == verdict
    assert "SECRET" not in json.dumps(report)


@pytest.mark.parametrize(
    "key",
    [
        "HTTPS_PROXY",
        "http_proxy",
        "ALL_PROXY",
        "NO_PROXY",
        "REQUESTS_CA_BUNDLE",
        "CURL_CA_BUNDLE",
        "SSL_CERT_FILE",
    ],
)
def test_environment_overrides_stop_before_network(approved, monkeypatch, key):
    monkeypatch.setenv(key, "SECRET")
    monkeypatch.setattr(HTTPAdapter, "send", lambda *a, **k: pytest.fail("network"))
    assert probe.run_once(approved) == probe.stopped(0)


@pytest.mark.parametrize("change", ["requests", "urllib3", "proxy", "fixture", "timeout"])
def test_config_version_and_fixture_mismatch(approved, monkeypatch, change):
    if change == "requests":
        monkeypatch.setattr(requests, "__version__", "0")
    elif change == "urllib3":
        monkeypatch.setattr(urllib3, "__version__", "0")
    elif change == "proxy":
        approved["config"]["proxy"] = "socks5://127.0.0.1:9"
    elif change == "fixture":
        approved["fixture"]["body"]["changed"] = True
    else:
        approved["config"]["read_timeout"] = 99
    monkeypatch.setattr(HTTPAdapter, "send", lambda *a, **k: pytest.fail("network"))
    assert probe.run_once(approved) == probe.stopped(0)


@pytest.mark.parametrize("boundary", ["request", "send", "adapter", "connection"])
def test_second_attempt_is_blocked_before_second_network_send(approved, monkeypatch, boundary):
    calls = 0

    def fake_send(adapter, request, **kwargs):
        nonlocal calls
        calls += 1
        return response(request)

    monkeypatch.setattr(HTTPAdapter, "send", fake_send)
    original = RzdTransport.request_json

    def duplicate(transport, *args, **kwargs):
        result = original(transport, *args, **kwargs)
        session = transport.session
        if boundary == "request":
            original(transport, *args, **kwargs)
        elif boundary == "send":
            session.send(requests.Request("POST", probe.ENDPOINT).prepare(), allow_redirects=False)
        elif boundary == "adapter":
            session.get_adapter(probe.ENDPOINT).send(
                requests.Request("POST", probe.ENDPOINT).prepare()
            )
        else:
            adapter = session.get_adapter(probe.ENDPOINT)
            cls = adapter.poolmanager.pool_classes_by_scheme["https"].ConnectionCls
            conn = cls("localhost")
            # First low-level entry is allowed, then block BEFORE super().request.
            with pytest.raises(probe.Stop):
                conn.request("POST", "/")
            conn.request("POST", "/")
        return result

    if boundary == "connection":
        # Never let this guard test resolve a name or open even a loopback socket.
        monkeypatch.setattr(
            urllib3.connection.HTTPSConnection,
            "request",
            lambda *a, **k: (_ for _ in ()).throw(probe.Stop()),
        )
    monkeypatch.setattr(RzdTransport, "request_json", duplicate)
    report = probe.run_once(approved)
    assert report["guard_verdict"] == "STOP"
    assert calls == report["attempt_count"] == 1


def test_wrong_destination_and_redirect_true_rejected():
    for method, url, extra in [
        ("GET", probe.ENDPOINT, {}),
        ("POST", probe.ENDPOINT + "?x=1", {}),
        ("POST", probe.ENDPOINT, {"allow_redirects": True}),
    ]:
        session = probe.OneShotSession()
        with pytest.raises(probe.Stop):
            session.request(method, url, timeout=(5.0, 12.0), **extra)
        assert session.request_count == 0
        session.close()


def test_loopback_success_and_exact_stock_request_parity(approved, loopback, monkeypatch):
    base, proxy, ca = loopback
    monkeypatch.setattr(probe, "ENDPOINT", base + "/ok")
    approved["config"].update(proxy=proxy, ca_bundle=ca)
    report = probe.run_once(approved)
    assert report["guard_verdict"] == "PASS"
    assert report["attempt_count"] == Proxy.connect_count == Origin.counts["/ok"] == 1
    traced = Origin.seen["/ok"]
    config = Config(**approved["config"], retry_total=0, read_timeout=12)
    with RzdTransport(config) as stock:
        stock.request_json(
            "POST",
            base + "/stock",
            json_body=approved["fixture"]["body"],
            headers={"Cookie": "LANG_SITE=ru"},
        )
    assert traced == Origin.seen["/stock"]
    assert set(report["stage_states"].values()) <= {"done", "received"}


@pytest.mark.parametrize(
    "path,status,verdict", [("/redirect", "3xx", "STOP"), ("/error500", "5xx", "PASS")]
)
def test_loopback_terminal_http(approved, loopback, monkeypatch, path, status, verdict):
    base, proxy, ca = loopback
    monkeypatch.setattr(probe, "ENDPOINT", base + path)
    approved["config"].update(proxy=proxy, ca_bundle=ca)
    report = probe.run_once(approved)
    assert report["guard_verdict"] == verdict and report["status_family"] == status
    assert report["attempt_count"] == Proxy.connect_count == 1
    assert Origin.counts["/redirected"] == 0


@pytest.mark.parametrize(
    "path,headers", [("/header-stall", "timeout"), ("/body-stall", "received")]
)
def test_loopback_real_read_deadline_and_body_stall(approved, loopback, monkeypatch, path, headers):
    base, proxy, ca = loopback
    Origin.stall_delay = 12.5  # exceed the REAL 12-second read budget, not a fake timer
    Proxy.relay_timeout = 14
    monkeypatch.setattr(probe, "ENDPOINT", base + path)
    approved["config"].update(proxy=proxy, ca_bundle=ca)
    report = probe.run_once(approved)
    assert report["failure_family"] == "read_timeout"
    assert report["stage_states"]["first_headers"] == headers
    assert report["attempt_count"] == Proxy.connect_count == 1


def test_loopback_tls_failure_verification_stays_on(approved, loopback, monkeypatch):
    base, proxy, _ = loopback
    monkeypatch.setattr(probe, "ENDPOINT", base + "/ok")
    approved["config"]["proxy"] = proxy
    report = probe.run_once(approved)
    assert report["failure_family"] == "tls"
    assert report["stage_states"]["target_tls"] == "failed"
    assert report["attempt_count"] == Proxy.connect_count == 1


def test_ssl_key_log_is_rejected_without_file_creation(approved, monkeypatch, tmp_path):
    log = tmp_path / "must-not-exist.log"
    monkeypatch.setenv("SSLKEYLOGFILE", str(log))
    assert probe.run_once(approved) == probe.stopped(0)
    assert not log.exists()


def test_source_gate_requires_reviewed_clean_checkout(monkeypatch):
    monkeypatch.delenv("RZD_MMP_REVIEWED_SHA", raising=False)
    with pytest.raises(probe.Stop):
        probe.source_gate()


def test_cli_invalid_input_outputs_only_safe_report(tmp_path):
    source = tmp_path / "input.json"
    source.write_text('{"secret":"DO_NOT_PRINT"}')
    with source.open("rb") as stream:
        result = subprocess.run(
            [sys.executable, "-m", "scripts.mmp_one_shot"],
            stdin=stream,
            capture_output=True,
            timeout=10,
        )
    assert result.returncode == 2 and result.stderr == b""
    assert json.loads(result.stdout) == probe.stopped(0)


def hanging_worker(data, connection, counter):
    counter.value = 1
    time.sleep(10)


def malformed_worker(data, connection, counter):
    connection.send_bytes(b'{"secret":"NEVER_PRINT"}')


def test_independent_process_deadline_and_malformed_ipc():
    started = time.monotonic()
    report = probe.supervise({}, target=hanging_worker, deadline=3)
    assert time.monotonic() - started < 6
    assert report["guard_verdict"] == "STOP" and report["attempt_count"] == 1
    assert probe.supervise({}, target=malformed_worker) == probe.stopped(0)


@pytest.mark.parametrize("case", ["changed_head", "dirty", "other_checkout"])
def test_source_identity_negative_cases(monkeypatch, case):
    import rzd_api

    monkeypatch.setenv("RZD_MMP_REVIEWED_SHA", "a" * 40)

    def git(args, **kwargs):
        value = b"a" * 40 if "rev-parse" in args else b""
        if case == "changed_head" and "rev-parse" in args:
            value = b"b" * 40
        if case == "dirty" and "status" in args:
            value = b" M scripts/mmp_one_shot.py"
        return SimpleNamespace(stdout=value)

    monkeypatch.setattr(probe.subprocess, "run", git)
    if case == "other_checkout":
        monkeypatch.setattr(rzd_api, "__file__", str(probe.Path(sys.prefix) / "alien.py"))
    with pytest.raises(probe.Stop):
        probe.source_gate()


@pytest.mark.parametrize("stage", ["tcp", "proxy_connect", "request_write"])
def test_low_level_duplicate_stage_callback(approved, monkeypatch, stage):
    attach = probe.attach_stage_trace
    entered = []

    def attach_and_duplicate(transport, *, before_stage):
        trace = attach(transport, before_stage=before_stage)
        before_stage(stage)
        entered.append(stage)
        before_stage(stage)
        pytest.fail("second low-level entry was not refused")
        return trace

    monkeypatch.setattr(probe, "attach_stage_trace", attach_and_duplicate)
    assert probe.run_once(approved) == probe.stopped(0)
    assert entered == [stage]


@pytest.mark.parametrize("fail", [False, True])
def test_worker_discards_stdout_stderr_logging_and_exception_text(fail):
    code = """
import os, logging
from types import SimpleNamespace
from scripts import mmp_one_shot as p
out = os.dup(1)
class Channel:
    def send_bytes(self, b): os.write(out, b)
    def close(self): pass
def noisy(*args):
    print("SECRET_STDOUT", flush=True)
    os.write(2, b"SECRET_STDERR")
    logging.critical("SECRET_LOG")
    if FAIL: raise RuntimeError("SECRET_EXCEPTION")
    return p.stopped(0)
p.source_gate = lambda: None
p.run_once = noisy
p.worker({}, Channel(), SimpleNamespace(value=0))
""".replace("FAIL", repr(fail))
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, timeout=10)
    assert result.returncode == 0 and result.stderr == b""
    assert json.loads(result.stdout) == probe.stopped(0)
    assert b"SECRET" not in result.stdout
