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
