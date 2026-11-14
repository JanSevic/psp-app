"""Kontrola správnosti dat: projde vystoupení sestavená z cache a hotový web (dist/)
a vypíše podezřelá místa. Nic nestahuje z psp.cz.

Použití: PYTHONPATH=. python scripts/audit.py [obdobi ...]   (výchozí 8 9 10)
Potřebuje PSP_CACHE_DIR s cache nočního běhu; dist/ je nepovinné (artefakt „site“).
"""

import gzip
import json
import re
import sys
import zipfile
from collections import Counter, defaultdict
from pathlib import Path

from pipeline import config, notes, opendata, steno
from pipeline.build_data import MIMO_BOD
from pipeline.unl import to_int

people = opendata.load_people(opendata.opendata_path("poslanci.zip"))
schuze_tab, bods = opendata.load_bods(opendata.opendata_path("schuze.zip"))
meta = opendata.load_steno_meta(opendata.opendata_path("steno.zip"))
surnames = {oid: (o.get("prijmeni") or "").strip() for oid, o in people.osoby.items()}
name_index = notes.NameIndex({oid: ((o.get("jmeno") or "").strip(), (o.get("prijmeni") or "").strip())
                              for oid, o in people.osoby.items()})
sch_num = {sid: (to_int(s.get("id_org")), to_int(s.get("schuze"))) for sid, s in schuze_tab.items()}

LABEL_IN_TEXT = re.compile(
    r"^(Poslan(ec|kyně)|Místopředsed(a|kyně) (PSP|vlády)|Předsed(a|kyně) (PSP|vlády|Senátu)|"
    r"Minist(r|ryně)|Senátor(ka)?|Předsedající)\b[^:]{2,90}:\s"
)


def cached_pages(period):
    base = config.CACHE_DIR / "steno" / str(period.year)
    pages = {}
    for z in sorted((base / "zip").glob("*.zip")):
        try:
            with zipfile.ZipFile(z) as zf:
                for info in zf.infolist():
                    m = steno.PAGE_RE.match(Path(info.filename).name)
                    if m and info.file_size:
                        pages[(int(m.group(1)), int(m.group(2)))] = ("zip", z, info.filename)
        except zipfile.BadZipFile:
            print(f"  poškozený ZIP {z.name}")
    for f in (base / "pages").glob("s*.htm"):
        m = steno.PAGE_RE.match(f.name)
        if m and (int(m.group(1)), int(m.group(2))) not in pages:
            pages[(int(m.group(1)), int(m.group(2)))] = ("file", f)
    return pages


def show(title, counter, n=12):
    print(f"  {title}: {sum(counter.values())}" + (f" – nejčastěji {counter.most_common(n)}" if counter else ""))


def main(nums):
    for period in people.periods:
        if period.num not in nums or not period.has_speeches:
            continue
        print(f"\n######## {period.num}. období ({period.year}), organ {period.organ_id}")
        pages = cached_pages(period)
        speeches, stats = steno.build_speeches(period, pages, meta, surnames, name_index)
        print(f"  stránek {len(pages)}, statistika: {stats}")

        # stránky, které steno.zip nezná (chybí datum, rec i značky bodů)
        unknown = Counter(sch for (sch, turn) in pages if (period.organ_id, sch, turn) not in meta.by_key)
        show("stránky bez záznamu v steno.zip (podle schůze)", unknown)
        known_turns = Counter(t.schuze for t in meta.turns.values() if t.organ_id == period.organ_id)
        missing = Counter()
        for t in meta.turns.values():
            if t.organ_id == period.organ_id and (t.schuze, t.turn) not in pages:
                missing[t.schuze] += 1
        show("turny ze steno.zip bez stránky v cache (podle schůze)", missing)
        print(f"  schůzí v steno.zip: {len(known_turns)}, max schůze se stránkami: {max((s for s, _ in pages), default=None)}")

        no_date = Counter(s.schuze for s in speeches if s.datum is None)
        show("vystoupení bez data (podle schůze)", no_date)
        no_osoba = [s for s in speeches if s.id_osoba is None]
        show("vystoupení bez osoby (na webu se neukážou)", Counter(s.label for s in no_osoba))
        print(f"    z toho slov: {sum(len(' '.join(s.paragraphs).split()) for s in no_osoba)}")

        # role: jmenovka předsedajícího u role řečníka a naopak
        chair_lbl = lambda s: s.label.lower().startswith(steno.CHAIR_PREFIXES)
        show("role řečník, ale jmenovka předsedajícího", Counter(s.label for s in speeches if s.role == "speaker" and chair_lbl(s)), 6)
        show("role předsedající, ale jmenovka jiná", Counter(s.label for s in speeches if s.role == "chair" and not chair_lbl(s)), 6)

        # bod jiné schůze, bod neexistující v bod_schuze
        wrong_sch, unknown_bod = Counter(), Counter()
        for s in speeches:
            if s.id_bod is None:
                continue
            b = bods.get(s.id_bod)
            if b is None:
                unknown_bod[s.schuze] += 1
                continue
            org, n = sch_num.get(to_int(b.get("id_schuze")), (None, None))
            if n is not None and (org != period.organ_id or n != s.schuze):
                wrong_sch[(s.schuze, n)] += 1
        show("bod patří jiné schůzi (schůze vystoupení, schůze bodu)", wrong_sch)
        show("id_bod chybí v bod_schuze (podle schůze)", unknown_bod)
        show("bez bodu (podle schůze)", Counter(s.schuze for s in speeches if s.id_bod is None), 8)

        # značky bodů: aname, které v rec téže stránky není
        odd = total = 0
        for t in meta.turns.values():
            if t.organ_id != period.organ_id:
                continue
            an = {r["aname"] for r in meta.rec.get(t.id_steno, [])}
            for a, _ in meta.bod_marks.get(t.id_steno, []):
                total += 1
                odd += a not in an
        print(f"  značky bodů (steno_bod): {total}, z toho s aname mimo rec téže stránky: {odd}")

        # jmenovka jiného řečníka uvnitř textu (parser nepoznal začátek vystoupení)
        inner = [(s, p) for s in speeches for p in s.paragraphs[1:] if LABEL_IN_TEXT.match(p)]
        show("odstavec vypadá jako jmenovka jiného řečníka", Counter(s.schuze for s, _ in inner), 8)
        for s, p in inner[:6]:
            print(f"    {s.id} ({s.label}): {p[:110]!r}")

        words = sorted(((len(" ".join(s.paragraphs).split()), s) for s in speeches if s.role == "speaker"), key=lambda x: -x[0])
        print("  nejdelší vystoupení:", [(w, s.id, s.label[:40]) for w, s in words[:5]])
        print(f"  prázdná vystoupení řečníků: {sum(1 for w, _ in words if w == 0)}")
        dup = [k for k, v in Counter(s.id for s in speeches).items() if v > 1]
        print(f"  duplicitní id vystoupení: {len(dup)} {dup[:5]}")

        # osoba z odkazu vs. rec: ukázky
        # (stats["osoba_mismatch"] – kolik; tady jen zda to nejsou systematicky předsedající)

        # --- hotový web
        d = Path("dist/data") / str(period.num)
        if not d.exists():
            continue
        print("  --- web (dist)")
        bad_sum = Counter()
        vcheck = Counter()
        gcount = Counter()
        for f in (d / "o").glob("*.json"):
            o = json.loads(f.read_text())
            h = (o.get("statistiky") or {}).get("hlasovani")
            if h:
                if h["hlasoval"] + h["omluven"] + h["neprihlasen"] != h["celkem"]:
                    vcheck["součet ≠ celkem"] += 1
                for k in h.get("pocty", {}):
                    if k not in "ANCF@MWK":
                        vcheck[f"neznámý kód {k}"] += 1
                if h["celkem"] == 0:
                    vcheck["poslanec bez hlasování"] += 1
            elif o.get("poslanec"):
                vcheck["poslanec bez statistiky"] += 1
            sf = d / "sum" / f.name
            sums = json.loads(sf.read_text()) if sf.exists() else {}
            for g in o.get("body", []):
                gcount["skupin"] += 1
                if g["bod"] == MIMO_BOD:
                    gcount["mimo bod"] += 1
                elif g.get("bod_cislo") is None:
                    gcount["název z nadpisu stránky"] += 1
                if g["datum"] is None:
                    gcount["bez data"] += 1
                st = (sums.get(g["key"]) or {}).get("s")
                bad_sum[st or "chybí v sum"] += 1
        print(f"  hlasování: {dict(vcheck) or 'v pořádku'}")
        print(f"  skupiny: {dict(gcount)}; stav shrnutí: {dict(bad_sum)}")


if __name__ == "__main__":
    main([int(a) for a in sys.argv[1:]] or [8, 9, 10])
