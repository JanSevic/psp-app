"""Poznámky stenografů o osobách zapsané během vystoupení jiných řečníků.

Výkřiky ze sálu a chování v jednací síni zapisují stenografové do závorek
uvnitř textu toho, kdo má zrovna slovo, např.:

    (Poslanec Faltýnek z lavice: Zopakovat ten návrh!)
    (Poslanec Lubomír Volný odnáší tabuli s nápisem a pokládá ji před lavice TOP 09, …)

Poznámka se přiřadí osobě, která je jejím podmětem (titul + jméno na začátku
závorky). Pokud jméno nejde určit jednoznačně (jen příjmení a v období je víc
osob se stejným příjmením), poznámka se nepřiřadí nikomu.
"""

from __future__ import annotations

import re
import unicodedata

NOTE_RE = re.compile(r"\(([^()]{4,600})\)")
TITLE_RE = re.compile(
    r"^(?:Posl\.|Poslanec|Poslankyně|Ministr|Ministryně|Premiér|Premiérka|Předseda vlády|Předsedkyně vlády|"
    r"Předseda|Předsedkyně|Místopředseda|Místopředsedkyně|Senátor|Senátorka)"
    r"(?:\s+(?:PSP|vlády|Sněmovny))?\s+"
)
NAME_TOKEN_RE = re.compile(r"[A-ZÁČĎÉĚÍŇÓŘŠŤÚŮÝŽ][a-záčďéěíňóřšťúůýž]+(?:-[A-ZÁČĎÉĚÍŇÓŘŠŤÚŮÝŽ][a-záčďéěíňóřšťúůýž]+)?")


def _fold(s: str) -> str:
    return unicodedata.normalize("NFC", s).strip().lower()


class NameIndex:
    """Rozpoznání osoby podle „Jméno Příjmení“ nebo jen „Příjmení“ v rámci jednoho období."""

    def __init__(self, people: dict[int, tuple[str, str]]):
        # people: {id_osoba: (jmeno, prijmeni)}
        self.full: dict[str, set[int]] = {}
        self.surname: dict[str, set[int]] = {}
        for oid, (jmeno, prijmeni) in people.items():
            if not prijmeni:
                continue
            self.full.setdefault(_fold(f"{jmeno} {prijmeni}"), set()).add(oid)
            self.surname.setdefault(_fold(prijmeni), set()).add(oid)

    def candidates(self, tokens: list[str]) -> set[int]:
        """Všechny osoby, na které jméno sedí (pro rozhodnutí podle kontextu)."""
        for n in range(min(len(tokens), 4), 1, -1):
            hit = self.full.get(_fold(" ".join(tokens[:n])))
            if hit:
                return set(hit)
        for n in range(min(len(tokens), 3), 0, -1):
            hit = self.surname.get(_fold(" ".join(tokens[:n])))
            if hit:
                return set(hit)
        return set()

    def resolve(self, tokens: list[str]) -> int | None:
        # nejdelší shoda: „Jméno Příjmení [Druhé]“, pak jen příjmení (i víceslovné)
        for n in range(min(len(tokens), 4), 1, -1):
            hit = self.full.get(_fold(" ".join(tokens[:n])))
            if hit:
                return next(iter(hit)) if len(hit) == 1 else None
        for n in range(min(len(tokens), 3), 0, -1):
            hit = self.surname.get(_fold(" ".join(tokens[:n])))
            if hit:
                return next(iter(hit)) if len(hit) == 1 else None
        return None


def subject_tokens(note: str) -> list[str]:
    """Jmenná slova hned za titulem („Poslanec Lubomír Volný od pultíku…“ → [Lubomír, Volný])."""
    m = TITLE_RE.match(note)
    if not m:
        return []
    tokens = []
    for raw in note[m.end():].split():
        word = raw.rstrip(":,.;!?")
        if not NAME_TOKEN_RE.fullmatch(word):
            break
        tokens.append(word)
        if word != raw or len(tokens) == 4:  # interpunkce ukončuje jméno („Vondráček: Nesouhlas.“)
            break
    return tokens


def find_notes(paragraphs: list[str]) -> list[tuple[int, str, list[str]]]:
    """[(index odstavce, text poznámky, jmenná slova podmětu)] – jen poznámky s titulem a jménem."""
    out = []
    for i, p in enumerate(paragraphs):
        for m in NOTE_RE.finditer(p):
            note = m.group(1).strip()
            tokens = subject_tokens(note)
            if tokens:
                out.append((i, note, tokens))
    return out


def label_tokens(label: str) -> list[str]:
    """Jméno z konce jmenovky („Místopředseda PSP Jan Kasal“ → [Jan, Kasal])."""
    words = label.rstrip(":").split()
    tokens = []
    for w in reversed(words):
        if not NAME_TOKEN_RE.fullmatch(w):
            break
        tokens.insert(0, w)
    return tokens


def resolve_label(index: NameIndex, label: str) -> int | None:
    """Osoba podle celého jména na konci jmenovky (bez ID osoby v HTML ani v `rec`)."""
    tokens = label_tokens(label)
    for n in range(min(len(tokens), 4), 1, -1):
        hit = index.full.get(_fold(" ".join(tokens[-n:])))
        if hit:
            return next(iter(hit)) if len(hit) == 1 else None
    return None


def _stem(word: str) -> str:
    # hrubý základ slova pro skloňované tvary („Lubomír“ → „Lubom“ ~ „Lubomíra“, „Volný“ → „Vol“ ~ „Volného“)
    return re.escape(word[: max(3, len(word) - 2)])


def disambiguate(
    cand: set[int],
    names: dict[int, tuple[str, str]],
    context: str,
    near_speakers: set[int],
    near_notes: set[int],
) -> tuple[int | None, str | None]:
    """Výběr mezi osobami stejného příjmení podle kontextu poznámky.

    Pořadí pravidel: 1) celé jméno (i skloňované) v okolním textu, 2) jediný z kandidátů
    vystupuje na stejné nebo sousední stránce, 3) jediný z kandidátů je v téže schůzi
    nablízku zmíněn jednoznačnou poznámkou. Jinak se poznámka nepřiřadí.
    """
    by_ctx = {
        o for o in cand
        if names.get(o) and re.search(rf"\b{_stem(names[o][0])}\w*\s+{_stem(names[o][1])}\w*", context)
    }
    if len(by_ctx) == 1:
        return next(iter(by_ctx)), "kontext"
    for found, how in ((cand & near_speakers, "vystoupeni"), (cand & near_notes, "poznamky")):
        if len(found) == 1:
            return next(iter(found)), how
    return None, None
