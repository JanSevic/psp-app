"""Podrobnější sonda k nálezům scripts/audit.py (značky bodů, vystoupení bez rec,
neznámé id_bod, role, duplicitní id). Čte jen z cache, nic nestahuje.

Použití: PYTHONPATH=. python scripts/audit_probe.py [obdobi ...]
"""

import re
import sys
import zipfile
from collections import Counter
from pathlib import Path

from pipeline import config, fetch, notes, opendata, steno
from pipeline.unl import to_int

sys.path.insert(0, str(Path(__file__).parent))
from audit import cached_pages, people, bods, meta, surnames, name_index, sch_num  # noqa: E402

nums = [int(a) for a in sys.argv[1:]] or [9, 10]


def html_of(ref):
    return steno.load_page(ref)


for period in people.periods:
    if period.num not in nums or not period.has_speeches:
        continue
    print(f"\n######## {period.num}. období")
    pages = cached_pages(period)

    # a) značky bodů: k čemu se vztahuje aname?
    shown = 0
    for t in sorted(meta.turns.values(), key=lambda t: (-t.schuze, t.turn)):
        if t.organ_id != period.organ_id or not meta.bod_marks.get(t.id_steno):
            continue
        ref = pages.get((t.schuze, t.turn))
        if not ref:
            continue
        html = html_of(ref)
        ids = re.findall(r'<a[^>]+(?:id|name)="([a-z]?\d+)"', html)
        recs = [(r["aname"], r["id_bod"]) for r in meta.rec.get(t.id_steno, [])]
        print(f"  s{t.schuze:03d}{t.turn:03d}: značky {meta.bod_marks[t.id_steno]}, rec {recs[:12]}, kotvy {ids[:25]}")
        for an, bid in meta.bod_marks[t.id_steno]:
            m = re.search(rf'(?:id|name)="[a-z]?{an}"', html)
            if m:
                i = m.start()
                print(f"    kotva {an}: …{re.sub(r'\s+', ' ', html[max(0, i - 80): i + 220])}…")
        shown += 1
        if shown >= 4:
            break

    # b)–f) přes sestavení vystoupení s počítáním zdroje bodu
    inferred = steno.infer_missing_bods(meta, period.organ_id)
    src = Counter()
    no_rec_pages = Counter()
    carry_bad = Counter()
    unknown_bod = Counter()
    chair_odd = []
    dups = Counter()
    for (sch, turn) in sorted(pages):
        st = meta.by_key.get((period.organ_id, sch, turn))
        if not st:
            continue
        try:
            parsed = steno.parse_page(html_of(pages[(sch, turn)]))
        except Exception:
            continue
        recs = meta.rec.get(st.id_steno, [])
        rec_by_aname = {r["aname"]: r for r in recs if r["aname"] is not None}
        marks = meta.bod_marks.get(st.id_steno, [])
        steno._guess_osoby(parsed.speeches, recs, surnames)
        aligned = steno._align_by_osoba(parsed.speeches, recs)
        an_seen = Counter(ps.aname for ps in parsed.speeches if ps.aname is not None)
        for a, c in an_seen.items():
            if c > 1:
                dups[(sch, turn, a, tuple(ps.label for ps in parsed.speeches if ps.aname == a))] += 1
        page_bods = {r["id_bod"] for r in recs if r["id_bod"]}
        for k, ps in enumerate(parsed.speeches):
            r = rec_by_aname.get(ps.aname) if ps.aname is not None else None
            if r is None and recs and len(recs) == len(parsed.speeches):
                r = recs[k]
            elif r is None and k in aligned:
                r = aligned[k]
            if r is None:
                no_rec_pages["víc vystoupení než rec" if len(parsed.speeches) > len(recs) else
                             "méně vystoupení než rec" if len(parsed.speeches) < len(recs) else "stejně"] += 1
                if page_bods:
                    carry_bad["stránka má rec s bodem"] += 1
                continue
            if r.get("id_bod"):
                src["rec"] += 1
                if r["id_bod"] not in bods:
                    unknown_bod[(sch, r["id_bod"])] += 1
            else:
                an = ps.aname if ps.aname is not None else r.get("aname")
                cand = [b for a, b in marks if an is not None and a <= an]
                inf = inferred.get((st.id_steno, r.get("aname")))
                if cand:
                    src["značka"] += 1
                    if inf is not None and inf != cand[-1]:
                        src["značka ≠ okolí"] += 1
                    elif inf is None:
                        src["značka, okolí nic"] += 1
                else:
                    src["okolí" if inf else "mimo bod"] += 1
            druh = r.get("druh")
            if druh in (2, 4) and not ps.label.lower().startswith(steno.CHAIR_PREFIXES) and len(chair_odd) < 12:
                chair_odd.append((f"{sch}-{turn}", ps.label, druh, r.get("id_osoba"), ps.link_osoba, len(" ".join(ps.paragraphs).split())))
    print(f"  zdroj bodu: {dict(src)}")
    print(f"  bez rec: {dict(no_rec_pages)}; {dict(carry_bad)}")
    print(f"  neznámé id_bod (schůze, id_bod): {unknown_bod.most_common(8)}")
    for (sch, bid), _ in unknown_bod.most_common(3):
        same = sorted((to_int(b.get("bod")), bid2, (opendata.bod_title(b) or "")[:50]) for bid2, b in bods.items()
                      if sch_num.get(to_int(b.get("id_schuze"))) == (period.organ_id, sch))[:3]
        near = sorted(bods, key=lambda x: abs(x - bid))[:3]
        print(f"    schůze {sch}, id_bod {bid}: body schůze v bod_schuze např. {same}; nejbližší id {near}")
    print(f"  role předsedající podle rec, jmenovka jiná (turn, jmenovka, druh, osoba rec, osoba odkaz, slov): {chair_odd}")
    print(f"  duplicitní kotvy: {len(dups)} {list(dups)[:4]}")
