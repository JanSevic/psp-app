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
- statistika hlasování jako na psp.cz (`sqw/pstat.sqw`): Hlasoval = A + N + zdržel se (C, F, K);
  Omluven = M; Nepřihlášen = @; základ = hlasování během mandátu bez zmatečných a bez W.
  Kódy `@` v době omluvy z tabulky `omluvy` (hl-<rok>ps.zip: id_organ, id_poslanec, den, od, do;
  prázdné od/do = celý den) se přeznačí na `M` – otevřená data u omluvených uvádějí `@`.
  Formát časů od/do v reálných datech neověřen (parser bere HH:MM i HH.MM), viz log „Omluvy: …“.

## 2. Architektura v kostce

```
nightly.yml: gate (půlnoc Praha) → data → summaries → site → deploy   (každá úloha vlastní limit)
  data:       cache restore → python -m pipeline build → cache save (vždy) → artefakt „data“
              (dist/ + state/work/); stahování má časový rozpočet PSP_FETCH_BUDGET_MINUTES
  summaries:  artefakt „data“ + release state → python -m pipeline summarize
              → release state (vždy) → artefakt „summary-status“
  site:       běží vždy, když doběhla data (i když shrnování selže / je přeskočené):
              artefakt „data“ + release state → write-sums → site + build-search.mjs + check-size
              → upload-pages-artifact → deploy
```

Stahování (14. 11. 2026, po prvních bězích na GitHubu):
- psp.cz **nepodporuje podmíněné požadavky** (vždy vrací celý soubor). Proto se o novém
  stažení rozhoduje podle stáří: ZIPy stenoprotokolů a `hl-*.zip` uzavřených období se
  z cache nestahují (kromě týdenní kontroly v neděli, `PSP_REFRESH_WEEKDAY`); ZIP schůze
  aktuálního období se stahuje znovu do `PSP_ZIP_RECHECK_DAYS` (60) dní od konce schůze;
  jednotlivé stránky do `PSP_PAGE_RECHECK_DAYS` (14) dní.
- Z GitHubu je psp.cz pomalé (~10 s na ZIP, ~8 s na stránku stahovanou zvlášť, občas
  „Connection refused“ / timeout). Stránky zvlášť se stahují v `PSP_FETCH_WORKERS` (2)
  souběžných požadavcích, nejnovější první, s průběžným výpisem každých 100 stránek.
  Po vyčerpání rozpočtu se použije cache a zbytek se dotáhne při dalším běhu.
- Chyba stažení jedné stránky build nezastaví (stránka se přeskočí, příště se zkusí znovu).
- psp.cz po ~150 rychlých požadavcích začne odmítat spojení / neodpovídat (rate limiting).
  `fetch` proto pauzu mezi požadavky přizpůsobuje: při odmítnutí, timeoutu nebo HTTP 429/5xx
  ji zdvojnásobí (nejvýš `PSP_MAX_DELAY` 30 s, respektuje `Retry-After`), po
  `PSP_BACKOFF_RECOVER` (20) úspěších v řadě ji zkrátí na polovinu. Navázání spojení má
  timeout 20 s (`PSP_CONNECT_TIMEOUT`), čtení 90 s. Změny pauzy jsou v logu („psp.cz nestíhá …“).

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
- `<P>/fotky.json` + `<P>/foto/<id_osoba>.jpg`: fotky poslanců (sloupec `poslanec.foto = 1`,
  URL `/eknih/cdrom/<rok>ps/eknih/<rok>ps/poslanci/i<id>.jpg`, 201×250 px). Stahují se jen jednou,
  nejnovější období první, v rámci časového rozpočtu. Podmínky psp.cz: fotky neměnit ani neořezávat,
  nepoužít komerčně ani pro volební kampaň, u fotky uvést „Foto: Kancelář Poslanecké sněmovny“.
- Účast a přehled hlasování se počítají jen z doby mandátu (`mask_to_mandate`): otevřená data
  obsahují u některých poslanců záznamy i mimo mandát.

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

## 3a. Ověřeno na skutečných datech (13. 11. 2026, období 10)

psp.cz je z lokálního prostředí dosažitelný. `python -m pipeline build --periods 10` proběhl
(první běh ~10 min, další ~7 min kvůli podmíněným požadavkům na stránky z posledních 60 dní).
Statistika: 4 548 stránek, 21 568 vystoupení; 14 537 spárováno podle kotvy, 6 145 podle pořadí,
12 zarovnáním osob, 874 bez `rec` (poslední schůze, data ještě nedoplněná), 39 neshod osoby.
Data období 10: 13,4 MB.

- **Bod 2 (steno.zip)**: existuje, sloupce sedí. `rec.druh`: 2/4 = řídí schůzi, 3/5 = řečník
  (3 = předseda PSP mluví jako řečník), u období ≤ 6 prázdné. **`rec.id_bod = 0` znamená
  „nevyplněno“** (~25 % záznamů, i uprostřed rozpravy) → bod se přenáší z předchozího
  vystoupení schůze, dokud nepřijde značka ze `steno_bod`.
- **Bod 1 (HTML)**: web: kotva `id="r<aname>"` je přímo na odkazu na osobu uvnitř `<b>`,
  obsah v `#main-content`. **Stránky v ZIPech nemají kotvy ani `<b>`** → párování podle pořadí,
  při rozdílném počtu zarovnání posloupnosti osob (`_align_by_osoba`). **Členové vlády mají
  odkaz na vlada.cz** místo `detail.sqw` → řečník se pozná podle odkazu + dvojtečky.
  Občas odkaz `detail.sqw?id=0` (chyba v datech) → osoba neznámá. Když `rec` nesouhlasí
  s odkazem (posunutý turn), věří se odkazu. Vzorky jsou v `tests/fixtures/steno/`.
- **Bod 3 (ZIPy)**: `zip/index.htm` funguje i pro 2017; ZIPy pro 2025 jsou jen do 23. schůze
  (novější se stahují po stránkách). Max. turn 651.
- **Bod 4 (hlasování)**: `hl2025s.unl`, `hl2025h1.unl`, `zmatecne.unl` (+ `omluvy.unl`).
  87 hlasování (bod 0) nemá název ani na psp.cz.
- **Bod 6 (claude CLI 2.1.x)**: flagy včetně `--max-turns` a `--tools ""` přijímá, JSON má
  `result`, `is_error`, `api_error_status` (401/403/404/429 → shrnování se zastaví).
  Skutečné shrnutí zatím nevyzkoušeno (čeká na souhlas autora – stojí limit).
- Zbývá: období 9 a 8, Pagefind na plných datech, kontrola webu v prohlížeči, GitHub Actions.

## 3b. Rozhodnutí a stav po 13. 11. 2026 (večer)

Odsouhlaseno autorem:
- **Shrnutí:** prompt v5 (`summarize.SYSTEM_PROMPT`): tvrzení připsaná řečníkovi, adresáti
  otázek, excesy doslovně v uvozovkách bez zjemňování (vč. fyzických potyček, rušení pořádku,
  výkřiků), tón jako poslední odrážka „Tón: …“ nad limit 6 odrážek, volný popis tónu.
- **Citace:** kontrola v kódu je přísná (politika – citlivé, netolerovat jiný tvar slova).
  Nedoslovná citace nebo chybějící tón → jedno opakování, pak uvozovky pryč + „(parafráze)“.
- **Model a úsilí:** Sonnet, `--effort high` pro vše. Logika „důležitých jednání“
  (`effort_for`: typ bodu A + rozsah/vyhrocenost B) zůstává pro případné snížení.
- **Strop:** 105 volání za noc ≈ 35 % týdenního limitu (měřeno: ~1 % za 21 volání se smíšeným
  úsilím; s high pro vše bude spotřeba vyšší – po prvních nocích porovnat `cost_usd` v
  `summary-status.json` s `/usage`).
- **Poznámky stenografů o osobách** (výkřiky, chování) se přiřazují osobě; shodná příjmení
  rozhoduje kontext, jinak se nepřiřadí; odhad je na webu označen.

Zjištění:
- Parser ověřen na všech obdobích (`scripts/check_parser.py`). Období 2 (1996–98) má stránky
  uspořádané podle bodů a chybí mu `rec` – pro případné zpracování starších projevů potřebuje
  vlastní stahování.
- `bod_schuze.id_typ`: 1 = 1. čtení, 2/3 = 2. čtení, 5 = 3. čtení, 13/14 = zákon vrácený
  prezidentem/Senátem, 0 = ostatní (odvozeno z nadpisů stenoprotokolů).
- Ne všechno je ve stenozáznamu: např. výkřik o menopauze (Ševčík, 10. období) stenografové
  nezapsali; zachytí se jen ve shrnutích těch, kdo na něj reagovali.

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

## Přiřazení vystoupení k bodům (oprava 14. 11. 2026)

- `rec.id_bod` chybí u vystoupení mimo projednávaný bod (přednostní práva na začátku
  dne, návrhy k pořadu, po přerušení). 10. období: 15 411 záznamů s bodem, 5 120 bez bodu
  na hranici bodů, jen 179 bez bodu uvnitř rozpravy k jednomu bodu (9. období obdobně).
- Dřív se taková vystoupení přilepila k bodu předchozího vystoupení téže schůze (i z jiného
  dne) – shrnutí pak neodpovídala názvu bodu (nahlášeno u V. Rakušana). Nově: značka bodu
  na téže stránce → jinak bod jen tehdy, když předchozí i následující záznam s bodem mají
  stejný bod (`infer_missing_bods`) → jinak skupina za den „Mimo projednávaný bod (přednostní práva, pořad schůze,
  procedurální vystoupení)“ (`MIMO_BOD`; autor zvolil jeden obecný název místo odhadu druhu).
- Ověřování na reálných datech: workflow „Diagnostika dat osoby“ (`diag.yml`) – vypíše
  data osoby z hotového webu, `rec`/`steno_bod`/nadpisy stránek (`scripts/diag_turns.py`)
  nebo souhrn `--stats` do logu běhu (logy jsou čitelné přes `gh api .../jobs/<id>/logs`).
- Web běží na https://www.ševic.eu/psp-app/ (GitHub Pages s vlastní doménou).

## Kontrola správnosti dat (14. 11. 2026)

Workflow „Diagnostika dat osoby“ s parametrem `kontrola` (např. `8 9 10`) spustí
`scripts/audit.py` a `scripts/audit_probe.py` nad cache a posledním webem a vypíše podezřelá
místa (jmenovka vs. přiřazená osoba, role, body chybějící v bod_schuze, vystoupení bez `rec`,
značky bodů, součty statistik hlasování). Spustit lze i na větvi (`ref`), nic nenasazuje.

Nalezeno a opraveno (větev `oprava-kontrola`):
- 9. období: ~730 vystoupení připsáno jiné osobě. Stránky ze ZIPů 2021 nemají odkaz na osobu
  a `rec` je v některých turnech posunuté (hlavně 112. schůze) – text předsedajícího se připsal
  ministrovi, text poslance předsedajícímu (a jako „řízení“ se neshrnul). Nově: když příjmení
  osoby z `rec` v jmenovce není a jmenovka jednoznačně patří jiné osobě, platí jmenovka
  (`osoba_dle_jmenovky` ve statistice). Zbylé neshody jsou změny příjmení (Opltová/Řehková).
- Body z `rec`, které v bod_schuze chybí (sloučené rozpravy, sliby – ~2 400 vystoupení
  v 8. období, ~1 300 v 9.), měly název „Mimo projednávaný bod“. Nově nadpis ze stenoprotokolu.
- Web: pohlaví žen je v otevřených datech „Ž“ (výjimečně „Z“), statistiky hlasování testovaly
  jen „Z“ → u většiny poslankyň mužský rod.

Zjištěno, neřešeno (ke zvážení s autorem):
- `steno_bod.aname` nesouvisí s kotvami `r<aname>` (shoda <1 %); pravidlo „značka bodu na
  stránce“ se uplatní jen výjimečně (1× za období). Neškodí, ale nic nepřináší.
- 10. období: 941 vystoupení bez `rec` (stránka má víc vystoupení než data; nejnovější schůze
  rec zatím nemá vůbec) – bod se přebírá z předchozího vystoupení schůze, případně nadpis
  stránky. Zkušební omezení na týž den zvýšilo počet „mimo bod“ o ~800 (hlavně 10. schůze),
  proto vráceno; správnost obou variant neověřena.
- Shrnutí se cachují jen podle textu, ne podle názvu bodu. Když se změní jen přiřazení bodu
  (text skupiny stejný), zůstane shrnutí vytvořené s původním názvem bodu.
- Shrnování šlo od nejnovějších schůzí, u kterých psp.cz ještě nemusí mít úplná data.
  Vyřešeno (rozhodnutí autora 14. 11. 2026): shrnují se jen schůze, které už mají v `rec`
  záznamy a skončily aspoň před `SUMMARY_MIN_AGE_DAYS` (14) dny. Úplnost `rec` po stránkách
  podmínkou být nemůže – i staré schůze mají část stránek bez `rec` natrvalo (8. období:
  92 ze 119 schůzí, 10. schůze 10. období 125 z 306 stránek). Neověřeno: zda psp.cz u
  čerstvých schůzí doplňuje `rec` postupně (např. 34. schůze 10. období 8 dní po konci:
  134 z 651 stránek bez `rec`) – sledovat řádek „Schůze zatím neuzavřené…“ v logu.
- `omluvy.unl` pro 9. období obsahuje každý záznam až ~2 000× (992 673 řádků, 28 573
  unikátních) – výsledek to nemění, jen log ukazuje přehnaný počet.

## 5. Známé slabiny a nápady (neřešit bez domluvy)

- **Statistika hlasování se liší od psp.cz o 23 hlasování** (ověřeno 14. 11. 2026 na 10. období,
  data hl-2025ps.zip ze 14. 11. 01:54, devět poslanců: Havlíček, Adamec, Babiš, Bartoň,
  Bartošová, Bendl, Berkovcová, Bouška, Bureš). psp.cz (`sqw/pstat.sqw`) má u všech celkem
  2651 hlasování, my 2674 (2702 minus 28 zmatečných ze `zmatecne.unl`). Jde o stejných 23
  hlasování pro všechny; omluvy a kategorie jinak sedí, procento „Hlasoval“ na desetinu.
  Vyloučeno: nejnovější hlasování (zpoždění), sousedé zmatečných, celá schůze/den,
  výsledek, počet přihlášených, prázdný název. Domněnka: psp.cz má vlastní seznam vyřazených
  hlasování, který v otevřených datech není. Autor rozhodl nechat zatím tak, bez poznámky
  na webu. Možné další kroky: dotaz na Sněmovnu, nebo porovnání se seznamem hlasování
  na pstat.sqw pro jednoho poslance (pokud ho psp.cz vypisuje).

- **Spotřeba předplatného na shrnutí** (změřil autor 14. 11. 2026): jeden noční běh shrnutí
  (`SUMMARY_MAX_CALLS` 105, sonnet, úsilí high; autor pak výchozí strop snížil na 50) bere 8–9 % týdenního limitu a 96 %
  pětihodinového (session) limitu, tedy je těsně pod stropem. Autor rozhodl nechat beze
  změny. Kdyby bylo potřeba šetřit: méně volání za noc, střední úsilí pro krátké skupiny
  (`effort_for` v kódu je připravené), nebo shrnovat jen obden.

- `actions/cache` ukládá každou noc nový klíč s celou cache (stovky MB až GB). Zvážit
  stabilnější klíč nebo ukládat jen při změně.
- `web/app.js` → `otherPeriods` načítá `people.json` všech období kvůli odkazům
  „Další období“. Lepší by byl jeden malý index osoba → období.
- Turny neveřejných jednání se značí souborem `.404` v cache; zmizí se smazáním cache.
- Ruční hlasování (`druh_hlasovani = R`) nemají hlasy jednotlivců a u osob se nezobrazí.
- **Nejvýš jednou za týden** (`PSP_MIN_REFETCH_DAYS`, 7): ZIP ani stránka stenoprotokolu
  stažená před méně než týdnem se znovu nestahuje, i když spadá do okna kontrol
  (`PSP_ZIP_RECHECK_DAYS`, `PSP_PAGE_RECHECK_DAYS`). Stáří se čte z `fetched` v `.meta.json`.
  Tabulky otevřených dat (hl-*.zip, schuze, steno, poslanci) se kontrolují dál každou noc.
- **Jistič výpadku** (`fetch.py`): psp.cz občas runnerům GitHubu úplně přestane navazovat
  spojení (14. 11. 2026: ~2 h ConnectTimeout, každá stránka ~1,5 min marných pokusů). Po
  `PSP_OUTAGE_FAILS` síťových chybách za sebou se na psp.cz přestane chodit, vše se vezme
  z cache a chybějící soubory se dotáhnou příští noc; každých `PSP_OUTAGE_PROBE_MINUTES`
  se zkusí jeden požadavek. V souhrnu stránek je „cache“ = použita starší kopie.
- Workflow `web.yml` (Aktualizace vzhledu) potřebuje artefakt `site` z nočního běhu, který
  se drží 30 dní. Pokud noční běh měsíc neproběhne úspěšně, je nutné ho nejdřív spustit.
  Workflow sdílí skupinu `nightly`, takže čeká, dokud noční běh neskončí.
- Ve veřejném repu GitHub po 60 dnech bez commitu vypíná plánované workflow.
- Projevy před rokem 2017 jsou odložené (limit 1 GB). Možnosti řešení byly s autorem
  probrány: externí úložiště (např. Cloudflare R2) nebo vlastní server.
- Testy v `tests/fake_psp.py` kopírují moje předpoklady. Dokud v nich nejsou skutečné
  vzorky, nedokazují správnost parsování.
