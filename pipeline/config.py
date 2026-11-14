"""Konfigurace pipeline. Hodnoty lze přepsat proměnnými prostředí."""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Základní adresa psp.cz. Pro testy lze nasměrovat na lokální server.
PSP_BASE = os.environ.get("PSP_BASE", "https://www.psp.cz").rstrip("/")
OPENDATA_BASE = f"{PSP_BASE}/eknih/cdrom/opendata"

# Lokální adresáře
CACHE_DIR = Path(os.environ.get("PSP_CACHE_DIR", ROOT / "cache"))
STATE_DIR = Path(os.environ.get("PSP_STATE_DIR", ROOT / "state"))
DIST_DIR = Path(os.environ.get("PSP_DIST_DIR", ROOT / "dist"))

# Od kterého roku začátku volebního období se zpracovávají projevy
# (plné texty, shrnutí, fulltext). Hlasování se zpracovávají vždy všechna.
SPEECHES_FROM_YEAR = int(os.environ.get("SPEECHES_FROM_YEAR", "2017"))

# Slušnost vůči psp.cz: pauza mezi požadavky (s)
REQUEST_DELAY = float(os.environ.get("PSP_REQUEST_DELAY", "0.3"))
HTTP_TIMEOUT = float(os.environ.get("PSP_HTTP_TIMEOUT", "90"))  # čtení odpovědi (s)
CONNECT_TIMEOUT = float(os.environ.get("PSP_CONNECT_TIMEOUT", "20"))  # navázání spojení (s)
# Přizpůsobení pauzy: když psp.cz odmítá spojení, neodpovídá nebo vrací 429/5xx,
# pauza mezi požadavky se zdvojnásobí (nejvýš MAX_DELAY); po BACKOFF_RECOVER
# úspěšných požadavcích v řadě se zase zkrátí na polovinu (nejméně REQUEST_DELAY).
MAX_DELAY = float(os.environ.get("PSP_MAX_DELAY", "30"))
BACKOFF_RECOVER = int(os.environ.get("PSP_BACKOFF_RECOVER", "20"))
USER_AGENT = os.environ.get(
    "PSP_USER_AGENT",
    "PSP-app (https://github.com/JanSevic/PSP-app; nocni zpracovani otevrenych dat)",
)

# Opakované stahování. psp.cz nepodporuje podmíněné požadavky (vždy vrací celý
# soubor), proto se o novém stažení souboru z cache rozhoduje podle stáří dat:
# - ZIPy stenoprotokolů a hlasování uzavřených období se z cache znovu nestahují,
#   kromě týdenní kontroly (REFRESH_WEEKDAY) kvůli případným opravám;
# - ZIP schůze aktuálního období se stahuje znovu, pokud schůze skončila před
#   méně než ZIP_RECHECK_DAYS dny;
# - jednotlivá stránka se stahuje znovu, pokud je mladší než PAGE_RECHECK_DAYS dní.
ZIP_RECHECK_DAYS = int(os.environ.get("PSP_ZIP_RECHECK_DAYS", "60"))
PAGE_RECHECK_DAYS = int(os.environ.get("PSP_PAGE_RECHECK_DAYS", "14"))
REFRESH_WEEKDAY = int(os.environ.get("PSP_REFRESH_WEEKDAY", "6"))  # 0 = pondělí, 6 = neděle, -1 = nikdy
# Časový rozpočet stahování: po jeho vyčerpání se už nestahuje nic nového, zpracuje se
# to, co je v cache, a zbytek se dotáhne při dalším běhu.
FETCH_BUDGET_MINUTES = float(os.environ.get("PSP_FETCH_BUDGET_MINUTES", "180"))
# Souběžné požadavky při stahování jednotlivých stránek
FETCH_WORKERS = int(os.environ.get("PSP_FETCH_WORKERS", "2"))

SITE_SIZE_LIMIT_MB = int(os.environ.get("SITE_SIZE_LIMIT_MB", "950"))

# Shrnutí
SUMMARY_MODEL = os.environ.get("SUMMARY_MODEL", "sonnet")
# 105 volání za noc ≈ 35 % týdenního limitu předplatného (měřeno 13. 11. 2026: ~1 % za 21 volání)
SUMMARY_MAX_CALLS = int(os.environ.get("SUMMARY_MAX_CALLS", "105"))
SUMMARY_MAX_MINUTES = float(os.environ.get("SUMMARY_MAX_MINUTES", "180"))
SUMMARY_BATCH_WORDS = int(os.environ.get("SUMMARY_BATCH_WORDS", "12000"))
SUMMARY_BATCH_GROUPS = int(os.environ.get("SUMMARY_BATCH_GROUPS", "8"))
SUMMARY_GROUP_MAX_WORDS = int(os.environ.get("SUMMARY_GROUP_MAX_WORDS", "60000"))
SUMMARY_MIN_WORDS = int(os.environ.get("SUMMARY_MIN_WORDS", "25"))
SUMMARY_PROMPT_VERSION = "v5"  # v5: citace jako přímá řeč, aby seděla gramatika
# Úsilí (claude --effort): výchozí, a vyšší pro důležitá jednání (viz summarize.effort_for)
# Rozhodnutí autora 13. 11. 2026: Sonnet high pro všechna jednání. Rozlišení podle důležitosti
# (SUMMARY_EFFORT_HIGH, effort_for) zůstává pro případ, že se výchozí úsilí zase sníží.
SUMMARY_EFFORT = os.environ.get("SUMMARY_EFFORT", "high")
SUMMARY_EFFORT_HIGH = os.environ.get("SUMMARY_EFFORT_HIGH", "high")
SUMMARY_HIGH_WORDS = int(os.environ.get("SUMMARY_HIGH_WORDS", "3000"))  # dlouhá skupina
SUMMARY_HIGH_SPEAKERS = int(os.environ.get("SUMMARY_HIGH_SPEAKERS", "50"))  # řečníků k bodu
SUMMARY_HIGH_HEAT = int(os.environ.get("SUMMARY_HIGH_HEAT", "3"))  # poznámek o vyhrocení
