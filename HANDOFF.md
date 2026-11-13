# Předávací dokument – PSP-app (stav k 13. 11. 2026)

Projekt vznikl v prostředí, odkud **nebyl dosažitelný psp.cz** (blokovaný proxy) a nefungoval
GitHub CLI. Proto kód nikdy neběžel proti skutečným datům. Ověřený je jen proti napodobenině
(`tests/fake_psp.py`), která odráží **moje předpoklady** o formátu. Hlavním úkolem teď je
tyto předpoklady ověřit na skutečných datech a opravit, co nesedí.

## 1. Zadání (odsouhlasené s autorem)

Původní požadavek: aplikace, která projde web Poslanecké sněmovny (ideálně přes API),
stenoprotokoly ze všech schůzí a výsledky hlasování. Pro každého poslance pak ukáže:

1. **Přehled všech hlasování**: název hlasování, zda se účastnil a jak hlasoval.
   Název prokliká na detail hlasování.
2. **Log toho, co řekl** ve Sněmovně: primárně jeho vlastní slova, s možností dostat se
   ke kontextu celého jednání.
3. **Zjednodušenou verzi** s krátkým shrnutím, co řekl, a proklikem na plné znění.

Upřesnění z doptávání (odpovědi autora):

| Otázka | Rozhodnutí |
|---|---|
| Forma | Webová aplikace, sběr dat přes GitHub Actions každou půlnoc |
| Volební období | Hlasování **všechna dostupná**. Projevy (texty, shrnutí, fulltext) **od 2017**; starší autor vyřeší později (limit 1 GB GitHub Pages) |
| Shrnutí | Přes **autorovo Claude předplatné** (token z `claude setup-token` v GitHub Secrets) |
| Starší projevy bez shrnutí | **Postupně po dávkách**: každou noc nejdřív nové, pak starší od nejnovějších |
| Granularita shrnutí | **Bod pořadu**: jedno shrnutí všeho, co osoba řekla k jednomu bodu |
| Délka shrnutí | **Odrážky** |
| „Kontext celého jednání“ | **Odkaz na psp.cz** (na místo ve stenoprotokolu) |
| Proklik z hlasování | **Stránka hlasování na psp.cz** |
| „Zápisy“ | Nic navíc, stačí hlasování a stenoprotokoly |
| Kdo má stránku | **Všichni mluvčí** včetně neposlanců (ministři, hosté) a vystoupení **v roli předsedajícího** |
| Více období | **Samostatná stránka za každé období** |
| Funkce navíc | Filtry v hlasování, statistiky poslance, fulltext v projevech, seznam a hledání poslanců |
| Hosting | GitHub Pages, repo `JanSevic/PSP-app` |
| Čas běhu | Půlnoc **pražského času** (i po změně na letní čas) |
| Vzhled | Zatím bez požadavků, ladit se bude společně později |

Moje návrhy, které autor schválil („ano, pusť se do toho“):
- řazení hlasování od nejnovějších a označení zmatečných hlasování;
- opakované stažení stenoprotokolů, které se změnily (autorizace textů);
- globální fulltext přes všechny mluvčí;
- strop počtu volání Claude na jeden běh;
- komprese textů kvůli limitu 1 GB a kontrola velikosti v buildu.

**Rozhodnuto bez výslovného souhlasu (autorovi oznámeno až po dokončení, potvrdit):**
- body, kde osoba jen řídila schůzi, se neshrnují (zobrazí se „Pouze řízení schůze“);
- vystoupení kratší než 25 slov se místo shrnutí zobrazí celá;
- výchozí model shrnutí je `sonnet`, dávky po max. 8 bodech nebo ~12 000 slovech,
  strop 200 volání a 180 minut za běh;
- velmi dlouhé skupiny (>60 000 slov) se pro shrnutí zkracují;
- úvodní stránka má záložky Poslanci / Ostatní řečníci / Hledat v projevech;
- účast = přítomen (A, N, C, F, K) / všechna hlasování během mandátu kromě W.

## 2. Architektura v kostce

```
nightly.yml: gate (půlnoc Praha) → build → deploy
  build:
    cache restore (cache/)                      ← stažené ZIPy, stránky, meta (ETag)
    gh release download state                   ← state/summaries.jsonl.gz
    python -m pipeline build                    → dist/data/**, state/work/*.jsonl.gz
    python -m pipeline summarize                → claude -p … ; dist/data/*/sum/*.json
    gh release upload state                     ← uloží shrnutí (i při chybě)
    pipeline site + build-search.mjs + check-size
    upload-pages-artifact → deploy-pages
```

Zdroje (`PSP_BASE=https://www.psp.cz`):
- `eknih/cdrom/opendata/poslanci.zip`: `osoby`, `poslanec`, `organy`, `typ_organu`, `zarazeni`
- `eknih/cdrom/opendata/hl-<rok>ps.zip`: `hl<rok>s.unl` (hlasování), `hl<rok>h<N>.unl`
  (hlasy poslanců), `zmatecne.unl`
- `eknih/cdrom/opendata/schuze.zip`: `schuze`, `bod_schuze`
- `eknih/cdrom/opendata/steno.zip`: `steno` (turny), `rec` (vystoupení: osoba, aname, bod, druh),
  `steno_bod`
- `eknih/<rok>ps/stenprot/zip/index.htm` → `NNNschuz.zip` se stránkami `sSSSTTT.htm`;
  chybějící stránky se stahují zvlášť z `eknih/<rok>ps/stenprot/NNNschuz/sSSSTTT.htm`

Schémata tabulek jsou převzatá z `kokes/od` (`data/psp/mapping.json`) a dokumentace psp.cz.
Pořadí sloupců je v `pipeline/opendata.py` (`COLS`).

Výstupy pro web (`dist/data`):
- `periods.json`: období, počty, čas zpracování, číselník kódů hlasování
- `<P>/people.json`: osoby v období (poslanci i ostatní řečníci)
- `<P>/votes.json`: `{cols, rows}` všech hlasování
- `<P>/o/<id_osoba>.json`: detail osoby. Hlasování jsou uložená jako řetězec kódů
  `hlasovani.kody` od indexu `offset` do pole `votes.rows`.
- `<P>/s/<id_osoba>.json.gz`: projevy seskupené podle `key = "<schuze>-b<id_bod>"`
- `<P>/sum/<id_osoba>.json`: `{key: {s: ok|short|chair|pending, b: [odrážky]}}`
- `summary-status.json`: stav shrnování a info o posledním běhu

## 3. Neověřené předpoklady – ověřit jako první

Seřazeno podle rizika. U každého je uvedeno, jak ho ověřit.

1. **HTML stenoprotokolů** (`steno.parse_page`). Předpokládám:
   - začátek vystoupení je `<p>` s odkazem `…detail.sqw?id=<id_osoba>`, jehož text je jmenovka
     („Poslanec Jan Novák“) na začátku odstavce, za ní `:` a text;
   - kotva `<a id|name="r<aname>">` je v odstavci nebo těsně před ním;
   - nadpis bodu je celý tučný odstavec;
   - odstavce před prvním řečníkem na stránce pokračují vystoupením z předchozího turnu;
   - obsah je v `#main-content` (jinak `body`) a navigační odstavce obsahují odkazy na
     `s*.htm` / `index.htm`;
   - kódování cp1250, pokud meta neříká jinak.

   *Ověření:* stáhnout pár stránek, např. `eknih/2021ps/stenprot/001schuz/s001002.htm` a jednu
   z 2017 a 2025, prohlédnout HTML a pustit na ně `parse_page`. Skutečné vzorky pak dát do
   `tests/fixtures/` a upravit `fake_psp.py`. V logu Actions hledat varování
   „parser žádné nenašel“ a krok „Statistiky parsování stenoprotokolů“ (`matched_rec`,
   `by_order`, `no_rec`, `osoba_mismatch`, `continuations`).
2. **`steno.zip` v opendata** existuje a má sloupce `steno(id_steno,id_org,schuze,turn,od_steno,jd,od_t,do_t)`,
   `rec(id_steno,id_osoba,aname,id_bod,druh)`, `steno_bod(id_steno,aname,id_bod)`.
   Dále předpokládám, že `rec.aname` odpovídá kotvě `r<aname>` na stránce a že `druh` 2/4 znamená
   předsedajícího. Bez `steno.zip` parser použije osobu z odkazu a hádá roli podle jmenovky.
3. **ZIPy stenoprotokolů**: umístění indexu (`zip/index.htm`, pro 2017 možná jen `zip/`),
   názvy `NNNschuz.zip`, názvy stránek uvnitř `sSSSTTT.htm`. Není jasné, co se děje při
   čísle turnu nad 999. Ověřit, jestli ZIPy existují i pro 2025 a jestli se aktualizují
   během probíhající schůze.
4. **Hlasování**: názvy souborů v `hl-<rok>ps.zip`, formát data (`DD.MM.YYYY` i `YYYY-MM-DD`
   jsou podporované), kódy `A,B/N,C,F,@,M,W,K`. Detail hlasování na psp.cz:
   `sqw/hlasy.sqw?g=<id_hlasovani>` (ověřeno z výsledků hledání).
5. **Období a kluby**: období = `organy.zkratka ~ ^PSP\d+$`, rok z `od_organ`. Kluby jsou
   orgány typu „Klub“ s rodičem = orgán období. Mandát se bere ze `zarazeni` do orgánu PSPx.
   Odkaz na osobu `sqw/detail.sqw?id=<id_osoba>`.
6. **Claude CLI v CI**: flagy `-p --output-format json --model --max-turns 1 --system-prompt`
   a `--tools ""` (při chybě se zkouší `--disallowedTools …`). Autentizace přes
   `CLAUDE_CODE_OAUTH_TOKEN`. Detekce vyčerpaného limitu je regex nad chybovou hláškou
   v `summarize.call_claude`. Ověřit lokálně jedním voláním a zkontrolovat tvar JSON výstupu
   (`result`, `is_error`).
7. **Pagefind**: Node API `createIndex` / `addCustomRecord` / `writeFiles`, verze 1.6.0. Lokálně
   funguje na malých datech; neznámá je velikost indexu a spotřeba paměti na plných
   datech (odhad stovky tisíc záznamů).
8. **GitHub Actions**: verze akcí (`checkout@v6`, `setup-python@v6`, `setup-node@v6`,
   `cache@v5`, `upload-pages-artifact@v5`, `deploy-pages@v5`). Gate pro půlnoc používá
   `github.event.schedule` a posun `TZ=Europe/Prague`. Neověřeno je i to, že se první běh
   vejde do 6 hodin a že release `state` půjde vytvořit s `GITHUB_TOKEN`.
9. **Podmíněné požadavky**: psp.cz posílá `ETag` / `Last-Modified` a respektuje
   `If-None-Match` / `If-Modified-Since`. Pokud ne, každou noc se stáhne všechno znovu.

## 4. Další kroky (návrh pořadí)

1. Ověřit body 1–5 na skutečných datech (lokálně `python -m pipeline build --periods 10`,
   pak 9 a 8) a opravit parser. Do testů přidat skutečné vzorky HTML a UNL.
2. Zkontrolovat výstup v prohlížeči na skutečných datech: přiřazení projevů ke správným
   osobám a bodům, pokračování přes stránky, role předsedajícího, datumy a časy.
3. Ověřit volání `claude` (bod 6) a zkusit shrnutí pár skutečných bodů. Kvalitu promptu
   ukázat autorovi.
4. Změřit velikost `dist/` a dobu běhu na plných datech. Pokud se nevejde do limitu,
   **zeptat se autora**, ne rozhodovat sám.
5. V repozitáři je potřeba (pokud už autor neudělal): Settings → Pages → Source = GitHub Actions;
   secret `CLAUDE_CODE_OAUTH_TOKEN`; ruční spuštění workflow.
6. Potvrdit s autorem body z části „Rozhodnuto bez výslovného souhlasu“.

## 5. Známé slabiny a nápady (neřešit bez domluvy)

- `actions/cache` ukládá každou noc nový klíč s celou cache (stovky MB až GB). Zvážit
  stabilnější klíč nebo ukládat jen při změně.
- `web/app.js` → `otherPeriods` načítá `people.json` všech období kvůli odkazům
  „Další období“. Lepší by byl jeden malý index osoba → období.
- Turny neveřejných jednání se značí souborem `.404` v cache; zmizí se smazáním cache.
- Ruční hlasování (`druh_hlasovani = R`) nemají hlasy jednotlivců a u osob se nezobrazí.
- Ve veřejném repu GitHub po 60 dnech bez commitu vypíná plánované workflow.
- Projevy před rokem 2017 jsou odložené (limit 1 GB). Možnosti řešení byly s autorem
  probrány: externí úložiště (např. Cloudflare R2) nebo vlastní server.
- Testy v `tests/fake_psp.py` kopírují moje předpoklady. Dokud v nich nejsou skutečné
  vzorky, nedokazují správnost parsování.
