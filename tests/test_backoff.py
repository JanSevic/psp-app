"""Přizpůsobení pauzy mezi požadavky, když server odmítá nebo nestíhá."""

import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from pipeline import config, fetch


@pytest.fixture(autouse=True)
def reset(monkeypatch):
    monkeypatch.setattr(config, "REQUEST_DELAY", 0.0)
    monkeypatch.setattr(config, "MAX_DELAY", 4.0)
    monkeypatch.setattr(config, "BACKOFF_RECOVER", 3)
    monkeypatch.setattr(fetch, "_delay", None)
    monkeypatch.setattr(fetch, "_ok_streak", 0)
    monkeypatch.setattr(fetch, "_fail_streak", 0)
    monkeypatch.setattr(fetch, "_outage_until", None)
    monkeypatch.setattr(fetch, "stats", {"stale": 0, "skipped_outage": 0})
    monkeypatch.setattr(fetch.time, "sleep", lambda s: None)  # testy nečekají


def test_backoff_doubles_up_to_max_and_recovers():
    fetch._backoff("test")
    assert fetch.current_delay() == 1.0
    for _ in range(5):
        fetch._backoff("test")
    assert fetch.current_delay() == 4.0
    for _ in range(3):
        fetch._success()
    assert fetch.current_delay() == 2.0
    for _ in range(9):
        fetch._success()
    assert fetch.current_delay() == 0.0


def test_retry_after_respected():
    fetch._backoff("HTTP 429", retry_after=3.5)
    assert fetch.current_delay() == 3.5


def test_fetch_retries_after_503(tmp_path, monkeypatch):
    hits = {"n": 0}

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            hits["n"] += 1
            if hits["n"] == 1:
                self.send_response(503)
                self.send_header("Retry-After", "2")
                self.end_headers()
                return
            body = b"ok"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    srv = ThreadingHTTPServer(("127.0.0.1", port), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    monkeypatch.setenv("no_proxy", "127.0.0.1")
    try:
        path, changed = fetch.fetch(f"http://127.0.0.1:{port}/x", tmp_path / "x")
    finally:
        srv.shutdown()
    assert path.read_bytes() == b"ok" and changed
    assert hits["n"] == 2
    assert fetch.current_delay() == 2.0


def test_outage_breaker_uses_cache_and_stops_requests(tmp_path, monkeypatch):
    """psp.cz nenavazuje spojení: po OUTAGE_FAILS chybách už se nechodí na server."""
    monkeypatch.setattr(config, "OUTAGE_FAILS", 3)
    calls = {"n": 0}

    def dead(*a, **kw):
        calls["n"] += 1
        raise fetch.requests.ConnectTimeout("timed out")

    monkeypatch.setattr(fetch.requests.Session, "get", dead)
    cached = []
    for i in range(10):
        f = tmp_path / f"p{i}.htm"
        f.write_text("stará kopie")
        cached.append(f)
    for f in cached:
        path, changed = fetch.fetch(f"http://psp.invalid/{f.name}", f)
        assert path == f and not changed and fetch.last_was_stale()
    assert calls["n"] == 3  # 3 neúspěšné pokusy, pak jistič
    assert fetch.in_outage()
    with pytest.raises(RuntimeError):
        fetch.fetch("http://psp.invalid/novy.htm", tmp_path / "novy.htm")
    assert calls["n"] == 3
    assert fetch.stats["stale"] == 10


def test_outage_probe_recovers(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "OUTAGE_FAILS", 1)
    monkeypatch.setattr(config, "OUTAGE_PROBE_MINUTES", 0)  # zkušební požadavek hned

    class R:
        status_code = 200
        headers = {}

        def raise_for_status(self):
            pass

        def iter_content(self, n):
            yield b"nove"

    state = {"up": False}

    def get(*a, **kw):
        if not state["up"]:
            raise fetch.requests.ConnectTimeout("timed out")
        return R()

    monkeypatch.setattr(fetch.requests.Session, "get", get)
    f = tmp_path / "x.htm"
    f.write_text("stara")
    fetch.fetch("http://psp.invalid/x", f)
    assert fetch._outage_until is not None
    state["up"] = True
    path, changed = fetch.fetch("http://psp.invalid/x", f)
    assert changed and f.read_text() == "nove" and fetch._outage_until is None
