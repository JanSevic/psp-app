# PSP-app – kontext pro Claude Code

Webová aplikace s přehledem hlasování a projevů v Poslanecké sněmovně PČR po jednotlivých
osobách a volebních obdobích. Data se stahují každou noc přes GitHub Actions z otevřených dat
psp.cz a nasazují se jako statický web na GitHub Pages.

**Aktuální stav, neověřené předpoklady a další kroky jsou v [HANDOFF.md](HANDOFF.md) – přečti ho před prací.**

## Spolupráce s autorem (Honza)

- Komunikuj česky.
- **Nepředpokládej nic, co nebylo výslovně řečeno.** Před netriviálním rozhodnutím (rozsah,
  chování UI, co se zobrazuje, co se shrnuje, náklady) se nejdřív zeptej. Technické detaily
  implementace můžeš rozhodnout sám, ale v odpovědi je jasně označ jako svůj návrh.
- Vzhled webu je odsouhlasený návrh „A1 + sloupec z A2“ (14. 11. 2026, plátno „PSP-app – návrh
  vzhledu“ v Claude Design): tmavě modrá lišta, Source Serif 4 / Source Sans 3 (hostované ve
  `web/fonts`), údaje o osobě ve sloupci vlevo, pod 1024 px hlavička přes celou šířku. Neměň ho bez zadání.
- Pokud v kódu nebo dokumentaci použiješ odborný termín, který nemusí být jasný, vysvětli ho.

## Příkazy

```bash
pip install -r requirements.txt && npm ci
python -m pytest -q                       # testy (proti napodobenině psp.cz v tests/fake_psp.py)
python -m pipeline build [--periods 9,10] # stažení + zpracování → dist/data, state/work
python -m pipeline summarize              # shrnutí přes `claude` CLI (potřebuje CLAUDE_CODE_OAUTH_TOKEN)
python -m pipeline write-sums             # zápis shrnutí bez volání Claude
python -m pipeline site                   # zkopíruje web/ do dist/
node scripts/build-search.mjs             # fulltextový index Pagefind → dist/pagefind
python -m pipeline check-size             # kontrola limitu GitHub Pages (1 GB)
cd dist && python -m http.server 8000     # lokální náhled
```

Proměnné prostředí (viz `pipeline/config.py`): `PSP_BASE`, `PSP_CACHE_DIR`, `PSP_STATE_DIR`,
`PSP_DIST_DIR`, `PSP_REQUEST_DELAY`, `SPEECHES_FROM_YEAR` (2017), `SUMMARY_MODEL`,
`SUMMARY_MAX_CALLS`, `SUMMARY_MAX_MINUTES`, `SUMMARY_MIN_WORDS`, `SUMMARY_EFFORT`,
`SUMMARY_EFFORT_HIGH`, `SITE_SIZE_LIMIT_MB`, `PSP_FETCH_BUDGET_MINUTES` (180),
`PSP_FETCH_WORKERS` (2), `PSP_ZIP_RECHECK_DAYS` (60), `PSP_PAGE_RECHECK_DAYS` (14), `PSP_MIN_REFETCH_DAYS` (7), `PSP_REFRESH_WEEKDAY` (6 = neděle),
`PSP_CONNECT_TIMEOUT` (20), `PSP_MAX_DELAY` (30), `PSP_BACKOFF_RECOVER` (20),
`PSP_OUTAGE_FAILS` (8), `PSP_OUTAGE_PROBE_MINUTES` (10).

## Struktura

```
pipeline/
  config.py      konfigurace (env)
  fetch.py       stahování s cache, časovým rozpočtem a přizpůsobivou pauzou (backoff)
  unl.py         parser UNL (pipe, cp1250, escapy \), převody datumů
  opendata.py    načtení tabulek: osoby/poslanci/orgány/zařazení, hlasování, schůze/body, steno metadata
  steno.py       stažení stenoprotokolů (ZIPy + chybějící stránky) a parsování HTML na vystoupení
  build_data.py  sestavení JSON pro web + vstupy pro shrnutí a fulltext
  photos.py      fotky poslanců z psp.cz (jen jednou, beze změny, se zdrojem „Foto: Kancelář PS“)
  summarize.py   shrnutí osoba × bod přes `claude -p`, úložiště state/summaries.jsonl.gz
  __main__.py    CLI
scripts/build-search.mjs   Pagefind index z state/work/search-*.jsonl.gz
web/                        statický frontend (vanilla JS, hash routing, bez build kroku)
tests/                      pytest + fake_psp.py (napodobenina struktury psp.cz)
.github/workflows/nightly.yml  noční běh (půlnoc Praha) + deploy na Pages
.github/workflows/web.yml      „Aktualizace vzhledu“: push do web/ → přenasazení bez stahování dat
.github/workflows/tests.yml    testy při pushi
```

## Konvence

- Python 3.12, bez těžkých závislostí (requests, lxml). Frontend bez frameworku a bez build kroku.
- Výstupní JSON je kompaktní; plné texty projevů jako `.json.gz` (frontend rozbaluje přes
  `DecompressionStream`, umí i nekomprimovanou odpověď).
- Slušnost vůči psp.cz: přizpůsobivá pauza mezi požadavky, nestahovat znovu, co se nemění, User-Agent s odkazem na repo.
- Shrnutí se cachují podle hashe textu + `SUMMARY_PROMPT_VERSION`; změna promptu = zvýšit verzi
  (pak se vše shrne znovu – to stojí limit předplatného, nejdřív se zeptej).
