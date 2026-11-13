"""Čtení souborů UNL z otevřených dat psp.cz.

Formát: jeden řádek = jeden záznam, oddělovač `|`, na konci řádku je
oddělovač navíc, kódování Windows-1250, prázdná hodnota = null.
Znak `\\` escapuje oddělovač, zpětné lomítko a konec řádku (formát Informix).
"""

from __future__ import annotations

import io
import re
import zipfile
from datetime import date
from pathlib import Path
from typing import Iterator


def iter_records(data: bytes) -> Iterator[list[str | None]]:
    text = data.decode("cp1250", errors="replace")
    fields: list[str | None] = []
    buf: list[str] = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch == "\\" and i + 1 < n:
            nxt = text[i + 1]
            if nxt == "\r" and i + 2 < n and text[i + 2] == "\n":
                buf.append("\n")
                i += 3
                continue
            buf.append(nxt)
            i += 2
            continue
        if ch == "|":
            val = "".join(buf)
            fields.append(val if val.strip() != "" else None)
            buf = []
        elif ch == "\n":
            tail = "".join(buf).strip()
            if fields or tail:
                if tail:
                    fields.append(tail)
                yield fields
            fields, buf = [], []
        elif ch != "\r":
            buf.append(ch)
        i += 1
    tail = "".join(buf).strip()
    if fields or tail:
        if tail:
            fields.append(tail)
        yield fields


def read_table(data: bytes, columns: list[str]) -> list[dict]:
    rows = []
    for rec in iter_records(data):
        row = {c: (rec[k].strip() if k < len(rec) and rec[k] is not None else None) for k, c in enumerate(columns)}
        rows.append(row)
    return rows


def read_from_zip(zip_path: Path, name_pattern: str, columns: list[str]) -> list[dict]:
    """Načte všechny soubory v ZIPu, jejichž jméno odpovídá regexu."""
    rx = re.compile(name_pattern, re.IGNORECASE)
    rows: list[dict] = []
    with zipfile.ZipFile(zip_path) as zf:
        names = sorted(n for n in zf.namelist() if rx.search(Path(n).name))
        for name in names:
            rows.extend(read_table(zf.read(name), columns))
    return rows


def to_int(v: str | None) -> int | None:
    if v is None or v == "":
        return None
    try:
        return int(v)
    except ValueError:
        try:
            return int(float(v))
        except ValueError:
            return None


def to_date(v: str | None) -> date | None:
    if not v:
        return None
    v = v.strip()
    m = re.match(r"(\d{4})-(\d{1,2})-(\d{1,2})", v)
    if m:
        y, mo, d = map(int, m.groups())
    else:
        m = re.match(r"(\d{1,2})\.\s*(\d{1,2})\.\s*(\d{4})", v)
        if not m:
            return None
        d, mo, y = map(int, m.groups())
    if y <= 1900:
        return None
    try:
        return date(y, mo, d)
    except ValueError:
        return None


def to_time(v: str | None) -> str | None:
    """'HH:MM' nebo část datetime -> 'HH:MM'."""
    if not v:
        return None
    m = re.search(r"(\d{1,2}):(\d{2})", v)
    if not m:
        return None
    return f"{int(m.group(1)):02d}:{m.group(2)}"


def zip_bytes(files: dict[str, bytes]) -> bytes:
    """Pomocná funkce pro testy: vytvoří ZIP v paměti."""
    bio = io.BytesIO()
    with zipfile.ZipFile(bio, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return bio.getvalue()
