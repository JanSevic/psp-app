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

from . import config, fetch, notes, opendata, steno
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


def _mandate_intervals(people: People, period: Period) -> dict[int, list[tuple[date | None, date | None]]]:
    """{id_osoba: [(od, do), …]} – všechna období mandátu v rámci volebního období."""
    out: dict[int, list[tuple[date | None, date | None]]] = defaultdict(list)
    for z in people.zarazeni:
        if to_int(z.get("cl_funkce")) != 0 or to_int(z.get("id_of")) != period.organ_id:
            continue
        out[to_int(z.get("id_osoba"))].append((to_date(z.get("od_o")), to_date(z.get("do_o"))))
    return out


def mask_to_mandate(codes: str, dates: list[date | None], intervals) -> str:
    """Kódy hlasování mimo dobu mandátu nahradí '.' (hlasování se osoby netýká).

    Otevřená data obsahují u některých poslanců záznamy i z doby, kdy mandát neměli;
    účast se má počítat jen z doby mandátu. Bez údajů o mandátu se nic nemění.
    """
    if not intervals:
        return codes

    def inside(d):
        if d is None:
            return True
        return any((od is None or d >= od) and (do is None or d <= do) for od, do in intervals)

    return "".join(ch if ch == "." or inside(dates[i]) else "." for i, ch in enumerate(codes))


# Název skupiny pro vystoupení, u nichž psp.cz neuvádí bod pořadu.
MIMO_BOD = "Mimo projednávaný bod (přednostní práva, pořad schůze, procedurální vystoupení)"
# Bod uvedený v `rec`, který v bod_schuze chybí (typicky sloučená rozprava) a nemá nadpis.
BEZ_NAZVU = "Bod pořadu schůze (název v otevřených datech chybí)"

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


def vote_stats(codes: str, skip: list[bool] | None = None) -> dict:
    """Statistika hlasování jako na psp.cz (sqw/pstat.sqw).

    Nepočítají se zmatečná hlasování (`skip`) ani hlasování před složením slibu (W).
    Hlasoval = ano + ne + zdržel se; „zdržel se“ zahrnuje i přihlášené, kteří
    nestiskli tlačítko (F, K) – stejně jako výpis hlasování na psp.cz.
    """
    c = Counter(ch for i, ch in enumerate(codes) if ch != "." and not (skip and skip[i]))
    total = sum(c.values()) - c.get("W", 0)
    zdrzel = c.get("C", 0) + c.get("F", 0) + c.get("K", 0)
    hlasoval = c.get("A", 0) + c.get("N", 0) + zdrzel

    def pct(n: int) -> float | None:
        return round(100 * n / total, 1) if total else None

    return {
        "celkem": total,
        "hlasoval": hlasoval,
        "ano": c.get("A", 0),
        "ne": c.get("N", 0),
        "zdrzel": zdrzel,
        "omluven": c.get("M", 0),
        "neprihlasen": c.get("@", 0),
        "ucast": pct(hlasoval),
        "pocty": dict(c),
    }


def _clock(v: str | None) -> str | None:
    """'14:30', '14.30', '1900-01-01 14:30:00' -> '14:30'."""
    m = re.search(r"(\d{1,2})[:.](\d{2})", v or "")
    return f"{int(m.group(1)):02d}:{m.group(2)}" if m else None


def apply_omluvy(codes: bytearray, votes: list, omluvy: list[tuple]) -> None:
    """Nepřihlášen (@) v době omluvy → omluven (M), jako to počítá psp.cz.

    `omluvy` = [(den, od, do)], od/do 'HH:MM' nebo None (celý jednací den).
    """
    for i, v in enumerate(votes):
        if codes[i] != ord("@") or v.datum is None:
            continue
        for den, od, do in omluvy:
            if den != v.datum:
                continue
            if v.cas is None or ((od is None or v.cas >= od) and (do is None or v.cas <= do)):
                codes[i] = ord("M")
                break


def _function_from_label(label: str, name: str) -> str | None:
    """'Ministr financí ČR Jan Novák' -> 'Ministr financí ČR'."""
    if not label:
        return None
    if name and label.endswith(name):
        f = label[: -len(name)].strip(" ,")
        return f or None
    parts = label.split()
    return " ".join(parts[:-2]) or None if len(parts) > 2 else None


def group_title(b: dict | None, sp: steno.Speech, bod_heading: dict[int, str]) -> str:
    """Název skupiny: bod z bod_schuze, jinak nadpis ze stenoprotokolu, jinak obecný popis."""
    if b:
        return opendata.bod_title(b) or sp.heading or BEZ_NAZVU
    if sp.id_bod is not None:
        return bod_heading.get(sp.id_bod) or sp.heading or BEZ_NAZVU
    return sp.heading or MIMO_BOD


def ready_schuze(meta, organ_id: int, today: date, min_age: int) -> tuple[set[int], dict[str, int]]:
    """Schůze, jejichž vystoupení se smí shrnout: psp.cz už ke schůzi zveřejnilo `rec`
    (osoby a body vystoupení) a poslední den schůze je aspoň `min_age` dní starý.

    Úplnost `rec` po stránkách podmínkou být nemůže: i staré schůze mají část stránek bez
    záznamů natrvalo (ověřeno 14. 11. 2026 – 92 ze 119 schůzí 8. období). Čerstvě skončená
    schůze ale `rec` nemá vůbec (36. schůze 10. období). Vrací (schůze, počty důvodů)."""
    turns: dict[int, list] = defaultdict(list)
    for t in meta.turns.values():
        if t.organ_id == organ_id:
            turns[t.schuze].append(t)
    ready, why = set(), Counter()
    for sch, ts in turns.items():
        last = max((t.datum for t in ts if t.datum), default=None)
        if not any(meta.rec.get(t.id_steno) for t in ts):
            why["chybí rec"] += 1
        elif last is None or (today - last).days < min_age:
            why["příliš nová"] += 1
        else:
            ready.add(sch)
    return ready, dict(why)


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
    closed = period.do is not None and (date.today() - period.do).days > config.ZIP_RECHECK_DAYS
    hl_zip = opendata.download_opendata(
        f"hl-{period.year}ps.zip", required=False, refresh=not closed or fetch.is_refresh_day()
    )
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
    omluvy: dict[int, list[tuple]] = {}
    for r in (opendata.load_omluvy(hl_zip) if hl_zip else []):
        if to_int(r.get("id_organ")) not in (None, period.organ_id):
            continue
        pid, den = to_int(r.get("id_poslanec")), to_date(r.get("den"))
        if pid is not None and den is not None:
            omluvy.setdefault(pid, []).append((den, _clock(r.get("od")), _clock(r.get("do"))))
    n_omluv = 0
    for pid, arr in codes_by_poslanec.items():
        if pid in omluvy:
            before = arr.count(b"M")
            apply_omluvy(arr, votes, omluvy[pid])
            n_omluv += arr.count(b"M") - before
    if votes:
        log.info("Omluvy: %d záznamů, %d hlasování přeznačeno nepřihlášen → omluven", sum(map(len, omluvy.values())), n_omluv)
    zm_mask = [v.zmatecne for v in votes]

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

    vote_dates = [v.datum for v in votes]
    intervals = _mandate_intervals(people, period)
    for osoba in list(codes_by_osoba):
        codes_by_osoba[osoba] = mask_to_mandate(codes_by_osoba[osoba], vote_dates, intervals.get(osoba))

    clubs = _memberships(people, period)
    mandates = _mandates(people, period)

    # --- Projevy
    speeches: list[steno.Speech] = []
    steno_stats = None
    if period.has_speeches:
        pages = steno.collect_pages(period, steno_meta)
        speeches, steno_stats = steno.build_speeches(
            period, pages, steno_meta, {oid: (o.get("prijmeni") or "").strip() for oid, o in people.osoby.items()},
            notes.NameIndex({oid: ((o.get("jmeno") or "").strip(), (o.get("prijmeni") or "").strip())
                             for oid, o in people.osoby.items()}),
        )
        log.info("Projevy %s: %s", period.year, steno_stats)

    sch_dates: dict[int, date] = {}
    for sid, s in schuze_tab.items():
        if to_int(s.get("id_org")) == period.organ_id:
            d = to_date(s.get("od_schuze"))
            n = to_int(s.get("schuze"))
            if n is not None and d:
                sch_dates[n] = d

    # nadpis ze stránky pro body, které v bod_schuze chybí (sloučené rozpravy apod.)
    bod_heading: dict[int, str] = {}
    for sp in speeches:
        if sp.id_bod is not None and sp.id_bod not in bods and sp.heading:
            bod_heading.setdefault(sp.id_bod, sp.heading)

    ready, why = ready_schuze(steno_meta, period.organ_id, date.today(), config.SUMMARY_MIN_AGE_DAYS)
    if why:
        log.info("Schůze zatím neuzavřené pro shrnutí: %s", why)

    by_osoba: dict[int, list[steno.Speech]] = defaultdict(list)
    for sp in speeches:
        if sp.id_osoba is not None:
            by_osoba[sp.id_osoba].append(sp)

    # Poznámky stenografů o osobách (výkřiky, chování) uvnitř cizích vystoupení
    note_names = {
        oid: ((people.osoby[oid].get("jmeno") or "").strip(), (people.osoby[oid].get("prijmeni") or "").strip())
        for oid in set(deputy_rows) | set(by_osoba) if oid in people.osoby
    }
    name_index = notes.NameIndex(note_names)
    notes_by_osoba: dict[int, list[tuple[steno.Speech, str, bool]]] = defaultdict(list)
    note_stats = Counter()
    speakers_at: dict[tuple[int, int], set[int]] = defaultdict(set)
    for sp in speeches:
        if sp.id_osoba:
            speakers_at[(sp.schuze, sp.turn)].add(sp.id_osoba)
    found = [(sp, i, text, tokens, name_index.resolve(tokens))
             for sp in speeches for i, text, tokens in notes.find_notes(sp.paragraphs)]
    resolved_at: dict[tuple[int, int], set[int]] = defaultdict(set)
    for sp, _, _, _, oid in found:
        if oid is not None:
            resolved_at[(sp.schuze, sp.turn)].add(oid)
    for sp, i, text, tokens, oid in found:
        guessed = False
        if oid is None:
            cand = name_index.candidates(tokens)
            if len(cand) > 1:
                near = lambda m, r: set().union(*(m.get((sp.schuze, sp.turn + d), set()) for d in r))
                oid, how = notes.disambiguate(
                    cand, note_names, " ".join(sp.paragraphs[max(0, i - 2): i + 3]),
                    near(speakers_at, (-1, 0, 1)), near(resolved_at, range(-3, 4)),
                )
                if oid is not None:
                    guessed = True
                    note_stats[f"odhad_{how}"] += 1
        if oid is None:
            note_stats["nepřiřazené"] += 1
        elif oid == sp.id_osoba:
            note_stats["o_recnikovi"] += 1  # zůstává v textu jeho vystoupení
        else:
            note_stats["prirazene"] += 1
            notes_by_osoba[oid].append((sp, text, guessed))
    if speeches:
        log.info("Poznámky stenografů %s: %s", period.year, dict(note_stats))

    # --- Osoby
    osoby_ids = set(deputy_rows) | set(by_osoba) | set(notes_by_osoba)
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
        vstats = vote_stats(codes, zm_mask) if codes else None

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
                    "bod_typ": to_int(b.get("id_typ")) if b else None,
                    "bod": group_title(b, sp, bod_heading),
                    "datum": sp.datum,
                    "speeches": [],
                }
            if sp.datum and (g["datum"] is None or sp.datum < g["datum"]):
                g["datum"] = sp.datum
            g["speeches"].append(sp)
        for host, text, guessed in notes_by_osoba.get(osoba, []):
            key = f"{host.schuze}-{bod_key(host)}"
            g = groups.get(key)
            if g is None:
                b = bods.get(host.id_bod) if host.id_bod is not None else None
                g = groups[key] = {
                    "key": key,
                    "schuze": host.schuze,
                    "id_bod": host.id_bod,
                    "bod_cislo": to_int(b.get("bod")) if b else None,
                    "bod_typ": to_int(b.get("id_typ")) if b else None,
                    "bod": group_title(b, host, bod_heading),
                    "datum": host.datum,
                    "speeches": [],
                }
            g.setdefault("notes", []).append((host, text, guessed))
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
            g_notes = g.get("notes", [])
            notes_out = [
                {"text": text, "kde": host.label, "datum": host.datum, "cas": host.cas,
                 "url": steno.page_url(period, host.schuze, host.turn, host.aname)}
                | ({"odhad": 1} if guessed else {})
                for host, text, guessed in g_notes
            ]
            speech_groups_out.append(
                {k: g[k] for k in ("key", "schuze", "id_bod", "bod_cislo", "bod", "datum")}
                | {"speeches": sp_out} | ({"poznamky": notes_out} if notes_out else {})
            )
            spk = [s for s in g["speeches"] if s.role == "speaker"]
            spk_text = "\n\n".join("\n".join(s.paragraphs) for s in spk)
            notes_in = [f"[během vystoupení: {host.label}] {text}" for host, text, _ in g_notes]
            gwords = sum(s.words for s in spk) + sum(len(t.split()) for _, t, _ in g_notes)
            labels_g = Counter(s.label for s in spk)
            g_funkce = _function_from_label(labels_g.most_common(1)[0][0], name) if labels_g else funkce
            hash_src = spk_text + ("\n\n#poznamky\n" + "\n".join(notes_in) if notes_in else "")
            group_index.append(
                {
                    "key": g["key"],
                    "schuze": g["schuze"],
                    "bod": g["bod"],
                    "bod_cislo": g["bod_cislo"],
                    "datum": g["datum"],
                    "n": len(g["speeches"]),
                    "n_speaker": len(spk),
                    "n_pozn": len(g_notes),
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
                    "funkce": g_funkce,
                    "bod_typ": g["bod_typ"],
                    "hash": text_hash(hash_src) if hash_src else None,
                    "uzavreno": g["schuze"] in ready,
                    "text": spk_text,
                    "notes": notes_in,
                }
            )
            full_text = "\n\n".join(["\n".join(s.paragraphs) for s in g["speeches"]] + [t for _, t, _ in g_notes])
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
    from .photos import sync_photos  # import až zde kvůli cyklu (photos používá write_json)

    sync_photos(people, dist, only_periods)
    if fetch.stats["skipped_outage"]:
        log.warning(
            "psp.cz bylo během běhu nedostupné: %d požadavků přeskočeno, %d souborů použito ze starší cache",
            fetch.stats["skipped_outage"], fetch.stats["stale"],
        )
    write_json(
        dist / "data" / "periods.json",
        {
            "periods": infos,
            "generated": datetime.now(ZoneInfo("Europe/Prague")).strftime("%Y-%m-%d %H:%M"),
            "vote_codes": VOTE_CODES,
            "speeches_from_year": config.SPEECHES_FROM_YEAR,
        },
    )
