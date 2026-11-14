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


for arg in sys.argv[3:]:
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
