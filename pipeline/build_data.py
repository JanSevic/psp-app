"""Sestavení dat webu: stáhne otevřená data a stenoprotokoly a zapíše JSON.

Výstup (dist/data):
  periods.json                    seznam volebních období
  <obdobi>/people.json            osoby s hlasováním nebo projevy v období
  <obdobi>/votes.json             všechna hlasování období
  <obdobi>/o/<id_osoba>.json      detail osoby: kluby, statistiky, hlasování, seznam bodů s projevy
  <obdobi>/s/<id_osoba>.json.gz   plné texty projevů seskupené podle bodů
Pomocné výstupy (state/work):
  groups-<obdobi>.jsonl.gz        vstupy pro shrnutí (osoba × bod)
  search-<obdobi>.jsonl.gz        záznamy pro fulltextový index
"""

from __future__ import annotations

import gzip
import hashlib
import json
import logging
import re
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from . import config, opendata, steno
from .opendata import Period, People
from .unl import to_date, to_int

log = logging.getLogger(__name__)

PSP = config.PSP_BASE


def write_json(path: Path, obj, gz: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(obj, ensure_ascii=False, separators=(",", ":"), default=_default).encode("utf-8")
    if gz:
        data = gzip.compress(data, compresslevel=9, mtime=0)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)


def write_jsonl_gz(path: Path, rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8", compresslevel=6) as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False, default=_default) + "\n")


def _default(o):
    if isinstance(o, date):
        return o.isoformat()
    raise TypeError(type(o))


def text_hash(text: str) -> str:
    return hashlib.sha1((config.SUMMARY_PROMPT_VERSION + "\n" + text).encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------


def _memberships(people: People, period: Period) -> dict[int, list[dict]]:
    """{id_osoba: [{zkratka, nazev, od, do}]} – členství v poslaneckých klubech."""
    club_types = opendata.club_type_ids(people)
    clubs = {
        oid: o for oid, o in people.organy.items()
        if to_int(o.get("id_typ_organu")) in club_types and to_int(o.get("organ_id_organ")) == period.organ_id
    }
    out: dict[int, list[dict]] = defaultdict(list)
    for z in people.zarazeni:
        if to_int(z.get("cl_funkce")) != 0:
            continue
        oid = to_int(z.get("id_of"))
        if oid not in clubs:
            continue
        osoba = to_int(z.get("id_osoba"))
        org = clubs[oid]
        out[osoba].append(
            {
                "zkratka": (org.get("zkratka") or "").strip(),
                "nazev": (org.get("nazev_organu_cz") or "").strip(),
                "od": to_date(z.get("od_o")),
                "do": to_date(z.get("do_o")),
            }
        )
    for v in out.values():
        v.sort(key=lambda m: (m["od"] or date.min))
    return out


def _mandates(people: People, period: Period) -> dict[int, dict]:
    """{id_osoba: {od, do}} – mandát poslance v období (zarazeni do orgánu PSPx)."""
    out = {}
    for z in people.zarazeni:
        if to_int(z.get("cl_funkce")) != 0 or to_int(z.get("id_of")) != period.organ_id:
            continue
        osoba = to_int(z.get("id_osoba"))
        od, do = to_date(z.get("od_o")), to_date(z.get("do_o"))
        cur = out.get(osoba)
        if cur is None:
            out[osoba] = {"od": od, "do": do}
        else:
            if od and (cur["od"] is None or od < cur["od"]):
                cur["od"] = od
            if cur["do"] is not None and (do is None or do > cur["do"]):
                cur["do"] = do
    return out


VOTE_CODES = {
    "A": "ano",
    "N": "ne",
    "C": "zdržel se",
    "F": "nehlasoval",
    "@": "nepřihlášen",
    "M": "omluven",
    "W": "před složením slibu",
    "K": "zdržel se / nehlasoval",
}


def vote_stats(codes: str) -> dict:
    c = Counter(ch for ch in codes if ch != ".")
    total = sum(c.values())
    base = total - c.get("W", 0)
    present = sum(c.get(k, 0) for k in ("A", "N", "C", "F", "K"))
    return {
        "celkem": total,
        "pocty": dict(c),
        "ucast": round(100 * present / base, 1) if base else None,
    }


def _function_from_label(label: str, name: str) -> str | None:
    """'Ministr financí ČR Jan Novák' -> 'Ministr financí ČR'."""
    if not label:
        return None
    if name and label.endswith(name):
        f = label[: -len(name)].strip(" ,")
        return f or None
    parts = label.split()
    return " ".join(parts[:-2]) or None if len(parts) > 2 else None


def bod_key(sp: steno.Speech) -> str:
    if sp.id_bod is not None:
        return f"b{sp.id_bod}"
    if sp.heading:
        return "h" + hashlib.sha1(sp.heading.encode()).hexdigest()[:8]
    return f"d{sp.datum.isoformat() if sp.datum else 'x'}"


def build_period(period: Period, people: People, schuze_tab, bods, steno_meta, dist: Path, work: Path) -> dict:
    out = dist / "data" / str(period.num)
    log.info("== %s", period.label)

    # --- Hlasování
    hl_zip = opendata.download_opendata(f"hl-{period.year}ps.zip", required=False)
    votes, codes_by_poslanec = (opendata.load_votes(hl_zip, period) if hl_zip else ([], {}))
    write_json(
        out / "votes.json",
        {
            "cols": ["id", "schuze", "cislo", "bod", "datum", "cas", "pro", "proti", "zdrzel", "nehlasoval",
                     "prihlaseno", "kvorum", "vysledek", "nazev", "zmatecne", "druh"],
            "rows": [
                [v.id, v.schuze, v.cislo, v.bod, v.datum, v.cas, v.pro, v.proti, v.zdrzel, v.nehlasoval,
                 v.prihlaseno, v.kvorum, v.vysledek, v.nazev, 1 if v.zmatecne else 0, v.druh]
                for v in votes
            ],
        },
    )

    deputies = [p for p in people.poslanci if to_int(p.get("id_obdobi")) == period.organ_id]
    pid_to_osoba = {to_int(p["id_poslanec"]): to_int(p["id_osoba"]) for p in deputies}
    deputy_rows = {to_int(p["id_osoba"]): p for p in deputies}
    codes_by_osoba: dict[int, str] = {}
    for pid, arr in codes_by_poslanec.items():
        osoba = pid_to_osoba.get(pid)
        if osoba is None:
            continue
        s = arr.decode("ascii")
        prev = codes_by_osoba.get(osoba)
        if prev:  # jedna osoba s více id_poslanec (nemělo by nastat) – sloučit
            s = "".join(b if a == "." else a for a, b in zip(prev, s))
        codes_by_osoba[osoba] = s

    clubs = _memberships(people, period)
    mandates = _mandates(people, period)

    # --- Projevy
    speeches: list[steno.Speech] = []
    steno_stats = None
    if period.has_speeches:
        pages = steno.collect_pages(period, steno_meta)
        speeches, steno_stats = steno.build_speeches(period, pages, steno_meta)
        log.info("Projevy %s: %s", period.year, steno_stats)

    sch_dates: dict[int, date] = {}
    for sid, s in schuze_tab.items():
        if to_int(s.get("id_org")) == period.organ_id:
            d = to_date(s.get("od_schuze"))
            n = to_int(s.get("schuze"))
            if n is not None and d:
                sch_dates[n] = d

    by_osoba: dict[int, list[steno.Speech]] = defaultdict(list)
    for sp in speeches:
        if sp.id_osoba is not None:
            by_osoba[sp.id_osoba].append(sp)

    # --- Osoby
    osoby_ids = set(deputy_rows) | set(by_osoba)
    people_list = []
    group_rows = []
    search_rows = []
    for osoba in sorted(osoby_ids):
        o = people.osoby.get(osoba)
        name = opendata.person_name(o)
        dep = deputy_rows.get(osoba)
        sp_list = by_osoba.get(osoba, [])
        labels = Counter(s.label for s in sp_list if s.role == "speaker") or Counter(s.label for s in sp_list)
        funkce = _function_from_label(labels.most_common(1)[0][0], name) if labels else None
        club_hist = clubs.get(osoba, [])
        last_club = club_hist[-1] if club_hist else None
        kraj = None
        kandidatka = None
        if dep:
            k = people.organy.get(to_int(dep.get("id_kraj")))
            kraj = (k.get("nazev_organu_cz") or "").strip() if k else None
            kd = people.organy.get(to_int(dep.get("id_kandidatka")))
            kandidatka = (kd.get("zkratka") or "").strip() if kd else None

        codes = codes_by_osoba.get(osoba, "")
        first = next((i for i, ch in enumerate(codes) if ch != "."), None)
        last = next((i for i in range(len(codes) - 1, -1, -1) if codes[i] != "."), None)
        vstats = vote_stats(codes) if codes else None

        # skupiny projevů podle schůze a bodu
        groups: dict[str, dict] = {}
        for sp in sp_list:
            key = f"{sp.schuze}-{bod_key(sp)}"
            g = groups.get(key)
            if g is None:
                b = bods.get(sp.id_bod) if sp.id_bod is not None else None
                g = groups[key] = {
                    "key": key,
                    "schuze": sp.schuze,
                    "id_bod": sp.id_bod,
                    "bod_cislo": to_int(b.get("bod")) if b else None,
                    "bod": opendata.bod_title(b) or sp.heading or "Bez určeného bodu",
                    "datum": sp.datum,
                    "speeches": [],
                }
            if sp.datum and (g["datum"] is None or sp.datum < g["datum"]):
                g["datum"] = sp.datum
            g["speeches"].append(sp)
        glist = sorted(groups.values(), key=lambda g: (g["datum"] or sch_dates.get(g["schuze"]) or date.min, g["schuze"]), reverse=True)

        n_speaker = sum(1 for s in sp_list if s.role == "speaker")
        n_chair = len(sp_list) - n_speaker
        words = sum(s.words for s in sp_list if s.role == "speaker")

        speech_groups_out = []
        group_index = []
        for g in glist:
            sp_out = []
            for s in g["speeches"]:
                sp_out.append(
                    {
                        "id": s.id,
                        "turn": s.turn,
                        "aname": s.aname,
                        "datum": s.datum,
                        "cas": s.cas,
                        "label": s.label,
                        "role": s.role,
                        "url": steno.page_url(period, s.schuze, s.turn, s.aname),
                        "text": s.paragraphs,
                    }
                )
            speech_groups_out.append({k: g[k] for k in ("key", "schuze", "id_bod", "bod_cislo", "bod", "datum")} | {"speeches": sp_out})
            spk = [s for s in g["speeches"] if s.role == "speaker"]
            spk_text = "\n\n".join("\n".join(s.paragraphs) for s in spk)
            gwords = sum(s.words for s in spk)
            group_index.append(
                {
                    "key": g["key"],
                    "schuze": g["schuze"],
                    "bod": g["bod"],
                    "bod_cislo": g["bod_cislo"],
                    "datum": g["datum"],
                    "n": len(g["speeches"]),
                    "n_speaker": len(spk),
                    "words": gwords,
                }
            )
            group_rows.append(
                {
                    "period": period.num,
                    "osoba": osoba,
                    "key": g["key"],
                    "name": name,
                    "bod": g["bod"],
                    "datum": g["datum"],
                    "words": gwords,
                    "n_speaker": len(spk),
                    "hash": text_hash(spk_text) if spk_text else None,
                    "text": spk_text,
                }
            )
            full_text = "\n\n".join("\n".join(s.paragraphs) for s in g["speeches"])
            if full_text.strip():
                search_rows.append(
                    {
                        "url": f"#/{period.num}/o/{osoba}/projevy/{g['key']}",
                        "content": full_text,
                        "title": f"{name}: {g['bod']}",
                        "datum": g["datum"],
                        "osoba": f"{period.num}-{osoba}",
                        "obdobi": str(period.num),
                        "name": name,
                    }
                )

        if speech_groups_out:
            write_json(out / "s" / f"{osoba}.json.gz", speech_groups_out, gz=True)

        detail = {
            "id": osoba,
            "obdobi": period.num,
            "name": name,
            "full_name": opendata.person_full_name(o),
            "poslanec": dep is not None,
            "pohlavi": (o or {}).get("pohlavi"),
            "funkce": funkce,
            "kraj": kraj,
            "kandidatka": kandidatka,
            "mandat": mandates.get(osoba),
            "kluby": club_hist,
            "psp_url": f"{PSP}/sqw/detail.sqw?id={osoba}",
            "hlasovani": {"offset": first, "kody": codes[first: last + 1] if first is not None else ""},
            "statistiky": {
                "hlasovani": vstats,
                "projevy": {"vystoupeni": n_speaker, "rizeni": n_chair, "slova": words, "body": len(glist)},
            },
            "body": group_index,
        }
        write_json(out / "o" / f"{osoba}.json", detail)
        people_list.append(
            {
                "id": osoba,
                "name": name,
                "sort": f"{(o or {}).get('prijmeni') or ''} {(o or {}).get('jmeno') or ''}".strip(),
                "poslanec": dep is not None,
                "funkce": funkce,
                "klub": last_club["zkratka"] if last_club else None,
                "kraj": kraj,
                "ucast": vstats["ucast"] if vstats else None,
                "vystoupeni": n_speaker,
                "rizeni": n_chair,
            }
        )

    write_json(out / "people.json", people_list)
    write_jsonl_gz(work / f"groups-{period.num}.jsonl.gz", group_rows)
    write_jsonl_gz(work / f"search-{period.num}.jsonl.gz", search_rows)
    info = {
        "num": period.num,
        "year": period.year,
        "label": period.label,
        "od": period.od,
        "do": period.do,
        "has_speeches": period.has_speeches,
        "n_votes": len(votes),
        "n_people": len(people_list),
        "n_deputies": sum(1 for p in people_list if p["poslanec"]),
        "n_speeches": len(speeches),
    }
    if steno_stats:
        write_json(work / f"steno-stats-{period.num}.json", steno_stats)
    return info


def build(dist: Path | None = None, only_periods: set[int] | None = None) -> None:
    dist = dist or config.DIST_DIR
    work = config.STATE_DIR / "work"
    work.mkdir(parents=True, exist_ok=True)

    people = opendata.load_people(opendata.download_opendata("poslanci.zip"))
    if not people.periods:
        raise RuntimeError("V datech nejsou žádná volební období (organy PSPx)")
    schuze_zip = opendata.download_opendata("schuze.zip", required=False)
    schuze_tab, bods = opendata.load_bods(schuze_zip) if schuze_zip else ({}, {})
    steno_meta = opendata.StenoMeta(turns={}, by_key={}, rec={}, bod_marks={})
    if any(p.has_speeches for p in people.periods):
        steno_meta = opendata.load_steno_meta(opendata.download_opendata("steno.zip", required=False))

    infos = []
    for period in people.periods:
        if only_periods and period.num not in only_periods:
            continue
        infos.append(build_period(period, people, schuze_tab, bods, steno_meta, dist, work))
    infos.sort(key=lambda i: i["num"], reverse=True)
    write_json(
        dist / "data" / "periods.json",
        {
            "periods": infos,
            "generated": datetime.now(ZoneInfo("Europe/Prague")).strftime("%Y-%m-%d %H:%M"),
            "vote_codes": VOTE_CODES,
            "speeches_from_year": config.SPEECHES_FROM_YEAR,
        },
    )
