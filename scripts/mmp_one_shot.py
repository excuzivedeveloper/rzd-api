"""Opt-in disposable process only. No live authority is conferred by this file.

Invoke as a module from a clean, independently reviewed checkout. Input is a
protected stdin JSON object, never command-line credentials. See SERVER_TESTING.
The supervisor discards both OS output descriptors before spawning the worker.
Only a validated report is written to its saved output descriptor.
"""

from __future__ import annotations

import hashlib
import http.client
import json
import logging
import multiprocessing as mp
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlsplit

import requests
import urllib3
from requests.adapters import HTTPAdapter
from requests.utils import get_environ_proxies, select_proxy

from rzd_api.config import Config
from rzd_api.query import RzdTransport
from scripts.mmp_stage_trace import StageTrace, attach_stage_trace

ENDPOINT = "https://ticket.rzd.ru/apib2b/mmp/onewayRoutesStream/v2"
DEADLINE = 30.0
_STAGES = {"tcp", "proxy_connect", "target_tls", "request_write", "first_headers"}
_BUCKETS = {"unknown", "<200ms", "200-999ms", "1-4s", "5-14s", ">=15s"}


class Stop(Exception):
    """Deliberately carries no diagnostic context."""


def require(condition: Any) -> None:
    if not condition:
        raise Stop


def stopped(attempts: int) -> dict[str, Any]:
    return {
        **StageTrace().summary(),
        "guard_verdict": "STOP",
        "attempt_count": attempts,
        "redirect_count": 0,
    }


def validate_report(report: Any) -> dict[str, Any]:
    require(type(report) is dict and set(report) == set(stopped(0)))
    require(report["proxy_source"] in {"unknown", "explicit", "environment"})
    require(report["status_family"] in {"unknown", "1xx", "2xx", "3xx", "4xx", "5xx"})
    require(
        report["failure_family"]
        in {
            "none",
            "read_timeout",
            "proxy",
            "tls",
            "connect_timeout",
            "http_status",
            "connection",
            "other",
        }
    )
    require(report["guard_verdict"] in {"PASS", "STOP"})
    require(type(report["attempt_count"]) is int and report["attempt_count"] in (0, 1))
    require(type(report["redirect_count"]) is int and report["redirect_count"] == 0)
    for key, allowed in (
        ("stage_states", {"unknown", "done", "failed", "timeout", "received"}),
        ("stage_elapsed", _BUCKETS),
    ):
        require(type(report[key]) is dict and set(report[key]) == _STAGES)
        require(all(type(v) is str and v in allowed for v in report[key].values()))
    return report


class OneShotSession(requests.Session):
    def __init__(self) -> None:
        super().__init__()
        self.request_count = 0
        self.send_count = 0

    def request(self, method: str, url: str, **kwargs: Any) -> requests.Response:  # type: ignore[override]
        require(self.request_count == 0 and method == "POST" and url == ENDPOINT)
        require(kwargs.get("allow_redirects", False) is False)
        require(kwargs.get("timeout") == (5.0, 12.0))
        self.request_count += 1
        kwargs["allow_redirects"] = False
        return super().request(method, url, **kwargs)

    def send(self, request: requests.PreparedRequest, **kwargs: Any) -> requests.Response:
        require(self.request_count == 1 and self.send_count == 0)
        require(kwargs.get("allow_redirects") is False)
        require(request.method == "POST" and request.url == ENDPOINT)
        require(not request.hooks.get("response"))
        self.send_count += 1
        return super().send(request, **kwargs)


def preflight(data: Any) -> tuple[Config, dict[str, Any]]:
    require(requests.__version__ == "2.34.2" and urllib3.__version__ == "2.8.0")
    require(
        all(
            os.getenv(k) == "1"
            for k in (
                "RZD_LIVE_TEST",
                "RZD_MMP_PREFLIGHT_OK",
                "RZD_MMP_BUDGET_OK",
            )
        )
    )
    require(type(data) is dict and set(data) == {"config", "fixture"})
    settings, fixture = data["config"], data["fixture"]
    require(
        type(settings) is dict
        and set(settings)
        == {
            "proxy",
            "ca_bundle",
            "user_agent",
            "referer",
        }
    )
    require(type(fixture) is dict and set(fixture) == {"body"})
    require(type(fixture["body"]) is dict)
    # The approved, nonpersonal B12B body is reused verbatim. No lookup or guess.
    digest = hashlib.sha256(
        json.dumps(fixture, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    require(os.getenv("RZD_MMP_FIXTURE_SHA256") == digest)
    proxy = settings["proxy"]
    require(isinstance(proxy, str))
    parsed = urlsplit(proxy)
    require(parsed.scheme == "http" and parsed.hostname and parsed.port)
    require(not parsed.path and not parsed.query and not parsed.fragment)
    # Stop on ANY ambient proxy/CA routing ambiguity, including platform proxy
    # discovery. Do not clear variables or turn trust_env off to make it work.
    require(
        not any(
            value
            for key, value in os.environ.items()
            if key.lower()
            in {
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
        )
    )
    require(not get_environ_proxies(ENDPOINT))
    require(not os.getenv("RZD_CA_BUNDLE") or os.getenv("RZD_CA_BUNDLE") == settings["ca_bundle"])
    config = Config(
        **settings, language="ru", retry_total=0, connect_timeout=5.0, read_timeout=12.0
    )
    return config, fixture["body"]


def source_gate() -> None:
    expected = os.getenv("RZD_MMP_REVIEWED_SHA", "")
    require(re.fullmatch(r"[0-9a-f]{40}", expected))
    root = Path(__file__).resolve().parents[1]
    for args, expected_output in (
        (["rev-parse", "HEAD"], expected),
        (["status", "--porcelain", "--untracked-files=normal"], ""),
    ):
        result = subprocess.run(
            ["git", "-C", str(root), *args], capture_output=True, timeout=3, check=True
        )
        require(result.stdout.decode().strip() == expected_output)
    require(Path(sys.prefix) != Path(sys.base_prefix))
    import rzd_api

    require(Path(rzd_api.__file__).resolve().parent == root / "rzd_api")


def run_once(data: Any, attempt_counter: Any = None) -> dict[str, Any]:
    """Worker core; call only through the supervisor for deadline/output safety."""
    trace = StageTrace()
    attempts = 0
    verdict = "STOP"
    session = None
    try:
        config, body = preflight(data)
        session = OneShotSession()
        transport = RzdTransport(config, session=session)
        stages: set[str] = set()

        def before_stage(stage: str) -> None:
            require(stage not in stages)
            stages.add(stage)

        trace = attach_stage_trace(transport, before_stage=before_stage)
        adapter = cast(HTTPAdapter, session.get_adapter(ENDPOINT))
        original_send = adapter.send

        def guarded_send(request: requests.PreparedRequest, **kwargs: Any) -> requests.Response:
            nonlocal attempts
            require(attempts == 0 and session is not None and session.send_count == 1)
            require(request.method == "POST" and request.url == ENDPOINT)
            retry = adapter.max_retries
            require(all(getattr(retry, k) == 0 for k in ("total", "connect", "read", "status")))
            require(session.trust_env is True and kwargs.get("timeout") == (5.0, 12.0))
            require(kwargs.get("verify") == (config.ca_bundle or True))
            require(kwargs.get("cert") is None and kwargs.get("stream") is False)
            require(select_proxy(ENDPOINT, kwargs.get("proxies") or {}) == config.proxy)
            require(not get_environ_proxies(ENDPOINT))
            require(request.headers.get("Cookie") == "LANG_SITE=ru")
            require("Authorization" not in request.headers)
            attempts = 1
            if attempt_counter is not None:
                attempt_counter.value = 1  # before entering the network library
            return original_send(request, **kwargs)

        adapter.send = guarded_send  # type: ignore[method-assign, assignment]
        try:
            transport.request_json(
                "POST", ENDPOINT, json_body=body, headers={"Cookie": "LANG_SITE=ru"}
            )
        except Exception as error:
            trace.finish(error)
            if isinstance(error, Stop):
                raise
        else:
            trace.finish()
        require(attempts == session.request_count == session.send_count == 1)
        require(trace.proxy_source == "explicit")
        require(trace.status_family != "3xx")
        require(trace.failure_family != "other")
        verdict = "PASS"
    except BaseException:
        verdict = "STOP"
    finally:
        if session is not None:
            try:
                session.close()
            except BaseException:
                verdict = "STOP"
    report = {
        **trace.summary(),
        "guard_verdict": verdict,
        "attempt_count": attempts,
        "redirect_count": 0,
    }
    try:
        return validate_report(report)
    except BaseException:
        return stopped(attempts)


def silence() -> None:
    logging.disable(sys.maxsize)
    http.client.HTTPConnection.debuglevel = 0
    null = os.open(os.devnull, os.O_WRONLY)
    try:
        os.dup2(null, 1)
        os.dup2(null, 2)
    finally:
        os.close(null)


def worker(data: Any, connection: Any, counter: Any) -> None:
    silence()
    report = stopped(0)
    try:
        source_gate()
        report = run_once(data, counter)
    except BaseException:
        report = stopped(counter.value)
    try:
        connection.send_bytes(json.dumps(validate_report(report)).encode())
    finally:
        connection.close()


def supervise(data: Any, *, target: Any = worker, deadline: float = DEADLINE) -> dict[str, Any]:
    """Independent wall clock; worker cannot extend it with trickled body bytes."""
    started = time.monotonic()
    context = mp.get_context("spawn")
    receive, send = context.Pipe(duplex=False)
    counter = context.Value("i", 0, lock=False)
    process = context.Process(target=target, args=(data, send, counter), daemon=True)
    report = stopped(0)
    try:
        process.start()
        send.close()
        process.join(max(0, deadline - (time.monotonic() - started)))
        if process.is_alive():
            process.kill()
            process.join(1)
            return stopped(counter.value)
        require(process.exitcode == 0 and receive.poll())
        report = validate_report(json.loads(receive.recv_bytes(4096)))
        require(report["attempt_count"] == counter.value)
        return report
    except BaseException:
        return stopped(counter.value)
    finally:
        if process.is_alive():
            process.kill()
            process.join(1)
        receive.close()
        send.close()


def main() -> int:
    output = os.dup(1)
    silence()
    report = stopped(0)
    try:
        require(len(sys.argv) == 1)
        # Input must be a regular protected file; no unbounded pipe/terminal read.
        import stat

        require(stat.S_ISREG(os.fstat(0).st_mode) and os.fstat(0).st_size <= 16384)
        data = json.loads(sys.stdin.read(16385))
        report = supervise(data)
    except BaseException:
        pass
    try:
        os.write(output, (json.dumps(validate_report(report), sort_keys=True) + "\n").encode())
    finally:
        os.close(output)
    return 0 if report["guard_verdict"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
