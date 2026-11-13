"""Stažení a parsování stenoprotokolů ze schůzí Poslanecké sněmovny.

Stenoprotokol schůze tvoří stránky `sSSSTTT.htm` (SSS = číslo schůze,
TTT = číslo stenozáznamu / „turn“, cca 10 minut jednání). Na stránce
začíná každé vystoupení odstavcem s odkazem na osobu řečníka
(`detail.sqw?id=<id_osoba>`) a kotvou `r<aname>`. Pokud vystoupení
pokračuje na další stránce, další stránka začíná odstavci bez řečníka.

Otevřená data (steno.zip, tabulka `rec`) přiřazují ke každé kotvě osobu,
bod pořadu a druh vystoupení (předsedající / řečník). Pokud tabulka
chybí, použije se osoba z odkazu v textu.
"""

from __future__ import annotations

import difflib
import logging
import re
import zipfile
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import lxml.html

from . import config, fetch
from .opendata import Period, StenoMeta

log = logging.getLogger(__name__)

PAGE_RE = re.compile(r"^s(\d{3})(\d{3,})\.html?$", re.IGNORECASE)
ANCHOR_RE = re.compile(r"^r(\d+)$")
PERSON_HREF_RE = re.compile(r"detail\.sqw\?(?:[^\"']*&)?id=(\d+)", re.IGNORECASE)
NAV_HREF_RE = re.compile(r"(^|/)(s\d+\.htm|index\.htm|\d+-\d+\.html)", re.IGNORECASE)
CHAIR_PREFIXES = (
    "předsedající",
    "předseda psp",
    "předsedkyně psp",
    "místopředseda psp",
    "místopředsedkyně psp",
)


def steno_base(period: Period) -> str:
    return f"{config.PSP_BASE}/eknih/{period.year}ps/stenprot/"


def page_url(period: Period, schuze: int, turn: int, aname: int | None = None) -> str:
    url = f"{steno_base(period)}{schuze:03d}schuz/s{schuze:03d}{turn:03d}.htm"
    if aname is not None:
        url += f"#r{aname}"
    return url


# ---------------------------------------------------------------------------
# Stahování


def _cache(period: Period) -> Path:
    return config.CACHE_DIR / "steno" / str(period.year)


def list_zip_urls(period: Period) -> dict[int, str]:
    """{číslo schůze: URL ZIPu} podle indexové stránky ZIPů."""
    base = steno_base(period) + "zip/"
    for idx in ("index.htm", ""):
        html = fetch.fetch_text(base + idx, _cache(period) / f"zip-index{'-' + idx if idx else ''}.html")
        if not html:
            continue
        root = lxml.html.fromstring(html)
        out = {}
        for a in root.iter("a"):
            href = a.get("href") or ""
            if not href.lower().endswith(".zip"):
                continue
            m = re.search(r"(\d+)schuz\.zip$", href, re.IGNORECASE) or re.search(r"(\d+)\.zip$", href)
            if not m:
                continue
            out[int(m.group(1))] = fetch.join(base + idx, href)
        if out:
            return out
    log.warning("Pro období %s jsem nenašel ZIPy stenoprotokolů", period.year)
    return {}


PageRef = tuple  # ("zip", cesta, jméno v ZIPu) nebo ("file", cesta)


def load_page(ref: PageRef) -> str:
    if ref[0] == "zip":
        with zipfile.ZipFile(ref[1]) as zf:
            return fetch.decode_html(zf.read(ref[2]))
    return fetch.decode_html(Path(ref[1]).read_bytes())


def collect_pages(period: Period, meta: StenoMeta) -> dict[tuple[int, int], PageRef]:
    """Stáhne (nebo z cache použije) všechny stránky stenoprotokolů období.

    Vrací {(schuze, turn): odkaz na stránku}; obsah se čte až při parsování.
    """
    pages: dict[tuple[int, int], PageRef] = {}
    zips = list_zip_urls(period)
    for sch, url in sorted(zips.items()):
        dest = _cache(period) / "zip" / Path(url).name
        path, changed = fetch.fetch(url, dest)
        if path is None:
            continue
        try:
            with zipfile.ZipFile(path) as zf:
                for name in zf.namelist():
                    m = PAGE_RE.match(Path(name).name)
                    if not m:
                        continue
                    pages[(int(m.group(1)), int(m.group(2)))] = ("zip", path, name)
        except zipfile.BadZipFile:
            log.warning("Poškozený ZIP %s, smažu ho z cache", path)
            path.unlink(missing_ok=True)

    # Stránky, které v ZIPech chybí (typicky probíhající schůze) – po jedné.
    today = date.today()
    missing = [
        t for t in meta.turns.values()
        if t.organ_id == period.organ_id and (t.schuze, t.turn) not in pages
    ]
    fetched = 0
    for t in sorted(missing, key=lambda t: (t.schuze, t.turn)):
        dest = _cache(period) / "pages" / f"s{t.schuze:03d}{t.turn:03d}.htm"
        recent = t.datum is None or (today - t.datum).days <= 60
        missing_marker = dest.with_name(dest.name + ".404")
        if not recent:
            if dest.exists():
                pages[(t.schuze, t.turn)] = ("file", dest)
                continue
            if missing_marker.exists():  # neveřejné jednání apod.
                continue
        path, _ = fetch.fetch(page_url(period, t.schuze, t.turn), dest)
        fetched += 1
        if path is not None:
            pages[(t.schuze, t.turn)] = ("file", path)
        else:
            missing_marker.parent.mkdir(parents=True, exist_ok=True)
            missing_marker.write_text("")
    log.info("Období %s: %d stránek stenoprotokolů (%d ZIPů, %d stránek zvlášť)", period.year, len(pages), len(zips), fetched)
    return pages


# ---------------------------------------------------------------------------
# Parsování jedné stránky


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


@dataclass
class ParsedSpeech:
    aname: int | None
    link_osoba: int | None
    label: str
    paragraphs: list[str] = field(default_factory=list)
    heading: str | None = None


@dataclass
class ParsedPage:
    continuation: list[str]  # odstavce před prvním řečníkem
    speeches: list[ParsedSpeech]
    headings: list[str]


def _anchor_num(el) -> int | None:
    for attr in ("id", "name"):
        v = el.get(attr)
        if v:
            m = ANCHOR_RE.match(v.strip())
            if m:
                return int(m.group(1))
    return None


def _inside_p(el, stop) -> bool:
    p = el.getparent()
    while p is not None and p is not stop:
        if p.tag == "p":
            return True
        p = p.getparent()
    return False


def _is_bold_only(p, text: str) -> bool:
    bold = "".join(_norm(b.text_content()) for b in p.iter("b", "strong"))
    return bool(bold) and _norm(bold) == text


def _is_nav(p, text: str) -> bool:
    links = list(p.iter("a"))
    if not links or len(text) > 120:
        return False
    return any(NAV_HREF_RE.search(a.get("href") or "") for a in links) and not any(
        PERSON_HREF_RE.search(a.get("href") or "") for a in links
    )


def parse_page(html: str) -> ParsedPage:
    root = lxml.html.fromstring(html)
    for bad in root.xpath("//script|//style|//noscript"):
        bad.drop_tree()
    for br in root.iter("br"):  # „10.<br>Návrh…“ → „10. Návrh…“
        br.tail = " " + (br.tail or "")
    container = None
    for cid in ("main-content", "body", "content"):
        found = root.xpath(f"//*[@id='{cid}']")
        if found:
            container = found[0]
            break
    if container is None:
        body = root.find("body")
        container = body if body is not None else root

    page = ParsedPage(continuation=[], speeches=[], headings=[])
    pending_anchor: int | None = None
    current_heading: str | None = None

    for el in container.iter():
        if not isinstance(el.tag, str):
            continue
        if el.tag == "a" and el.get("href") is None:
            n = _anchor_num(el)
            if n is not None and not _inside_p(el, container):
                pending_anchor = n
            continue
        if el.tag != "p":
            continue
        text = _norm(el.text_content())
        if not text:
            # prázdný odstavec může nést kotvu
            for a in el.iter():
                n = _anchor_num(a)
                if n is not None:
                    pending_anchor = n
            continue
        if _is_nav(el, text):
            continue

        # kotvy uvnitř odstavce
        inner_anchor = None
        for a in el.iter():
            n = _anchor_num(a)
            if n is not None:
                inner_anchor = n
                break

        speaker = None
        for a in el.iter("a"):
            m = PERSON_HREF_RE.search(a.get("href") or "")
            if not m:
                continue
            label = _norm(a.text_content())
            if label and text.startswith(label[: min(len(label), 12)]):
                speaker = (int(m.group(1)) or None, label)  # id=0 se v datech občas vyskytuje
            break
        if speaker is None:
            # členové vlády mají odkaz na vlada.cz místo detail.sqw; osoba se doplní z `rec`
            first_a = next((a for a in el.iter("a") if a.get("href")), None)
            if first_a is not None:
                label = _norm(first_a.text_content())
                if 3 < len(label) < 200 and text.startswith(label) and text[len(label):].lstrip().startswith(":"):
                    speaker = (None, label)

        if speaker is None and _is_bold_only(el, text):
            current_heading = text
            page.headings.append(text)
            continue

        if speaker is None:
            # starší formát: řečník jen tučně („<b>Poslanec X Y:</b> text“)
            first_b = next(iter(el.iter("b", "strong")), None)
            if first_b is not None:
                blabel = _norm(first_b.text_content())
                if (
                    blabel.endswith(":")
                    and 3 < len(blabel) < 150
                    and text.startswith(blabel)
                    and len(text) > len(blabel)
                ):
                    speaker = (None, blabel.rstrip(":").strip())

        if speaker is not None:
            osoba, label = speaker
            rest = text[len(label):] if text.startswith(label) else text
            rest = re.sub(r"^[\s:.\-–]+", "", rest)
            aname = inner_anchor if inner_anchor is not None else pending_anchor
            sp = ParsedSpeech(aname=aname, link_osoba=osoba, label=label.rstrip(":"), heading=current_heading)
            if rest:
                sp.paragraphs.append(rest)
            page.speeches.append(sp)
            pending_anchor = None
            continue

        if page.speeches:
            page.speeches[-1].paragraphs.append(text)
        else:
            page.continuation.append(text)
    return page


# ---------------------------------------------------------------------------
# Sestavení vystoupení celého období


@dataclass
class Speech:
    id: str
    schuze: int
    turn: int
    aname: int | None
    datum: date | None
    cas: str | None
    id_osoba: int | None
    label: str
    role: str  # "chair" | "speaker"
    id_bod: int | None
    heading: str | None
    paragraphs: list[str]

    @property
    def words(self) -> int:
        return sum(len(p.split()) for p in self.paragraphs)


def _guess_role(label: str) -> str:
    low = label.lower()
    return "chair" if low.startswith(CHAIR_PREFIXES) else "speaker"


def _align_by_osoba(parsed: list[ParsedSpeech], recs: list[dict]) -> dict[int, dict]:
    """Přiřazení vystoupení k záznamům `rec` podle shodné posloupnosti osob.

    Stránky ze ZIPů nemají kotvy `r<aname>`, takže při různém počtu vystoupení
    na stránce a v datech nelze párovat prostě podle pořadí.
    """
    if not recs or not parsed or len(recs) == len(parsed):
        return {}
    a = [ps.link_osoba for ps in parsed]
    b = [r.get("id_osoba") for r in recs]
    out = {}
    for blk in difflib.SequenceMatcher(None, a, b, autojunk=False).get_matching_blocks():
        for i in range(blk.size):
            out[blk.a + i] = recs[blk.b + i]
    return out


def build_speeches(period: Period, pages: dict[tuple[int, int], PageRef], meta: StenoMeta) -> tuple[list[Speech], dict]:
    speeches: list[Speech] = []
    stats = {"pages": 0, "speeches": 0, "matched_rec": 0, "by_order": 0, "no_rec": 0, "osoba_mismatch": 0, "continuations": 0}
    last_in_schuze: dict[int, Speech] = {}
    current_bod: dict[int, int] = {}
    for (sch, turn) in sorted(pages):
        try:
            html = load_page(pages[(sch, turn)])
            parsed = parse_page(html)
        except Exception as exc:  # pragma: no cover - neočekávané HTML
            log.warning("Nelze parsovat s%03d%03d: %s", sch, turn, exc)
            continue
        stats["pages"] += 1
        st = meta.by_key.get((period.organ_id, sch, turn))
        recs = meta.rec.get(st.id_steno, []) if st else []
        if recs and not parsed.speeches:
            stats["pages_no_speaker"] = stats.get("pages_no_speaker", 0) + 1
            if stats["pages_no_speaker"] <= 2:
                log.warning(
                    "Stránka s%03d%03d má v datech %d vystoupení, ale parser žádné nenašel. Ukázka HTML:\n%s",
                    sch, turn, len(recs), html[:2500],
                )
        rec_by_aname = {r["aname"]: r for r in recs if r["aname"] is not None}
        marks = meta.bod_marks.get(st.id_steno, []) if st else []
        cas = None
        if st and st.od_t is not None and st.od_t >= 0:
            cas = f"{st.od_t // 60:02d}:{st.od_t % 60:02d}"

        prev = last_in_schuze.get(sch)
        if parsed.continuation and prev is not None and prev.turn == turn - 1:
            prev.paragraphs.extend(parsed.continuation)
            stats["continuations"] += 1

        aligned = _align_by_osoba(parsed.speeches, recs)
        for k, ps in enumerate(parsed.speeches):
            r = rec_by_aname.get(ps.aname) if ps.aname is not None else None
            if r is not None:
                stats["matched_rec"] += 1
            elif recs and len(recs) == len(parsed.speeches):
                r = recs[k]
                stats["by_order"] += 1
            elif k in aligned:
                r = aligned[k]
                stats["by_align"] = stats.get("by_align", 0) + 1
            else:
                stats["no_rec"] += 1
            osoba = ps.link_osoba
            druh = r.get("druh") if r else None
            if r and r.get("id_osoba"):
                if osoba and r["id_osoba"] != osoba:
                    # `rec` je v turnu posunuté (chybí v něm vystoupení) – odkaz na stránce
                    # je spolehlivější; druh patří jinému vystoupení, roli odhadneme z jmenovky
                    stats["osoba_mismatch"] += 1
                    druh = None
                else:
                    osoba = r["id_osoba"]
            # rec.id_bod bývá nevyplněné (0) i uprostřed dlouhé rozpravy. Pak platí
            # poslední značka bodu na této stránce před vystoupením, jinak bod
            # předchozího vystoupení téže schůze (bod pokračuje, dokud nezačne další).
            id_bod = r.get("id_bod") if r else None
            if id_bod is None:
                an = ps.aname if ps.aname is not None else (r.get("aname") if r else None)
                cand = [b for a, b in marks if an is not None and a <= an]
                id_bod = cand[-1] if cand else current_bod.get(sch)
            if id_bod is not None:
                current_bod[sch] = id_bod
            if druh in (2, 4):
                role = "chair"
            elif druh in (3, 5):
                role = "speaker"
            else:
                role = _guess_role(ps.label)
            aname = ps.aname if ps.aname is not None else (r.get("aname") if r else None)
            sp = Speech(
                id=f"{sch}-{turn}-{aname if aname is not None else 'k' + str(k)}",
                schuze=sch,
                turn=turn,
                aname=aname,
                datum=st.datum if st else None,
                cas=cas,
                id_osoba=osoba,
                label=ps.label,
                role=role,
                id_bod=id_bod,
                heading=ps.heading,
                paragraphs=ps.paragraphs,
            )
            speeches.append(sp)
            last_in_schuze[sch] = sp
            stats["speeches"] += 1
    return speeches, stats
