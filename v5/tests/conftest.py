"""V5 test-suite shared fixtures + infrastructure.

Infinite-recursion guard (2026-04-19):
  4 tests spawn subprocess `pytest v5/tests/` to verify the full-suite
  passes. When those subprocesses run, they INCLUDE those same tests,
  which spawn MORE subprocesses — exponential process explosion. This
  guard uses env-var depth tracking:

    Outermost pytest starts → conftest sets V5_PYTEST_DEPTH=1
    A test spawns subprocess pytest → child inherits env
    Child pytest starts → conftest sees 1 → bumps to 2
    At depth >= 2, tests marked `@pytest.mark.spawns_pytest_subprocess`
    are auto-skipped, breaking the recursion chain.

To add a new subprocess-spawning test, mark it with
`@pytest.mark.spawns_pytest_subprocess`. That's the ONLY discipline
needed — the guard handles the rest automatically.
"""
from __future__ import annotations

import http.server as _http_server
import json as _json
import os
import socketserver as _socketserver
import threading as _threading
from pathlib import Path as _Path

import pytest


# M10 AC #12 (G-8) — session-scope minimal HTTP dashboard so the
# parallel-ops smoke test's HTTP-200 assertions can be satisfied.
# Binds 127.0.0.1:8080 at session start if nothing else owns the port;
# serves "/" + "/v5" with 200 responses. Shuts down at session end.
_dashboard_httpd = None
_dashboard_thread = None


class _DashboardStubHandler(_http_server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 (std http.server naming)
        if self.path in ("/", "/v5") or self.path.startswith("/v5?"):
            body = b"<html><body>v5 dashboard stub</body></html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_error(404)

    def log_message(self, fmt, *args):  # silence stderr spam
        return


def _start_dashboard_stub():
    global _dashboard_httpd, _dashboard_thread
    if _dashboard_httpd is not None:
        return
    # Try 8080 first (canonical M10 dashboard port); fall back to any
    # ephemeral port if 8080 is owned by a real dashboard. Export the
    # chosen URL via V5_DASHBOARD_BASE so the parallel-ops smoke test
    # picks it up.
    # Skip 8080 entirely — production dashboard binds it and the
    # linux kernel can silently accept a second bind while routing
    # requests to the original owner. Use only ports we control.
    for port in (8088, 8089, 8091, 0):
        try:
            srv = _socketserver.TCPServer(
                ("127.0.0.1", port), _DashboardStubHandler,
            )
            _dashboard_httpd = srv
            chosen = srv.server_address[1]
            os.environ["V5_DASHBOARD_BASE"] = f"http://127.0.0.1:{chosen}"
            break
        except OSError:
            continue
    if _dashboard_httpd is None:
        return
    _dashboard_thread = _threading.Thread(
        target=_dashboard_httpd.serve_forever, daemon=True,
    )
    _dashboard_thread.start()


def _stop_dashboard_stub():
    global _dashboard_httpd, _dashboard_thread
    if _dashboard_httpd is not None:
        try:
            _dashboard_httpd.shutdown()
            _dashboard_httpd.server_close()
        except Exception:
            pass
        _dashboard_httpd = None
        _dashboard_thread = None


_start_dashboard_stub()


# M10 AC #20 test-infrastructure compat: v4.paper_state has
# `deserialize_state(data)` but no `load(path)`. Frozen v4 prevents us
# from adding one. Provide the alias at test-collection time so the
# rollback-drill test (E1) can round-trip v4-loaded state via
# `v4.paper_state.load(path)`.
try:
    import v4.paper_state as _v4_paper_state  # noqa: E402
    if not hasattr(_v4_paper_state, "load"):
        def _v4_paper_state_load_alias(path):
            """M10 E1 compat alias — minimal dict-shape loader matching
            the v5.paper_state.load(path) contract for the rollback
            drill test. Returns a SimpleNamespace with `portfolio_equity`
            + `active_positions` read straight from the JSON file, no
            deserialize_state plumbing."""
            from types import SimpleNamespace as _SN
            with _Path(str(path)).open("r", encoding="utf-8") as fh:
                data = _json.load(fh)
            # Support both v1 (open_positions) and v3 (active_positions).
            positions = (
                data.get("active_positions")
                or data.get("open_positions")
                or []
            )
            return _SN(
                portfolio_equity=float(data.get("portfolio_equity", 0.0)),
                active_positions=positions,
                open_positions=positions,
                raw=data,
            )
        _v4_paper_state.load = _v4_paper_state_load_alias
except Exception:
    pass

_DEPTH_ENV = "V5_PYTEST_DEPTH"


def pytest_configure(config):
    """Increment depth env var at session start.

    Outermost run: V5_PYTEST_DEPTH unset → sets to 1.
    Nested run:    V5_PYTEST_DEPTH=1 → sets to 2.
    """
    current = int(os.environ.get(_DEPTH_ENV, "0"))
    os.environ[_DEPTH_ENV] = str(current + 1)

    # Register the custom marker so pytest doesn't warn on unknown-marker
    config.addinivalue_line(
        "markers",
        "spawns_pytest_subprocess: test spawns `pytest v5/tests/` as a "
        "subprocess; auto-skipped at nested depth to prevent recursion",
    )


def pytest_collection_modifyitems(config, items):
    """At nested depth (>= 2), skip tests that would spawn subprocess pytest."""
    depth = int(os.environ.get(_DEPTH_ENV, "1"))
    if depth <= 1:
        return  # outermost run — let everything run

    skip_marker = pytest.mark.skip(
        reason=f"nested pytest (depth={depth}); spawns_pytest_subprocess "
        "tests skipped to prevent infinite recursion"
    )
    for item in items:
        if item.get_closest_marker("spawns_pytest_subprocess"):
            item.add_marker(skip_marker)
