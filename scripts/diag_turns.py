"""Diagnostika přiřazení k bodům: pro zadané stránky (schůze-turn) vypíše
záznamy `rec`, značky bodů `steno_bod`, nadpisy a vystoupení ze stránky.

Použití: python scripts/diag_turns.py <rok_obdobi> <id_organ> 34-518 34-164 ...
Čte jen z cache (PSP_CACHE_DIR), nic nestahuje.
"""

import sys
import zipfile
from pathlib import Path

from pipeline import config, opendata, steno, fetch

year, organ = int(sys.argv[1]), int(sys.argv[2])
meta = opendata.load_steno_meta(opendata.opendata_path("steno.zip"))
_, bods = opendata.load_bods(opendata.opendata_path("schuze.zip"))


def title(bid):
    b = bods.get(bid)
    return f"{bid} č.{b.get('bod') if b else '?'} {(opendata.bod_title(b) or '')[:70]}"


def page_html(sch, turn):
    base = config.CACHE_DIR / "steno" / str(year)
    name = f"s{sch:03d}{turn:03d}.htm"
    f = base / "pages" / name
    if f.exists():
        return fetch.decode_html(f.read_bytes()), "pages"
    z = base / "zip" / f"{sch:03d}schuz.zip"
    if z.exists():
        with zipfile.ZipFile(z) as zf:
            for n in zf.namelist():
                if n.endswith(name) and zf.getinfo(n).file_size:
                    return fetch.decode_html(zf.read(n)), "zip"
    return None, None


for arg in [a for a in sys.argv[3:] if not a.startswith("--")]:
    sch, turn = map(int, arg.split("-"))
    st = meta.by_key.get((organ, sch, turn))
    print(f"\n===== schůze {sch}, turn {turn}, id_steno {st.id_steno if st else None}, {st.datum if st else ''}")
    if st:
        for r in meta.rec.get(st.id_steno, []):
            print(f"  rec aname={r['aname']} osoba={r['id_osoba']} druh={r['druh']} bod={title(r['id_bod']) if r['id_bod'] else None}")
        for an, bid in meta.bod_marks.get(st.id_steno, []):
            print(f"  steno_bod aname={an} -> {title(bid)}")
    html, src = page_html(sch, turn)
    if html is None:
        print("  stránka v cache není")
        continue
    p = steno.parse_page(html)
    print(f"  stránka ({src}), nadpisy: {p.headings}")
    for ps in p.speeches:
        print(f"  vystoupení aname={ps.aname} osoba={ps.link_osoba} '{ps.label}' nadpis={ps.heading!r}")


# --- souhrn: jak často chybí rec.id_bod a jestli je „uvnitř“ jednoho bodu
if "--stats" in sys.argv:
    from collections import Counter
    seq = {}
    for t in meta.turns.values():
        if t.organ_id != organ:
            continue
        for r in meta.rec.get(t.id_steno, []):
            seq.setdefault(t.schuze, []).append(((t.turn, r["aname"] or 0), r["id_bod"], r["druh"]))
    c = Counter()
    for sch, rows in seq.items():
        rows.sort()
        bods = [b for _, b, _ in rows]
        for i, (_, b, druh) in enumerate(rows):
            if b is not None:
                c["s bodem"] += 1
                continue
            prev = next((x for x in reversed(bods[:i]) if x is not None), None)
            nxt = next((x for x in bods[i + 1:] if x is not None), None)
            kind = "uvnitř téhož bodu" if prev is not None and prev == nxt else "mezi body / na okraji"
            c[f"bez bodu, {kind}, {'řečník' if druh in (3, 5) else 'předsedající/jiné'}"] += 1
    for k, v in sorted(c.items()):
        print(f"  {k}: {v}")
