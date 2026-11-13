"""Stahování souborů z psp.cz s lokální cache a podmíněnými požadavky.

Každý stažený soubor má vedle sebe `<soubor>.meta.json` s hlavičkami
ETag / Last-Modified, takže při dalším běhu se nezměněné soubory nestahují.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from urllib.parse import urljoin

import requests

from . import config

log = logging.getLogger(__name__)

_session: requests.Session | None = None
_last_request = 0.0


def session() -> requests.Session:
    global _session
    if _session is None:
        _session = requests.Session()
        _session.headers["User-Agent"] = config.USER_AGENT
    return _session


def _throttle() -> None:
    global _last_request
    wait = config.REQUEST_DELAY - (time.monotonic() - _last_request)
    if wait > 0:
        time.sleep(wait)
    _last_request = time.monotonic()


def _meta_path(path: Path) -> Path:
    return path.with_name(path.name + ".meta.json")


def fetch(url: str, dest: Path, *, conditional: bool = True, retries: int = 3) -> tuple[Path | None, bool]:
    """Stáhne `url` do `dest`.

    Vrací (cesta nebo None při 404, změnilo_se). Při síťové chybě po
    vyčerpání pokusů vyhodí výjimku – kromě případu, kdy existuje starší
    kopie v cache; pak ji vrátí (a zaloguje varování).
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    meta_file = _meta_path(dest)
    headers = {}
    if conditional and dest.exists() and meta_file.exists():
        meta = json.loads(meta_file.read_text())
        if meta.get("etag"):
            headers["If-None-Match"] = meta["etag"]
        if meta.get("last_modified"):
            headers["If-Modified-Since"] = meta["last_modified"]

    last_exc: Exception | None = None
    for attempt in range(retries):
        _throttle()
        try:
            r = session().get(url, headers=headers, timeout=config.HTTP_TIMEOUT, stream=True)
            if r.status_code == 304:
                return dest, False
            if r.status_code == 404:
                return None, False
            r.raise_for_status()
            tmp = dest.with_name(dest.name + ".part")
            with open(tmp, "wb") as fh:
                for chunk in r.iter_content(1 << 16):
                    fh.write(chunk)
            changed = not dest.exists() or dest.read_bytes() != tmp.read_bytes()
            tmp.replace(dest)
            meta_file.write_text(
                json.dumps(
                    {
                        "url": url,
                        "etag": r.headers.get("ETag"),
                        "last_modified": r.headers.get("Last-Modified"),
                        "fetched": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    }
                )
            )
            return dest, changed
        except requests.RequestException as exc:  # pragma: no cover - síťové chyby
            last_exc = exc
            log.warning("Chyba při stahování %s (pokus %d): %s", url, attempt + 1, exc)
            time.sleep(2 * (attempt + 1))
    if dest.exists():
        log.warning("Používám starší kopii z cache: %s", dest)
        return dest, False
    raise RuntimeError(f"Nepodařilo se stáhnout {url}") from last_exc


def fetch_text(url: str, dest: Path, **kw) -> str | None:
    path, _ = fetch(url, dest, **kw)
    if path is None:
        return None
    return decode_html(path.read_bytes())


def decode_html(raw: bytes) -> str:
    head = raw[:2000].lower()
    enc = "cp1250"
    if b"charset=utf-8" in head or b'charset="utf-8"' in head:
        enc = "utf-8"
    elif b"iso-8859-2" in head:
        enc = "iso-8859-2"
    return raw.decode(enc, errors="replace")


def join(base: str, href: str) -> str:
    return urljoin(base, href)
