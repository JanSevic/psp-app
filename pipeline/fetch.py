"""Stahování souborů z psp.cz s lokální cache a přizpůsobivou pauzou mezi požadavky.

Každý stažený soubor má vedle sebe `<soubor>.meta.json` s hlavičkami
ETag / Last-Modified, takže při dalším běhu se nezměněné soubory nestahují.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from urllib.parse import urljoin

import requests

from . import config

log = logging.getLogger(__name__)

_local = threading.local()
_lock = threading.Lock()
_last_request = 0.0
_T0 = time.monotonic()


def session() -> requests.Session:
    """Jedna session na vlákno (requests.Session není bezpečná pro sdílení mezi vlákny)."""
    s = getattr(_local, "session", None)
    if s is None:
        s = _local.session = requests.Session()
        s.headers["User-Agent"] = config.USER_AGENT
    return s


_delay: float | None = None  # aktuální pauza mezi požadavky (přizpůsobuje se)
_ok_streak = 0
_fail_streak = 0  # síťové chyby za sebou (bez jakékoli odpovědi serveru)
_outage_until: float | None = None  # jistič: do kdy na psp.cz nechodit (monotonic)
stats = {"stale": 0, "skipped_outage": 0}


def in_outage() -> bool:
    """True = psp.cz je teď vyhodnocené jako nedostupné a nemá se volat.

    Po uplynutí OUTAGE_PROBE_MINUTES vrátí jednomu volajícímu False (zkušební
    požadavek); ostatní mezitím dál čekají na jeho výsledek.
    """
    global _outage_until
    with _lock:
        if _outage_until is None:
            return False
        now = time.monotonic()
        if now < _outage_until:
            return True
        _outage_until = now + config.OUTAGE_PROBE_MINUTES * 60  # tohle vlákno zkouší
        return False


def _net_failure() -> None:
    global _fail_streak, _outage_until
    with _lock:
        _fail_streak += 1
        trip = _outage_until is None and _fail_streak >= config.OUTAGE_FAILS
        if trip:
            _outage_until = time.monotonic() + config.OUTAGE_PROBE_MINUTES * 60
    if trip:
        log.warning(
            "psp.cz neodpovídá (%d chyb za sebou) – dál beru data z cache, chybějící se dotáhnou příště; "
            "zkusím to znovu za %.0f min", _fail_streak, config.OUTAGE_PROBE_MINUTES,
        )


def _server_answered() -> None:
    global _fail_streak, _outage_until
    with _lock:
        was = _outage_until is not None
        _fail_streak, _outage_until = 0, None
    if was:
        log.info("psp.cz zase odpovídá – pokračuji ve stahování")


def last_was_stale() -> bool:
    """Poslední fetch() v tomto vlákně vrátil starší kopii z cache místo nové."""
    return getattr(_local, "stale", False)


def current_delay() -> float:
    return config.REQUEST_DELAY if _delay is None else _delay


def _throttle() -> None:
    """Pauza mezi začátky požadavků – společná pro všechna vlákna."""
    global _last_request
    with _lock:
        wait = current_delay() - (time.monotonic() - _last_request)
        if wait > 0:
            time.sleep(wait)
        _last_request = time.monotonic()


def _backoff(reason: str, retry_after: float | None = None) -> None:
    """Server odmítá nebo nestíhá – prodloužit pauzu."""
    global _delay, _ok_streak
    with _lock:
        old = current_delay()
        new = min(config.MAX_DELAY, max(old * 2, 1.0, retry_after or 0))
        _delay, _ok_streak = new, 0
    if new != old:
        log.info("psp.cz nestíhá (%s) – pauza mezi požadavky %.1f s → %.1f s", reason, old, new)


def _success() -> None:
    """Úspěšný požadavek – po sérii úspěchů pauzu zase zkrátit."""
    global _delay, _ok_streak
    with _lock:
        _ok_streak += 1
        if _delay is None or _delay <= config.REQUEST_DELAY or _ok_streak < config.BACKOFF_RECOVER:
            return
        old = _delay
        new = old / 2
        if new <= max(config.REQUEST_DELAY * 1.5, 0.5):  # blízko výchozí hodnoty → rovnou na ni
            new = config.REQUEST_DELAY
        _delay, _ok_streak = new, 0
    log.info("psp.cz odpovídá – pauza mezi požadavky %.1f s → %.1f s", old, _delay)


def _retry_after(r) -> float | None:
    try:
        return float(r.headers.get("Retry-After", ""))
    except ValueError:
        return None


def elapsed_minutes() -> float:
    return (time.monotonic() - _T0) / 60


def over_budget() -> bool:
    """Vyčerpaný časový rozpočet stahování (config.FETCH_BUDGET_MINUTES)."""
    return elapsed_minutes() > config.FETCH_BUDGET_MINUTES


def is_refresh_day() -> bool:
    """Den týdenní kontroly uzavřených dat (pražský čas)."""
    return datetime.now(ZoneInfo("Europe/Prague")).weekday() == config.REFRESH_WEEKDAY


def _meta_path(path: Path) -> Path:
    return path.with_name(path.name + ".meta.json")


def fetch(url: str, dest: Path, *, conditional: bool = True, retries: int = 3) -> tuple[Path | None, bool]:
    """Stáhne `url` do `dest`.

    Vrací (cesta nebo None při 404, změnilo_se). Při síťové chybě po
    vyčerpání pokusů vyhodí výjimku – kromě případu, kdy existuje starší
    kopie v cache; pak ji vrátí (a zaloguje varování).
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    _local.stale = False
    probing = False
    if _outage_until is not None:
        if in_outage():
            stats["skipped_outage"] += 1
            if dest.exists():
                _local.stale = True
                stats["stale"] += 1
                return dest, False
            raise RuntimeError(f"psp.cz nedostupné, přeskočeno: {url}")
        probing, retries = True, 1
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
            r = session().get(url, headers=headers, timeout=(config.CONNECT_TIMEOUT, config.HTTP_TIMEOUT), stream=True)
            _server_answered()
            if r.status_code == 304:
                _success()
                return dest, False
            if r.status_code == 404:
                _success()
                return None, False
            if r.status_code == 429 or r.status_code >= 500:
                _backoff(f"HTTP {r.status_code}", _retry_after(r))
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
            _success()
            return dest, changed
        except requests.RequestException as exc:
            last_exc = exc
            if isinstance(exc, (requests.ConnectionError, requests.Timeout)):
                _backoff(type(exc).__name__)
                _net_failure()
            log.warning("Chyba při stahování %s (pokus %d): %s", url, attempt + 1, exc)
            if probing or _outage_until is not None:
                break  # jistič vypnul – další pokusy nemají smysl
            time.sleep(max(2 * (attempt + 1), current_delay()))
    if dest.exists():
        _local.stale = True
        stats["stale"] += 1
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
