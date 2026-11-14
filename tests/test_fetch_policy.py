"""Pravidla opakovaného stahování a časový rozpočet (proti napodobenině psp.cz)."""

import os
import socket
import subprocess
import sys
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *a):  # bez výpisu do stderr testů
        pass


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    from tests import fake_psp

    site = tmp_path_factory.mktemp("psp")
    fake_psp.build(site)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    srv = ThreadingHTTPServer(("127.0.0.1", port), partial(QuietHandler, directory=str(site)))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{port}"
    srv.shutdown()


def run_build(base: str, tmp: Path, **extra) -> str:
    env = dict(os.environ)
    env.update(
        PSP_BASE=base,
        PSP_CACHE_DIR=str(tmp / "cache"),
        PSP_STATE_DIR=str(tmp / "state"),
        PSP_DIST_DIR=str(tmp / "dist"),
        PSP_REQUEST_DELAY="0",
        PSP_REFRESH_WEEKDAY="-1",
        NO_PROXY="127.0.0.1,localhost",
        no_proxy="127.0.0.1,localhost",
        **extra,
    )
    p = subprocess.run([sys.executable, "-m", "pipeline", "build"], cwd=ROOT, env=env, check=True,
                       capture_output=True, text=True)
    return p.stderr


def test_closed_period_not_downloaded_again(server, tmp_path):
    first = run_build(server, tmp_path)
    assert "hl-2021ps.zip: aktualizováno" in first
    second = run_build(server, tmp_path)
    # uzavřené období (9.) se bere z cache, aktuální (10.) se stahuje znovu
    assert "hl-2021ps.zip: z cache" in second
    assert "hl-2013ps.zip: z cache" in second
    assert "hl-2025ps.zip: z cache" not in second
    line = next(l for l in second.splitlines() if "Období 2021:" in l and "stránek stenoprotokolů" in l)
    assert "staženo 0, z cache 1" in line
    # stránka 2. schůze je z roku 2022, takže se podruhé nestahuje
    assert "0 stránek zvlášť" in line


def test_budget_exhausted_still_builds(server, tmp_path):
    log = run_build(server, tmp_path, PSP_FETCH_BUDGET_MINUTES="0")
    line = next(l for l in log.splitlines() if "Období 2021:" in l and "stránek stenoprotokolů" in l)
    assert "přeskočeno 1" in line
    assert "vyčerpán časový rozpočet" in log
    assert (tmp_path / "dist" / "data" / "periods.json").exists()
