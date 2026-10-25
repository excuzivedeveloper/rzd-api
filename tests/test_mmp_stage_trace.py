"""Local-only HTTPS proxy tests for the opt-in diagnostic adapter."""

from __future__ import annotations

import ipaddress
import select
import socket
import ssl
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from typing import Any, ClassVar

import pytest
import requests
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from rzd_api.config import Config
from rzd_api.exceptions import RzdHTTPError, RzdTransportError
from rzd_api.query import RzdTransport
from scripts.mmp_stage_trace import attach_stage_trace


class Origin(BaseHTTPRequestHandler):
    stall_delay = 0.3
    counts: ClassVar[Counter[str]] = Counter()
    seen: ClassVar[dict[str, tuple[str, bytes, dict[str, str]]]] = {}

    def log_message(self, *_: Any) -> None:
        pass

    def do_GET(self) -> None:
        self.respond()

    def do_POST(self) -> None:
        self.respond()

    def respond(self) -> None:
        type(self).counts[self.path] += 1
        body_in = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        type(self).seen[self.path] = (self.command, body_in, dict(self.headers))
        if self.path == "/header-stall":
            time.sleep(type(self).stall_delay)
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "/redirected")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        response = b'{"synthetic":true}'
        try:
            self.send_response(500 if self.path == "/error500" else 200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            if self.path == "/body-stall":
                self.wfile.write(response[:2])
                self.wfile.flush()
                time.sleep(type(self).stall_delay)
                self.wfile.write(response[2:])
            else:
                self.wfile.write(response)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, ssl.SSLEOFError):
            pass


class Proxy(BaseHTTPRequestHandler):
    connect_count = 0
    fail_connect = False
    relay_timeout = 1

    def log_message(self, *_: Any) -> None:
        pass

    def do_CONNECT(self) -> None:
        type(self).connect_count += 1
        if type(self).fail_connect:
            self.send_error(502)
            return
        host, port_text = self.path.rsplit(":", 1)
        assert host in {"localhost", "127.0.0.1"}
        upstream = socket.create_connection(("127.0.0.1", int(port_text)), timeout=1)
        try:
            self.send_response(200, "Connection Established")
            self.end_headers()
            self.connection.settimeout(1)
            upstream.settimeout(1)
            while True:
                readable, _, _ = select.select(
                    [self.connection, upstream], [], [], type(self).relay_timeout
                )
                if not readable:
                    break
                for channel in readable:
                    chunk = channel.recv(65536)
                    if not chunk:
                        return
                    (upstream if channel is self.connection else self.connection).sendall(chunk)
        except (OSError, TimeoutError):
            pass
        finally:
            upstream.close()


def _certificate(path: Path) -> tuple[Path, Path]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(timezone.utc) - timedelta(days=1))
        .not_valid_after(datetime.now(timezone.utc) + timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
            ),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    cert_path = path / "localhost.crt"
    key_path = path / "localhost.key"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )
    return cert_path, key_path


@pytest.fixture
def loopback(tmp_path: Path):
    Origin.stall_delay = 0.3
    Origin.counts = Counter()
    Origin.seen = {}
    Proxy.connect_count = 0
    Proxy.fail_connect = False
    Proxy.relay_timeout = 1
    cert_path, key_path = _certificate(tmp_path)
    origin = ThreadingHTTPServer(("127.0.0.1", 0), Origin)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certfile=str(cert_path), keyfile=str(key_path))
    origin.socket = context.wrap_socket(origin.socket, server_side=True)
    proxy = ThreadingHTTPServer(("127.0.0.1", 0), Proxy)
    for server in (origin, proxy):
        Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield (
            f"https://localhost:{origin.server_address[1]}",
            f"http://127.0.0.1:{proxy.server_address[1]}",
            str(cert_path),
        )
    finally:
        for server in (origin, proxy):
            server.shutdown()
            server.server_close()


def _transport(proxy: str, ca: str) -> RzdTransport:
    session = requests.Session()
    session.trust_env = False  # deterministic loopback fixture, not production behavior
    return RzdTransport(
        Config(proxy=proxy, ca_bundle=ca, connect_timeout=0.15, read_timeout=0.15, retry_total=0),
        session=session,
    )


@pytest.mark.parametrize(
    ("name", "path", "expected", "failure"),
    [
        (
            "fast500",
            "/error500",
            {
                "tcp": "done",
                "proxy_connect": "done",
                "target_tls": "done",
                "request_write": "done",
                "first_headers": "received",
            },
            "http_status",
        ),
        (
            "header_timeout",
            "/header-stall",
            {
                "tcp": "done",
                "proxy_connect": "done",
                "target_tls": "done",
                "request_write": "done",
                "first_headers": "timeout",
            },
            "read_timeout",
        ),
        (
            "body_stall",
            "/body-stall",
            {
                "tcp": "done",
                "proxy_connect": "done",
                "target_tls": "done",
                "request_write": "done",
                "first_headers": "received",
            },
            "read_timeout",
        ),
        (
            "tls_failure",
            "/ok",
            {
                "tcp": "done",
                "proxy_connect": "done",
                "target_tls": "failed",
                "request_write": "unknown",
                "first_headers": "unknown",
            },
            "tls",
        ),
        (
            "connect_failure",
            "/ok",
            {
                "tcp": "done",
                "proxy_connect": "failed",
                "target_tls": "unknown",
                "request_write": "unknown",
                "first_headers": "unknown",
            },
            "proxy",
        ),
    ],
)
def test_stage_diagnostics_through_https_proxy(
    loopback: tuple[str, str, str],
    name: str,
    path: str,
    expected: dict[str, str],
    failure: str,
) -> None:
    base, proxy, ca = loopback
    Proxy.fail_connect = name == "connect_failure"
    transport = _transport(proxy, ca)
    if name == "tls_failure":
        transport.session.verify = True  # wrong trust store; TLS verification stays enabled
    trace = attach_stage_trace(transport)
    before = Proxy.connect_count
    error: Exception | None = None
    try:
        transport.request_json("POST", base + path, json_body={"synthetic": True})
    except (RzdHTTPError, RzdTransportError) as exc:
        error = exc
    trace.finish(error)
    summary = trace.summary()
    assert error is not None
    assert Proxy.connect_count - before == 1
    assert summary["proxy_source"] == "explicit"
    assert summary["stage_states"] == expected
    assert summary["failure_family"] == failure
    assert set(summary["stage_elapsed"].values()) <= {
        "unknown",
        "<200ms",
        "200-999ms",
        "1-4s",
        "5-14s",
        ">=15s",
    }
    assert summary["status_family"] == (
        "5xx" if name == "fast500" else "2xx" if name == "body_stall" else "unknown"
    )
    transport.session.close()


def test_opt_in_trace_preserves_wire_get_and_redirects(loopback: tuple[str, str, str]) -> None:
    base, proxy, ca = loopback
    captured = []
    for path, traced in (("/stock", False), ("/traced", True)):
        transport = _transport(proxy, ca)
        if traced:
            attach_stage_trace(transport)
        assert transport.request_json(
            "POST", base + path, json_body={"synthetic": True}, headers={"Cookie": "LANG_SITE=ru"}
        ) == {"synthetic": True}
        captured.append(Origin.seen[path])
        transport.session.close()
    keys = {
        "Accept",
        "Accept-Encoding",
        "Connection",
        "Content-Length",
        "Content-Type",
        "Cookie",
        "Referer",
        "User-Agent",
    }
    assert captured[0][0:2] == captured[1][0:2]
    assert {key: captured[0][2].get(key) for key in keys} == {
        key: captured[1][2].get(key) for key in keys
    }

    for traced in (False, True):
        transport = _transport(proxy, ca)
        if traced:
            attach_stage_trace(transport)
        assert transport.request_json("GET", base + "/get") == {"synthetic": True}
        assert "Cookie" not in Origin.seen["/get"][2]
        assert transport.request_json(
            "POST", base + "/redirect", json_body={"synthetic": True}
        ) == {"synthetic": True}
        assert Origin.seen["/redirected"][0] == "GET"
        transport.session.close()


def test_version_guard_rejects_unvalidated_transport(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(requests, "__version__", "0.0.0")
    transport = RzdTransport(Config())
    with pytest.raises(RuntimeError, match="Unsupported Requests/urllib3"):
        attach_stage_trace(transport)
    transport.close()


def test_effective_environment_proxy_is_reported_without_its_address(
    loopback: tuple[str, str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    base, proxy, ca = loopback
    monkeypatch.setenv("HTTPS_PROXY", proxy)
    monkeypatch.setenv("NO_PROXY", "")
    transport = _transport("http://configured-proxy.invalid:9000", ca)
    transport.session.trust_env = True
    trace = attach_stage_trace(transport)
    assert transport.request_json("GET", base + "/environment") == {"synthetic": True}
    assert trace.summary()["proxy_source"] == "environment"
    assert Proxy.connect_count == 1
    transport.session.close()


def test_adapter_preserves_retry_settings() -> None:
    transport = RzdTransport(Config(retry_total=3))
    original = transport.session.get_adapter("https://")
    attach_stage_trace(transport)
    traced = transport.session.get_adapter("https://")
    assert traced.max_retries == original.max_retries
    assert traced._pool_connections == original._pool_connections
    assert traced._pool_maxsize == original._pool_maxsize
    assert traced._pool_block == original._pool_block
    transport.close()
