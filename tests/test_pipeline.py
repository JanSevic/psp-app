import gzip
import json
import os
import socket
import stat
import subprocess
import sys
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from pipeline.unl import iter_records, to_date
from pipeline import steno

ROOT = Path(__file__).resolve().parent.parent


def test_unl_escapes_and_nulls():
    data = "1|a\\|b||x\\\ny|\n2|c|d|e|\n".encode("cp1250")
    rows = list(iter_records(data))
    assert rows[0] == ["1", "a|b", None, "x\ny"]
    assert rows[1] == ["2", "c", "d", "e"]


def test_dates():
    assert str(to_date("10.11.2021")) == "2021-11-10"
    assert str(to_date("2021-11-10 09")) == "2021-11-10"
    assert to_date("1900-01-01") is None


def test_parse_page_speakers_and_continuation():
    html = (
        '<html><body><div id="main-content"><p><a href="index.htm">Obsah</a></p>'
        "<p>pokračování předchozího</p>"
        "<p><b>2. Bod pořadu</b></p>"
        '<p><a id="r4"></a><b><a href="/sqw/detail.sqw?id=55">Poslanec X Y</a>:</b> Text jedna.</p>'
        "<p>Druhý odstavec.</p>"
        '<p><b><a href="https://www.psp.cz/sqw/detail.sqw?o=9&amp;id=56">Ministryně A B</a>:</b> Odpověď.</p>'
        "</div></body></html>"
    )
    p = steno.parse_page(html)
    assert p.continuation == ["pokračování předchozího"]
    assert p.headings == ["2. Bod pořadu"]
    assert [(s.aname, s.link_osoba, s.label) for s in p.speeches] == [(4, 55, "Poslanec X Y"), (None, 56, "Ministryně A B")]
    assert p.speeches[0].paragraphs == ["Text jedna.", "Druhý odstavec."]
    assert p.speeches[0].heading == "2. Bod pořadu"


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    from tests import fake_psp

    tmp = tmp_path_factory.mktemp("e2e")
    site = tmp / "psp"
    fake_psp.build(site)
    port = _free_port()
    srv = ThreadingHTTPServer(("127.0.0.1", port), partial(SimpleHTTPRequestHandler, directory=str(site)))
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    # falešný `claude` – vrací shrnutí pro všechny skupiny v promptu
    bindir = tmp / "bin"
    bindir.mkdir()
    fake = bindir / "claude"
    fake.write_text(
        "#!/usr/bin/env python3\n"
        "import sys, json, re\n"
        "prompt = sys.stdin.read()\n"
        "ids = re.findall(r'<skupina id=\"(g\\d+)\"', prompt)\n"
        "res = {i: ['Shrnutí bodu ' + i + '.', 'Druhá odrážka.'] for i in ids}\n"
        "print(json.dumps({'type':'result','is_error':False,'result':'```json\\n'+json.dumps(res, ensure_ascii=False)+'\\n```'}))\n"
    )
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)

    env = dict(os.environ)
    env.update(
        PSP_BASE=f"http://127.0.0.1:{port}",
        PSP_CACHE_DIR=str(tmp / "cache"),
        PSP_STATE_DIR=str(tmp / "state"),
        PSP_DIST_DIR=str(tmp / "dist"),
        PSP_REQUEST_DELAY="0",
        CLAUDE_CODE_OAUTH_TOKEN="test",
        SUMMARY_MIN_WORDS="10",
        PATH=f"{bindir}:{env['PATH']}",
        NO_PROXY="127.0.0.1,localhost",
        no_proxy="127.0.0.1,localhost",
    )
    for cmd in (["build"], ["summarize"], ["site"]):
        subprocess.run([sys.executable, "-m", "pipeline", *cmd], cwd=ROOT, env=env, check=True)
    yield tmp / "dist", tmp / "state"
    srv.shutdown()


def load(p: Path):
    data = p.read_bytes()
    if data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    return json.loads(data)


def test_periods(built):
    dist, _ = built
    meta = load(dist / "data" / "periods.json")
    nums = [p["num"] for p in meta["periods"]]
    assert nums == [10, 9, 7]
    p9 = next(p for p in meta["periods"] if p["num"] == 9)
    assert p9["has_speeches"] and p9["n_votes"] == 3
    p7 = next(p for p in meta["periods"] if p["num"] == 7)
    assert not p7["has_speeches"] and p7["n_votes"] == 1


def test_votes_and_person(built):
    dist, _ = built
    votes = load(dist / "data" / "9" / "votes.json")
    assert votes["rows"][1][votes["cols"].index("nazev")] == "Pozměňovací návrh A|B"
    assert votes["rows"][2][votes["cols"].index("zmatecne")] == 1
    novak = load(dist / "data" / "9" / "o" / "101.json")
    assert novak["hlasovani"]["kody"] == "AAC"
    assert [k["zkratka"] for k in novak["kluby"]] == ["KlA", "KlB"]
    assert novak["statistiky"]["hlasovani"]["ucast"] == 100.0
    pred = load(dist / "data" / "9" / "o" / "100.json")
    assert pred["hlasovani"]["kody"] == "ANM"
    svob = load(dist / "data" / "9" / "o" / "102.json")
    assert svob["statistiky"]["hlasovani"]["ucast"] == 50.0
    people = load(dist / "data" / "9" / "people.json")
    minister = next(p for p in people if p["id"] == 200)
    assert not minister["poslanec"] and minister["funkce"] == "Ministr dopravy ČR"


def test_speeches(built):
    dist, _ = built
    groups = load(dist / "data" / "9" / "s" / "101.json.gz")
    keys = [g["key"] for g in groups]
    assert keys == ["2-b4003", "1-b4001"]  # nejnovější první
    g1 = groups[1]
    assert g1["bod"].startswith("Vládní návrh zákona o něčem")
    sp = g1["speeches"][0]
    # první vystoupení Nováka je na s001001 s kotvou r2
    assert sp["label"] == "Poslanec Jan Novák"
    assert sp["url"].endswith("/eknih/2021ps/stenprot/001schuz/s001001.htm#r2")
    pred = load(dist / "data" / "9" / "s" / "100.json.gz")
    roles = [s["role"] for g in pred for s in g["speeches"]]
    assert roles == ["chair", "chair"]
    assert "(Pokračování.)" in pred[0]["speeches"][-1]["text"][-1]
    minister = load(dist / "data" / "9" / "s" / "200.json.gz")
    assert minister[0]["key"] == "1-b4002"


def test_summaries(built):
    dist, state = built
    s = load(dist / "data" / "9" / "sum" / "101.json")
    assert s["1-b4001"]["s"] == "ok" and s["1-b4001"]["b"][1] == "Druhá odrážka."
    pred = load(dist / "data" / "9" / "sum" / "100.json")
    assert all(v["s"] == "chair" for v in pred.values())
    assert (state / "summaries.jsonl.gz").exists()
    status = load(dist / "data" / "summary-status.json")
    assert status["stav"].get("pending", 0) == 0


def test_search_records_and_site(built):
    dist, state = built
    with gzip.open(state / "work" / "search-9.jsonl.gz", "rt") as fh:
        recs = [json.loads(line) for line in fh]
    assert any(r["osoba"] == "9-101" and "D35" in r["content"] for r in recs)
    assert (dist / "index.html").exists() and (dist / "app.js").exists()


def test_parse_page_bold_speaker_without_link():
    html = "<html><body><p><b>Poslanec Jan Novák:</b> Text bez odkazu.</p><p><b>Celý tučný nadpis</b></p></body></html>"
    p = steno.parse_page(html)
    assert [(s.link_osoba, s.label, s.paragraphs) for s in p.speeches] == [(None, "Poslanec Jan Novák", ["Text bez odkazu."])]
    assert p.headings == ["Celý tučný nadpis"]
