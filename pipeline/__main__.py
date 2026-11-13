"""Příkazová řádka: python -m pipeline <příkaz>

  build        stáhne data z psp.cz a zapíše JSON do dist/data
  summarize    vytvoří chybějící shrnutí (Claude) a zapíše dist/data/*/sum
  write-sums   jen zapíše shrnutí z uloženého stavu, bez volání Claude
  site         zkopíruje web (web/) do dist
  check-size   ověří, že se web vejde do limitu GitHub Pages
  all          build + summarize + site
"""

from __future__ import annotations

import argparse
import logging
import shutil
import sys

from . import build_data, config, summarize


def copy_site() -> None:
    src = config.ROOT / "web"
    for p in src.rglob("*"):
        if p.is_file():
            dest = config.DIST_DIR / p.relative_to(src)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, dest)
    (config.DIST_DIR / ".nojekyll").write_text("")


def check_size() -> int:
    total = sum(p.stat().st_size for p in config.DIST_DIR.rglob("*") if p.is_file())
    files = sum(1 for p in config.DIST_DIR.rglob("*") if p.is_file())
    mb = total / 1024 / 1024
    print(f"Velikost webu: {mb:.1f} MB, {files} souborů (limit {config.SITE_SIZE_LIMIT_MB} MB)")
    for sub in sorted(config.DIST_DIR.iterdir()):
        if sub.is_dir():
            s = sum(p.stat().st_size for p in sub.rglob("*") if p.is_file()) / 1024 / 1024
            print(f"  {sub.name}: {s:.1f} MB")
    data = config.DIST_DIR / "data"
    if data.exists():
        for sub in sorted(data.iterdir()):
            if sub.is_dir():
                s = sum(p.stat().st_size for p in sub.rglob("*") if p.is_file()) / 1024 / 1024
                print(f"  data/{sub.name}: {s:.1f} MB")
    if mb > config.SITE_SIZE_LIMIT_MB:
        print("::error::Web je větší než limit GitHub Pages – viz README, sekce Velikost webu.")
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="pipeline")
    ap.add_argument("cmd", choices=["build", "summarize", "write-sums", "site", "check-size", "all"])
    ap.add_argument("--periods", help="jen vybraná období, např. 9,10")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    only = {int(x) for x in a.periods.split(",")} if a.periods else None
    if a.cmd in ("build", "all"):
        build_data.build(only_periods=only)
    if a.cmd in ("summarize", "all"):
        summarize.summarize()
    if a.cmd == "write-sums":
        summarize.summarize(run_llm=False)
    if a.cmd in ("site", "all"):
        copy_site()
    if a.cmd == "check-size":
        return check_size()
    return 0


if __name__ == "__main__":
    sys.exit(main())
