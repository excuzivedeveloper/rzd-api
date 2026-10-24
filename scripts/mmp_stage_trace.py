"""Opt-in, one-shot MMP transport stage probe for a separate diagnostic process.

This module never sends a request by itself. It records only stage names, coarse
monotonic time buckets and status classes. It is limited to the validated
Requests/urllib3 pair because it uses per-adapter urllib3 pool subclasses.
"""

from __future__ import annotations

import ssl
import time
from collections.abc import Mapping
from typing import Any

import requests
import urllib3
from requests.adapters import HTTPAdapter
from requests.utils import get_environ_proxies, select_proxy
from urllib3.connection import HTTPSConnection
from urllib3.connectionpool import HTTPSConnectionPool
from urllib3.exceptions import ReadTimeoutError

from rzd_api.query import RzdTransport

_SUPPORTED_REQUESTS = "2.34.2"
_SUPPORTED_URLLIB3 = "2.8.0"
_STAGES = (
    "tcp",
    "proxy_connect",
    "target_tls",
    "request_write",
    "first_headers",
)


def _bucket(seconds: float) -> str:
    if seconds < 0.2:
        return "<200ms"
    if seconds < 1.0:
        return "200-999ms"
    if seconds < 5.0:
        return "1-4s"
    if seconds < 15.0:
        return "5-14s"
    return ">=15s"


class StageTrace:
    """Allowlisted in-memory outcome for exactly one isolated client operation."""

    def __init__(self) -> None:
        self._started = time.monotonic()
        self._events: list[tuple[str, str, str]] = []
        self._using_proxy = False
        self.proxy_source = "unknown"
        self.status_family = "unknown"
        self.failure_family = "none"

    def _mark(self, stage: str, state: str) -> None:
        self._events.append((stage, state, _bucket(time.monotonic() - self._started)))

    def finish(self, error: BaseException | None = None) -> None:
        """Call after the entire request, including response-body consumption."""
        if error is None:
            return
        seen: set[int] = set()
        current: BaseException | None = error
        names: set[str] = set()
        while current is not None and id(current) not in seen:
            seen.add(id(current))
            names.add(type(current).__name__)
            reason = getattr(current, "reason", None)
            current = (
                reason
                if isinstance(reason, BaseException)
                else current.__cause__ or current.__context__
            )
        if "ReadTimeoutError" in names or "ReadTimeout" in names:
            self.failure_family = "read_timeout"
        elif "ProxyError" in names:
            self.failure_family = "proxy"
        elif "SSLError" in names or isinstance(error, ssl.SSLError):
            self.failure_family = "tls"
        elif "ConnectTimeout" in names:
            self.failure_family = "connect_timeout"
        elif "RzdHTTPError" in names:
            self.failure_family = "http_status"
        elif "ConnectionError" in names:
            self.failure_family = "connection"
        else:
            self.failure_family = "other"

    def summary(self) -> dict[str, Any]:
        states = {stage: "unknown" for stage in _STAGES}
        elapsed = {stage: "unknown" for stage in _STAGES}
        for stage, state, bucket in self._events:
            states[stage] = state
            elapsed[stage] = bucket
        return {
            "proxy_source": self.proxy_source,
            "stage_states": states,
            "stage_elapsed": elapsed,
            "status_family": self.status_family,
            "failure_family": self.failure_family,
        }


def attach_stage_trace(transport: RzdTransport) -> StageTrace:
    """Replace only this transport's HTTPS adapter, preserving retry/pool settings.

    This relies on urllib3 connection-pool internals and deliberately refuses
    unvalidated versions. It must never be attached to a long-running service.
    """
    if requests.__version__ != _SUPPORTED_REQUESTS or urllib3.__version__ != _SUPPORTED_URLLIB3:
        raise RuntimeError("Unsupported Requests/urllib3 diagnostic version")
    session = transport.session
    if not isinstance(session, requests.Session):
        raise TypeError("Stage tracing requires a Requests Session")
    original = session.get_adapter("https://")
    if not isinstance(original, HTTPAdapter):
        raise TypeError("Stage tracing requires the standard HTTPS adapter")
    if getattr(original, "_mmp_stage_trace", False):
        raise RuntimeError("Stage trace is already attached")

    trace = StageTrace()

    class Connection(HTTPSConnection):
        def _new_conn(self) -> Any:
            try:
                result = super()._new_conn()
            except Exception:
                trace._mark("tcp", "failed")
                raise
            trace._mark("tcp", "done")
            return result

        def _tunnel(self) -> None:
            try:
                super()._tunnel()
            except Exception:
                trace._mark("proxy_connect", "failed")
                raise
            trace._mark("proxy_connect", "done")

        def connect(self) -> None:
            try:
                super().connect()
            except Exception:
                tcp_failed = any(
                    event[0] == "tcp" and event[1] == "failed" for event in trace._events
                )
                tunnel_done = any(
                    event[0] == "proxy_connect" and event[1] == "done" for event in trace._events
                )
                if not tcp_failed and (not trace._using_proxy or tunnel_done):
                    trace._mark("target_tls", "failed")
                raise
            trace._mark("target_tls", "done")

        def request(self, *args: Any, **kwargs: Any) -> None:
            try:
                super().request(*args, **kwargs)
            except Exception:
                trace._mark("request_write", "failed")
                raise
            trace._mark("request_write", "done")

        def getresponse(self) -> Any:
            try:
                result = super().getresponse()
            except (ReadTimeoutError, TimeoutError):
                trace._mark("first_headers", "timeout")
                raise
            except Exception:
                trace._mark("first_headers", "unknown")
                raise
            trace._mark("first_headers", "received")
            return result

    class Pool(HTTPSConnectionPool):
        ConnectionCls = Connection

    class Adapter(HTTPAdapter):
        _mmp_stage_trace = True

        def init_poolmanager(self, *args: Any, **kwargs: Any) -> None:
            super().init_poolmanager(*args, **kwargs)
            self.poolmanager.pool_classes_by_scheme = {
                **self.poolmanager.pool_classes_by_scheme,
                "https": Pool,
            }

        def proxy_manager_for(self, proxy: str, **proxy_kwargs: Any) -> Any:
            manager = super().proxy_manager_for(proxy, **proxy_kwargs)
            manager.pool_classes_by_scheme = {**manager.pool_classes_by_scheme, "https": Pool}
            return manager

        def send(
            self,
            request: requests.PreparedRequest,
            stream: bool = False,
            timeout: float | tuple[float, float] | tuple[float, None] | None = None,
            verify: bool | str = True,
            cert: bytes | str | tuple[bytes | str, bytes | str] | None = None,
            proxies: Mapping[str, str] | None = None,
        ) -> requests.Response:
            effective = select_proxy(request.url or "", proxies or {})
            trace._using_proxy = bool(effective)
            explicit = transport.config.proxy
            environment = (
                select_proxy(request.url or "", get_environ_proxies(request.url or ""))
                if session.trust_env
                else None
            )
            if effective and effective == explicit and environment != effective:
                trace.proxy_source = "explicit"
            elif effective and effective == environment and effective != explicit:
                trace.proxy_source = "environment"
            else:
                trace.proxy_source = "unknown"
            response = super().send(
                request,
                stream=stream,
                timeout=timeout,
                verify=verify,
                cert=cert,
                proxies=proxies,
            )
            trace.status_family = f"{response.status_code // 100}xx"
            return response

    session.mount(
        "https://",
        Adapter(
            pool_connections=original._pool_connections,  # type: ignore[attr-defined]
            pool_maxsize=original._pool_maxsize,  # type: ignore[attr-defined]
            max_retries=original.max_retries,
            pool_block=original._pool_block,  # type: ignore[attr-defined]
        ),
    )
    return trace
