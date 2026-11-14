"""Fotografie poslanců z psp.cz.

Podle dokumentace otevřených dat (tabulka poslanec, sloupec foto = 1) jsou fotky na
  /eknih/cdrom/OBDOBI/eknih/OBDOBI/poslanci/iID_OSOBA.jpg   (obvykle 201×250 px)
Stahuje se jen tato velká verze; v seznamu se na webu jen zmenší.

Podmínky užití psp.cz: fotky se nesmí měnit ani upravovat (ani ořezávat), nesmí se
použít komerčně ani pro volební kampaň a u fotky musí být zdroj
„Foto: Kancelář Poslanecké sněmovny“. Web proto fotky zobrazuje beze změny a se zdrojem.

Fotky se nemění, takže se stahují jen jednou (žádné opakované stahování z cache).
Stahování respektuje časový rozpočet (fetch.over_budget) a nejnovější období jde první;
co se nestihne, dotáhne se při dalším běhu.
"""

from __future__ import annotations

import logging
import shutil
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import config, fetch
from .build_data import write_json
from .opendata import People, Period
from .unl import to_int

log = logging.getLogger(__name__)


def photo_url(period: Period, osoba: int) -> str:
    y = period.year
    return f"{config.PSP_BASE}/eknih/cdrom/{y}ps/eknih/{y}ps/poslanci/i{osoba}.jpg"


def _cache(period: Period) -> Path:
    return config.CACHE_DIR / "photos" / str(period.year)


def sync_photos(people: People, dist: Path, only_periods: set[int] | None = None) -> None:
    """Stáhne chybějící fotky a zkopíruje je do dist/data/<obdobi>/foto/<id>.jpg.

    Ke každému období zapíše dist/data/<obdobi>/fotky.json = seznam id osob, které fotku mají.
    """
    periods = [p for p in people.periods if not only_periods or p.num in only_periods]
    periods.sort(key=lambda p: p.num, reverse=True)  # nejnovější první
    stats = {"stazeno": 0, "z_cache": 0, "chybi": 0, "chyba": 0, "rozpocet": 0}
    for period in periods:
        ids = sorted(
            {
                to_int(r["id_osoba"])
                for r in people.poslanci
                if to_int(r.get("id_obdobi")) == period.organ_id and (r.get("foto") or "").strip() == "1"
            }
            - {None}
        )
        todo = []
        for osoba in ids:
            dest = _cache(period) / f"i{osoba}.jpg"
            marker = dest.with_name(dest.name + ".404")
            if dest.exists():
                stats["z_cache"] += 1
            elif not marker.exists():
                todo.append((osoba, dest, marker))

        def get(item):
            osoba, dest, marker = item
            if fetch.over_budget():
                return "rozpocet"
            try:
                path, _ = fetch.fetch(photo_url(period, osoba), dest, conditional=False)
            except RuntimeError as exc:
                log.warning("%s", exc)
                return "chyba"
            if path is None:
                marker.parent.mkdir(parents=True, exist_ok=True)
                marker.write_text("")
                return "chybi"
            return "stazeno"

        if todo:
            log.info("Fotky %s: %d ke stažení (%d v cache)", period.year, len(todo), len(ids) - len(todo))
            with ThreadPoolExecutor(max_workers=max(1, config.FETCH_WORKERS)) as pool:
                for res in pool.map(get, todo):
                    stats[res] += 1

        # zkopírovat do webu
        out_dir = dist / "data" / str(period.num) / "foto"
        have = []
        for osoba in ids:
            src = _cache(period) / f"i{osoba}.jpg"
            if src.exists() and src.stat().st_size > 0:
                out_dir.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(src, out_dir / f"{osoba}.jpg")
                have.append(osoba)
        write_json(dist / "data" / str(period.num) / "fotky.json", have)
    log.info("Fotky: %s", stats)
    if stats["rozpocet"]:
        log.warning("Fotky: vyčerpán časový rozpočet stahování, %d fotek se dotáhne příště", stats["rozpocet"])
