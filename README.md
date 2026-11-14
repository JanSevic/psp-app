# PSP-app – hlasování a projevy poslanců

Statický web, který pro každého poslance a každé volební období ukazuje:

- **Hlasování** – všechna hlasování během mandátu: název (proklik na detail na psp.cz),
  jak hlasoval(a), výsledek; filtry podle data, schůze, způsobu hlasování a textu názvu.
- **Projevy** – plné znění všeho, co osoba ve Sněmovně řekla, seskupené podle schůze
  a bodu pořadu, s odkazem na místo v originálním stenoprotokolu na psp.cz.
- **Shrnutí** – ke každému bodu pořadu několik odrážek s tím, co osoba řekla
  (vytváří Claude), s prokliknutím na plné znění.
- **Statistiky** – hlasoval / omluven / nepřihlášen (stejně jako psp.cz), počet vystoupení.
- **Fulltext** – hledání v projevech jedné osoby i napříč všemi řečníky.

Stránku má každý, kdo ve Sněmovně promluvil (i neposlanci – ministři, hosté),
včetně vystoupení v roli předsedajícího. Hlasování jsou zpracovaná za všechna
volební období, projevy od roku 2017 (8. období a novější).

## Jak to funguje

```
GitHub Actions (každý den o půlnoci pražského času), tři úlohy s vlastními limity:
  data       python -m pipeline build       stáhne otevřená data psp.cz + stenoprotokoly → dist/data
  summaries  python -m pipeline summarize   doplní chybějící shrnutí přes Claude Code
  site       node scripts/build-search.mjs  fulltextový index (Pagefind) → nasazení na GitHub Pages
```

Web se nasadí pokaždé, když doběhne úloha data, i když shrnování selže nebo se přeskočí.
Stahování má časový rozpočet (proměnná `PSP_FETCH_BUDGET_MINUTES`, výchozí 180 minut);
co se nestihne, dotáhne se při dalším běhu.

Zdroje dat (psp.cz nemá API, používají se otevřená data):

| Soubor | Obsah |
|---|---|
| `opendata/poslanci.zip` | osoby, poslanci, orgány (kluby, kraje), členství |
| `opendata/hl-<rok>ps.zip` | hlasování a hlasování jednotlivých poslanců |
| `opendata/schuze.zip` | schůze a body pořadu |
| `opendata/steno.zip` | metadata stenozáznamů: kdo kde mluvil, k jakému bodu, zda jako předsedající |
| `eknih/<rok>ps/stenprot/zip/*.zip` | HTML stenoprotokolů po schůzích; chybějící stránky se stahují jednotlivě |

Stažené soubory se ukládají do cache (GitHub Actions cache) a stahují se znovu
jen při změně (podmíněné HTTP požadavky). Hotová shrnutí se ukládají do souboru
`summaries.jsonl.gz` v GitHub Release `state` – za žádné shrnutí se neplatí dvakrát.

## Nastavení (jednorázově)

1. **GitHub Pages:** Settings → Pages → *Build and deployment* → Source: **GitHub Actions**.
2. **Token pro Claude:** na svém počítači s nainstalovaným Claude Code spusť
   `claude setup-token`, přihlas se a zkopíruj vypsaný token. Pak v repozitáři
   Settings → Secrets and variables → Actions → *New repository secret*:
   název `CLAUDE_CODE_OAUTH_TOKEN`, hodnota = token. Token platí zhruba rok.
3. **První běh:** Actions → *Noční zpracování a nasazení* → *Run workflow*.
   První běh stahuje všechna data a trvá déle.

**Změna vzhledu:** po pushi změn ve `web/` se sám spustí workflow *Aktualizace vzhledu*
(lze i ručně). Nic nestahuje ani neshrnuje – vezme hotový web z posledního úspěšného
nočního běhu (artefakt `site`, drží se 30 dní), přepíše v něm soubory z `web/` a nasadí ho
během pár minut. Další noční běh pak nasadí vše znovu, už i s novým vzhledem.

Bez tokenu web funguje také, jen u bodů bez shrnutí zobrazí „Shrnutí zatím není hotové“.

### Volitelné proměnné (Settings → Secrets and variables → Actions → Variables)

| Proměnná | Výchozí | Význam |
|---|---|---|
| `SUMMARY_MAX_CALLS` | 105 | strop počtu volání Claude za jeden běh (≈ 35 % týdenního limitu předplatného) |
| `SUMMARY_MIN_AGE_DAYS` | 14 | shrnují se jen schůze, ke kterým psp.cz už zveřejnilo `rec` (osoby a body vystoupení) a které skončily aspoň před tolika dny – data čerstvých schůzí se ještě mění a shrnutí by se dělala dvakrát |
| `SUMMARY_MAX_MINUTES` | 180 | časový strop shrnování za jeden běh |
| `SUMMARY_MODEL` | `sonnet` | model pro shrnutí (`sonnet`, `haiku`, `opus`) |
| `SUMMARY_EFFORT` | `high` | úsilí (`claude --effort`) pro běžná jednání |
| `SUMMARY_EFFORT_HIGH` | `high` | úsilí pro důležitá jednání: důvěra/nedůvěra, rozpočet, ústavní zákon, volba/odvolání předsedy, třetí čtení, vrácený zákon; nebo skupina nad `SUMMARY_HIGH_WORDS` (3000) slov, bod s více než `SUMMARY_HIGH_SPEAKERS` (50) řečníky, vyhrocené jednání (`SUMMARY_HIGH_HEAT` = 3 poznámky stenografů) |

Každé volání shrne dávku až 8 bodů (max. ~12 000 slov). Shrnují se nejdřív
nejnovější body, pak postupně starší až do roku 2017. Když předplatné narazí
na limit, běh shrnování ukončí, uloží hotovou práci a pokračuje další noc.

Body, kde osoba jen řídila schůzi, se neshrnují („Pouze řízení schůze“);
velmi krátká vystoupení (pod 25 slov) se místo shrnutí zobrazí celá.

## Lokální spuštění

```bash
pip install -r requirements.txt
npm ci
python -m pipeline build --periods 10   # jen 10. období
python -m pipeline write-sums            # shrnutí bez volání Claude
python -m pipeline site
node scripts/build-search.mjs
cd dist && python -m http.server 8000    # http://localhost:8000
```

Testy (používají napodobeninu psp.cz v `tests/fake_psp.py`): `python -m pytest`.

## Velikost webu

GitHub Pages povoluje web do 1 GB. Plné texty jsou uložené komprimovaně
(`.json.gz`, rozbalí je prohlížeč). Krok `check-size` vypíše velikosti
jednotlivých částí a selže, pokud web přesáhne 950 MB. Projevy starší než 2017
by se do limitu nevešly; jejich zpracování je odložené.

## Známá omezení

- Formát HTML stenoprotokolů jsem při vývoji neměl k dispozici (psp.cz nebyl
  dosažitelný). Parser je napsaný podle dokumentace a vzorků; po prvním běhu
  zkontroluj v logu krok *Statistiky parsování stenoprotokolů* (počet stránek,
  vystoupení, shod s metadaty) a případná varování „parser žádné nenašel“.
- Shrnutí vytváří AI a mohou obsahovat chyby; rozhodující je plné znění.
- GitHub spouští plánované úlohy s proměnlivým zpožděním. Ve veřejném repozitáři
  plánované úlohy po 60 dnech bez commitu deaktivuje (pošle e-mail; stačí je znovu zapnout).
