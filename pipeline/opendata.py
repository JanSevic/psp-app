"""Stažení a načtení otevřených dat psp.cz (poslanci, hlasování, schůze, steno)."""

from __future__ import annotations

import logging
import re
import zipfile
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from . import config, fetch
from .unl import read_from_zip, to_date, to_int, to_time

log = logging.getLogger(__name__)

COLS = {
    "osoby": ["id_osoba", "pred", "prijmeni", "jmeno", "za", "narozeni", "pohlavi", "zmena", "umrti"],
    "poslanec": [
        "id_poslanec", "id_osoba", "id_kraj", "id_kandidatka", "id_obdobi", "web", "ulice", "obec",
        "psc", "email", "telefon", "fax", "psp_telefon", "facebook", "foto",
    ],
    "organy": [
        "id_organ", "organ_id_organ", "id_typ_organu", "zkratka", "nazev_organu_cz", "nazev_organu_en",
        "od_organ", "do_organ", "priorita", "cl_organ_base",
    ],
    "typ_organu": ["id_typ_org", "typ_id_typ_org", "nazev_typ_org_cz", "nazev_typ_org_en", "typ_org_obecny", "priorita"],
    "zarazeni": ["id_osoba", "id_of", "cl_funkce", "od_o", "do_o", "od_f", "do_f"],
    "hl_hlasovani": [
        "id_hlasovani", "id_organ", "schuze", "cislo", "bod", "datum", "cas", "pro", "proti", "zdrzel",
        "nehlasoval", "prihlaseno", "kvorum", "druh_hlasovani", "vysledek", "nazev_dlouhy", "nazev_kratky",
    ],
    "zmatecne": ["id_hlasovani"],
    # omluvy poslanců z jednání Sněmovny; od/do prázdné = celý jednací den
    "omluvy": ["id_organ", "id_poslanec", "den", "od", "do"],
    "schuze": ["id_schuze", "id_org", "schuze", "od_schuze", "do_schuze", "aktualizace", "pozvanka"],
    "bod_schuze": [
        "id_bod", "id_schuze", "id_tisk", "id_typ", "bod", "uplny_naz", "uplny_kon", "poznamka",
        "id_bod_stav", "pozvanka", "rj", "pozn2", "druh_bodu", "id_sd", "zkratka",
    ],
    "steno": ["id_steno", "id_org", "schuze", "turn", "od_steno", "jd", "od_t", "do_t"],
    "steno_bod": ["id_steno", "aname", "id_bod"],
    "rec": ["id_steno", "id_osoba", "aname", "id_bod", "druh"],
}


def opendata_path(name: str) -> Path:
    return config.CACHE_DIR / "opendata" / name


def download_opendata(name: str, required: bool = True, refresh: bool = True) -> Path | None:
    """Stáhne soubor otevřených dat. `refresh=False`: je-li v cache, nestahuje se znovu."""
    cached = opendata_path(name)
    if cached.exists() and (not refresh or fetch.over_budget()):
        log.info("%s: z cache", name)
        return cached
    path, changed = fetch.fetch(f"{config.OPENDATA_BASE}/{name}", cached)
    if path is None:
        if required:
            raise RuntimeError(f"Soubor otevřených dat {name} není k dispozici")
        log.warning("Soubor otevřených dat %s není k dispozici", name)
    else:
        log.info("%s: %s", name, "aktualizováno" if changed else "beze změny")
    return path


# ---------------------------------------------------------------------------
# Poslanci, osoby, orgány


@dataclass
class Period:
    num: int  # pořadí volebního období (1 = 1993)
    organ_id: int
    year: int  # rok v URL (hl-2021ps.zip, /eknih/2021ps/)
    od: date | None
    do: date | None

    @property
    def label(self) -> str:
        end = str(self.do.year) if self.do else ""
        return f"{self.num}. volební období ({self.year}–{end})"

    @property
    def has_speeches(self) -> bool:
        return self.year >= config.SPEECHES_FROM_YEAR


@dataclass
class People:
    osoby: dict[int, dict]
    poslanci: list[dict]  # řádky tabulky poslanec
    organy: dict[int, dict]
    typ_organu: dict[int, dict]
    zarazeni: list[dict]
    periods: list[Period] = field(default_factory=list)


def load_people(zip_path: Path) -> People:
    def rows(table: str) -> list[dict]:
        return read_from_zip(zip_path, rf"^{table}\.unl$", COLS[table])

    osoby = {}
    for r in rows("osoby"):
        oid = to_int(r["id_osoba"])
        if oid is not None:
            osoby[oid] = r
    organy = {to_int(r["id_organ"]): r for r in rows("organy") if to_int(r["id_organ"]) is not None}
    typ = {to_int(r["id_typ_org"]): r for r in rows("typ_organu") if to_int(r["id_typ_org"]) is not None}
    people = People(osoby=osoby, poslanci=rows("poslanec"), organy=organy, typ_organu=typ, zarazeni=rows("zarazeni"))
    people.periods = find_periods(people)
    return people


def find_periods(people: People) -> list[Period]:
    periods = []
    for oid, org in people.organy.items():
        m = re.fullmatch(r"PSP(\d+)", (org.get("zkratka") or "").strip())
        if not m:
            continue
        od = to_date(org.get("od_organ"))
        if od is None:
            continue
        # psp.cz vede 1. období (ČNR od 6/1992, Sněmovna od 1. 1. 1993) pod rokem 1993 (hl-1993ps.zip, /eknih/1993ps/)
        year = max(od.year, 1993)
        periods.append(Period(num=int(m.group(1)), organ_id=oid, year=year, od=od, do=to_date(org.get("do_organ"))))
    periods.sort(key=lambda p: p.num)
    return periods


def person_name(o: dict | None) -> str:
    if not o:
        return "Neznámá osoba"
    return " ".join(x for x in [o.get("jmeno"), o.get("prijmeni")] if x).strip()


def person_full_name(o: dict | None) -> str:
    if not o:
        return "Neznámá osoba"
    parts = [o.get("pred"), o.get("jmeno"), o.get("prijmeni")]
    name = " ".join(x for x in parts if x).strip()
    if o.get("za"):
        name += ", " + o["za"]
    return name


def club_type_ids(people: People) -> set[int]:
    return {tid for tid, t in people.typ_organu.items() if (t.get("nazev_typ_org_cz") or "").strip().lower() == "klub"}


def region_type_ids(people: People) -> set[int]:
    return {
        tid for tid, t in people.typ_organu.items()
        if (t.get("nazev_typ_org_cz") or "").strip().lower() in ("volební kraj", "volebni kraj")
    }


# ---------------------------------------------------------------------------
# Hlasování


@dataclass
class Vote:
    id: int
    schuze: int | None
    cislo: int | None
    bod: int | None
    datum: date | None
    cas: str | None
    pro: int | None
    proti: int | None
    zdrzel: int | None
    nehlasoval: int | None
    prihlaseno: int | None
    kvorum: int | None
    druh: str | None
    vysledek: str | None
    nazev: str
    zmatecne: bool


def load_omluvy(zip_path: Path) -> list[dict]:
    """Omluvy poslanců z jednání (omluvy.unl v hl-<rok>ps.zip)."""
    return read_from_zip(zip_path, r"^omluvy\.unl$", COLS["omluvy"])


def load_votes(zip_path: Path, period: Period) -> tuple[list[Vote], dict[int, bytearray]]:
    """Vrací (seznam hlasování seřazený podle id, {id_poslanec: kódy}).

    Kódy jsou bytearray délky len(votes); znak na pozici i je výsledek
    poslance v hlasování i ('.' = hlasování se ho netýká).
    """
    y = period.year
    raw = read_from_zip(zip_path, rf"^hl{y}s\.unl$", COLS["hl_hlasovani"])
    zmatecne = {to_int(r["id_hlasovani"]) for r in read_from_zip(zip_path, r"^zmatecne\.unl$", COLS["zmatecne"])}
    votes = []
    for r in raw:
        vid = to_int(r["id_hlasovani"])
        if vid is None:
            continue
        nazev = (r.get("nazev_dlouhy") or r.get("nazev_kratky") or "").strip()
        vys = (r.get("vysledek") or "").strip() or None
        votes.append(
            Vote(
                id=vid,
                schuze=to_int(r["schuze"]),
                cislo=to_int(r["cislo"]),
                bod=to_int(r["bod"]),
                datum=to_date(r["datum"]),
                cas=to_time(r["cas"]),
                pro=to_int(r["pro"]),
                proti=to_int(r["proti"]),
                zdrzel=to_int(r["zdrzel"]),
                nehlasoval=to_int(r["nehlasoval"]),
                prihlaseno=to_int(r["prihlaseno"]),
                kvorum=to_int(r["kvorum"]),
                druh=(r.get("druh_hlasovani") or "").strip() or None,
                vysledek=vys,
                nazev=nazev,
                zmatecne=vid in zmatecne or vys not in ("A", "R"),
            )
        )
    votes.sort(key=lambda v: v.id)
    index = {v.id: i for i, v in enumerate(votes)}
    n = len(votes)
    codes: dict[int, bytearray] = {}
    rx = re.compile(rf"^hl{y}h\d+\.unl$", re.IGNORECASE)
    with zipfile.ZipFile(zip_path) as zf:
        for name in sorted(zf.namelist()):
            if not rx.match(Path(name).name):
                continue
            text = zf.read(name).decode("cp1250", errors="replace")
            for line in text.splitlines():
                parts = line.split("|")
                if len(parts) < 3:
                    continue
                try:
                    pid = int(parts[0])
                    vid = int(parts[1])
                except ValueError:
                    continue
                i = index.get(vid)
                if i is None:
                    continue
                code = (parts[2].strip() or "?")[0]
                if code == "B":
                    code = "N"
                arr = codes.get(pid)
                if arr is None:
                    arr = codes[pid] = bytearray(b"." * n)
                arr[i] = ord(code) if ord(code) < 128 else ord("?")
    return votes, codes


# ---------------------------------------------------------------------------
# Schůze a body


def load_bods(zip_path: Path) -> tuple[dict[int, dict], dict[int, dict]]:
    """Vrací ({id_schuze: schuze}, {id_bod: bod}) – preferuje schválený pořad."""
    schuze = {}
    for r in read_from_zip(zip_path, r"^schuze\.unl$", COLS["schuze"]):
        sid = to_int(r["id_schuze"])
        if sid is None:
            continue
        if sid not in schuze or r.get("pozvanka") is None:
            schuze[sid] = r
    bods = {}
    for r in read_from_zip(zip_path, r"^bod_schuze\.unl$", COLS["bod_schuze"]):
        bid = to_int(r["id_bod"])
        if bid is None:
            continue
        if bid not in bods or r.get("pozvanka") is None:
            bods[bid] = r
    return schuze, bods


def bod_title(b: dict | None) -> str | None:
    if not b:
        return None
    name = (b.get("uplny_naz") or b.get("zkratka") or "").strip()
    kon = (b.get("uplny_kon") or "").strip()
    if kon:
        name = f"{name} {kon}".strip()
    return name or None


# ---------------------------------------------------------------------------
# Stenozáznamy – metadata


@dataclass
class StenoTurn:
    id_steno: int
    organ_id: int
    schuze: int
    turn: int
    datum: date | None
    jd: int | None
    od_t: int | None


@dataclass
class StenoMeta:
    turns: dict[int, StenoTurn]  # id_steno -> turn
    by_key: dict[tuple[int, int, int], StenoTurn]  # (organ, schuze, turn)
    rec: dict[int, list[dict]]  # id_steno -> [rec]
    bod_marks: dict[int, list[tuple[int, int]]]  # id_steno -> [(aname, id_bod)]


def load_steno_meta(zip_path: Path | None) -> StenoMeta:
    meta = StenoMeta(turns={}, by_key={}, rec={}, bod_marks={})
    if zip_path is None:
        return meta
    for r in read_from_zip(zip_path, r"^steno\.unl$", COLS["steno"]):
        sid, org, sch, turn = (to_int(r[k]) for k in ("id_steno", "id_org", "schuze", "turn"))
        if None in (sid, org, sch, turn):
            continue
        t = StenoTurn(sid, org, sch, turn, to_date(r["od_steno"]), to_int(r["jd"]), to_int(r["od_t"]))
        meta.turns[sid] = t
        meta.by_key[(org, sch, turn)] = t
    for r in read_from_zip(zip_path, r"^rec\.unl$", COLS["rec"]):
        sid = to_int(r["id_steno"])
        if sid is None:
            continue
        meta.rec.setdefault(sid, []).append(
            {
                "id_osoba": to_int(r["id_osoba"]),
                "aname": to_int(r["aname"]),
                "id_bod": to_int(r["id_bod"]) or None,  # 0 = mimo bod pořadu
                "druh": to_int(r["druh"]),
            }
        )
    for r in read_from_zip(zip_path, r"^steno_bod\.unl$", COLS["steno_bod"]):
        sid, an, bid = to_int(r["id_steno"]), to_int(r["aname"]), to_int(r["id_bod"])
        if sid is None or not bid:
            continue
        meta.bod_marks.setdefault(sid, []).append((an or 0, bid))
    for v in meta.rec.values():
        v.sort(key=lambda x: (x["aname"] is None, x["aname"] or 0))
    for v in meta.bod_marks.values():
        v.sort()
    return meta
