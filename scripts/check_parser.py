"""Kontrola parseru stenoprotokolů na všech volebních obdobích.

Nic nezapisuje do dist/, jen vypíše statistiky párování vystoupení s tabulkou
`rec` a poznámek stenografů. U období se ZIPy stenoprotokolů zpracuje všechny
stránky ze ZIPů; u období bez ZIPů stáhne vzorek stránek (--sample).

    python scripts/check_parser.py [--periods 1,2,3] [--sample 80]
"""

from __future__ import annotations

import argparse
import collections
import logging
import random
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline import config, fetch, notes, opendata, steno  # noqa: E402


def zip_pages(period) -> dict:
    pages = {}
    for sch, url in sorted(steno.list_zip_urls(period).items()):
        path, _ = fetch.fetch(url, steno._cache(period) / "zip" / Path(url).name)
        if path is None:
            continue
        with zipfile.ZipFile(path) as zf:
            for info in zf.infolist():
                m = steno.PAGE_RE.match(Path(info.filename).name)
                if m and info.file_size:
                    pages[(int(m.group(1)), int(m.group(2)))] = ("zip", path, info.filename)
    return pages


def sample_pages(period, meta, n: int) -> dict:
    turns = sorted((t.schuze, t.turn) for t in meta.turns.values() if t.organ_id == period.organ_id)
    random.seed(period.num)
    pages = {}
    for sch, turn in sorted(random.sample(turns, min(n, len(turns)))):
        dest = steno._cache(period) / "pages" / f"s{sch:03d}{turn:03d}.htm"
        path, _ = fetch.fetch(steno.page_url(period, sch, turn), dest)
        if path is not None:
            pages[(sch, turn)] = ("file", path)
    return pages


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--periods")
    ap.add_argument("--sample", type=int, default=80)
    a = ap.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    people = opendata.load_people(opendata.download_opendata("poslanci.zip"))
    meta = opendata.load_steno_meta(opendata.download_opendata("steno.zip"))
    surnames = {oid: (o.get("prijmeni") or "").strip() for oid, o in people.osoby.items()}
    all_names = notes.NameIndex({oid: ((o.get("jmeno") or "").strip(), surnames[oid]) for oid, o in people.osoby.items()})
    only = {int(x) for x in a.periods.split(",")} if a.periods else None
    for period in people.periods:
        if only and period.num not in only:
            continue
        pages = zip_pages(period)
        source = "ZIP"
        if not pages:
            pages, source = sample_pages(period, meta, a.sample), f"vzorek {a.sample}"
        n_turns = sum(1 for t in meta.turns.values() if t.organ_id == period.organ_id)
        speeches, stats = steno.build_speeches(period, pages, meta, surnames, all_names)
        n_rec = sum(len(meta.rec.get(meta.by_key[(period.organ_id,) + k].id_steno, []))
                    for k in pages if (period.organ_id,) + k in meta.by_key)
        ids = {s.id_osoba for s in speeches if s.id_osoba}
        idx = notes.NameIndex({i: ((people.osoby[i].get("jmeno") or "").strip(), surnames.get(i, ""))
                               for i in ids if i in people.osoby})
        nst = collections.Counter()
        for sp in speeches:
            for _, _, tok in notes.find_notes(sp.paragraphs):
                o = idx.resolve(tok)
                nst["nejednoznačné" if o is None else "o řečníkovi" if o == sp.id_osoba else "přiřazené"] += 1
        roles = collections.Counter(s.role for s in speeches)
        no_osoba = sum(1 for s in speeches if not s.id_osoba)
        no_bod = sum(1 for s in speeches if s.id_bod is None)
        print(f"\n== {period.label} [{source}] stránek {len(pages)}/{n_turns} turnů, rec na nich {n_rec}")
        print(f"   vystoupení {len(speeches)}; bez osoby {no_osoba}; bez bodu {no_bod}; role {dict(roles)}")
        print(f"   párování {stats}")
        print(f"   poznámky {dict(nst)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
