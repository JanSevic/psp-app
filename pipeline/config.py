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
HTTP_TIMEOUT = float(os.environ.get("PSP_HTTP_TIMEOUT", "90"))
USER_AGENT = os.environ.get(
    "PSP_USER_AGENT",
    "PSP-app (https://github.com/JanSevic/PSP-app; nocni zpracovani otevrenych dat)",
)

# Kolik posledních schůzí aktuálního období se každou noc kontroluje na změnu
# stenoprotokolů (autorizace textů apod.). Ostatní se kontrolují podmíněným
# požadavkem (If-Modified-Since), takže nezměněné soubory se nestahují.
SITE_SIZE_LIMIT_MB = int(os.environ.get("SITE_SIZE_LIMIT_MB", "950"))

# Shrnutí
SUMMARY_MODEL = os.environ.get("SUMMARY_MODEL", "sonnet")
SUMMARY_MAX_CALLS = int(os.environ.get("SUMMARY_MAX_CALLS", "200"))
SUMMARY_MAX_MINUTES = float(os.environ.get("SUMMARY_MAX_MINUTES", "180"))
SUMMARY_BATCH_WORDS = int(os.environ.get("SUMMARY_BATCH_WORDS", "12000"))
SUMMARY_BATCH_GROUPS = int(os.environ.get("SUMMARY_BATCH_GROUPS", "8"))
SUMMARY_GROUP_MAX_WORDS = int(os.environ.get("SUMMARY_GROUP_MAX_WORDS", "60000"))
SUMMARY_MIN_WORDS = int(os.environ.get("SUMMARY_MIN_WORDS", "25"))
SUMMARY_PROMPT_VERSION = "v5"  # v5: citace jako přímá řeč, aby seděla gramatika
# Úsilí (claude --effort): výchozí, a vyšší pro důležitá jednání (viz summarize.effort_for)
SUMMARY_EFFORT = os.environ.get("SUMMARY_EFFORT", "medium")
SUMMARY_EFFORT_HIGH = os.environ.get("SUMMARY_EFFORT_HIGH", "high")
SUMMARY_HIGH_WORDS = int(os.environ.get("SUMMARY_HIGH_WORDS", "3000"))  # dlouhá skupina
SUMMARY_HIGH_SPEAKERS = int(os.environ.get("SUMMARY_HIGH_SPEAKERS", "50"))  # řečníků k bodu
SUMMARY_HIGH_HEAT = int(os.environ.get("SUMMARY_HIGH_HEAT", "3"))  # poznámek o vyhrocení
