"""Vytvoří adresář napodobující strukturu psp.cz (pro testy a lokální ukázku).

Formát odpovídá dokumentaci otevřených dat; HTML stenoprotokolů je
zjednodušená napodobenina skutečných stránek.

Použití: python -m tests.fake_psp <cílový adresář>
"""

from __future__ import annotations

import sys
from pathlib import Path

from pipeline.unl import zip_bytes


def unl(rows: list[list]) -> bytes:
    lines = []
    for r in rows:
        lines.append("|".join("" if v is None else str(v) for v in r) + "|")
    return ("\n".join(lines) + "\n").encode("cp1250")


def steno_page(paragraphs: list[str]) -> bytes:
    html = (
        '<html><head><meta http-equiv="Content-Type" content="text/html; charset=windows-1250">'
        "<title>Stenoprotokol</title></head><body><div id=\"main-content\">"
        '<p class="nav"><a href="index.htm">Obsah</a> <a href="s001002.htm">Další</a></p>'
        + "".join(paragraphs)
        + "</div></body></html>"
    )
    return html.encode("cp1250")


def speaker(aname: int | None, osoba: int, label: str, text: str) -> str:
    a = f'<a id="r{aname}"></a>' if aname is not None else ""
    return f'<p align="justify">{a}<b><a href="/sqw/detail.sqw?id={osoba}">{label}</a>:</b> {text}</p>'


def build(root: Path) -> None:
    od = root / "eknih" / "cdrom" / "opendata"
    od.mkdir(parents=True, exist_ok=True)

    # --- poslanci.zip
    typ_organu = [[11, None, "Parlament", "Parliament", None, 1], [1, None, "Klub", "Club", None, 2], [75, None, "Volební kraj", "Region", None, 3]]
    organy = [
        [171, None, 11, "PSP7", "Poslanecká sněmovna", "Chamber", "2013-10-26", "2017-10-21", 1, None],
        [173, None, 11, "PSP9", "Poslanecká sněmovna", "Chamber", "2021-10-09", "2025-10-04", 1, None],
        [174, None, 11, "PSP10", "Poslanecká sněmovna", "Chamber", "2025-10-04", None, 1, None],
        [501, 173, 1, "KlA", "Poslanecký klub A", "Club A", "2021-10-09", None, 1, None],
        [502, 173, 1, "KlB", "Poslanecký klub B", "Club B", "2021-10-09", None, 1, None],
        [601, None, 75, "PHA", "Hlavní město Praha", "Prague", None, None, 1, None],
        [701, None, 2, "STRA", "Strana A", "Party A", None, None, 1, None],
    ]
    osoby = [
        [100, None, "Předseda", "Jan", None, "1970-01-01", "M", None, None],
        [101, "Ing.", "Novák", "Jan", "Ph.D.", "1975-05-05", "M", None, None],
        [102, "Mgr.", "Svobodová", "Eva", None, "1980-02-02", "Z", None, None],
        [200, None, "Ministr", "Petr", None, "1965-03-03", "M", None, None],
        [103, None, "Starý", "Karel", None, "1950-03-03", "M", None, None],
    ]
    poslanec = [
        [9100, 100, 601, 701, 173] + [None] * 10,
        [9101, 101, 601, 701, 173] + [None] * 9 + [1],  # foto = 1 (fotka existuje)
        [9102, 102, 601, 701, 173] + [None] * 9 + [1],  # foto = 1, ale na serveru chybí (404)
        [7103, 103, 601, 701, 171] + [None] * 10,
        [7101, 101, 601, 701, 171] + [None] * 10,
    ]
    zarazeni = [
        [100, 173, 0, "2021-10-21 00", None, None, None],
        [101, 173, 0, "2021-10-21 00", None, None, None],
        [102, 173, 0, "2021-10-21 00", "2023-01-01 00", None, None],
        [100, 501, 0, "2021-10-21 00", None, None, None],
        [101, 501, 0, "2021-10-21 00", "2022-06-01 00", None, None],
        [101, 502, 0, "2022-06-01 00", None, None, None],
        [102, 502, 0, "2021-10-21 00", None, None, None],
        [103, 171, 0, "2013-10-26 00", "2017-10-21 00", None, None],
        [101, 171, 0, "2013-10-26 00", "2017-10-21 00", None, None],
    ]
    foto = root / "eknih" / "cdrom" / "2021ps" / "eknih" / "2021ps" / "poslanci"
    foto.mkdir(parents=True, exist_ok=True)
    (foto / "i101.jpg").write_bytes(b"\xff\xd8\xff\xe0fake-jpeg")
    (od / "poslanci.zip").write_bytes(
        zip_bytes(
            {
                "typ_organu.unl": unl(typ_organu),
                "organy.unl": unl(organy),
                "osoby.unl": unl(osoby),
                "poslanec.unl": unl(poslanec),
                "zarazeni.unl": unl(zarazeni),
                # Svobodová omluvena 10. 11. 2021 10:00–10:20 → hlasování 80001 (10:15) je „omluven“
                "omluvy.unl": unl([[173, 9102, "10.11.2021", "10:00", "10:20"], [173, 9100, "12.01.2022", None, None]]),
            }
        )
    )

    # --- hlasování 2021 (zahrnuje escapovaný oddělovač v názvu)
    hl = [
        [80001, 173, 1, 1, 1, "10.11.2021", "10:15", 2, 0, 0, 0, 3, 2, "N", "A", "Návrh zákona o něčem – prvé čtení", "Zákon o něčem"],
        [80002, 173, 1, 2, 1, "10.11.2021", "10:30", 1, 2, 0, 0, 3, 2, "N", "R", "Pozměňovací návrh A\\|B", "PN A|B"],
        [80003, 173, 2, 1, 0, "2022-01-12", "11:00", 0, 0, 0, 0, 0, 0, "N", "Z", "Zmatečné hlasování", ""],
    ]
    hl_posl = [
        [9100, 80001, "A"], [9101, 80001, "A"], [9102, 80001, "@"],
        [9100, 80002, "B"], [9101, 80002, "A"], [9102, 80002, "N"],
        [9100, 80003, "M"], [9101, 80003, "C"],
    ]
    (od / "hl-2021ps.zip").write_bytes(
        zip_bytes({"hl2021s.unl": unl(hl), "hl2021h1.unl": unl(hl_posl), "zmatecne.unl": unl([[80003]])})
    )
    hl13 = [[60001, 171, 5, 3, 2, "2014-03-04", "09:00", 1, 1, 0, 0, 2, 2, "N", "A", "Starší hlasování", ""]]
    (od / "hl-2013ps.zip").write_bytes(
        zip_bytes({"hl2013s.unl": unl(hl13), "hl2013h1.unl": unl([[7103, 60001, "A"], [7101, 60001, "N"]]), "zmatecne.unl": b""})
    )
    (od / "hl-2025ps.zip").write_bytes(zip_bytes({"hl2025s.unl": b"", "zmatecne.unl": b""}))

    # --- schuze.zip
    schuze = [[3001, 173, 1, "2021-11-10 09", "2021-11-12 18", None, None], [3002, 173, 2, "2022-01-12 09", None, None, None]]
    bod = [
        [4001, 3001, None, 3, 1, "Vládní návrh zákona o něčem", "/sněmovní tisk 12/ - prvé čtení", None, 1, None, None, None, 0, None, None],
        [4002, 3001, None, 3, 2, "Návrh na změnu pořadu", None, None, 1, None, None, None, 0, None, None],
        [4003, 3002, None, 3, 1, "Interpelace", None, None, 1, None, None, None, 1, None, None],
    ]
    (od / "schuze.zip").write_bytes(zip_bytes({"schuze.unl": unl(schuze), "bod_schuze.unl": unl(bod)}))

    # --- steno.zip (metadata)
    steno = [
        [5001, 173, 1, 1, "2021-11-10", 1, 540, 550],
        [5002, 173, 1, 2, "2021-11-10", 1, 550, 560],
        [5003, 173, 2, 1, "2022-01-12", 1, 600, 610],
    ]
    rec = [
        [5001, 100, 1, 4001, 2],
        [5001, 101, 2, 4001, 3],
        [5001, 100, 3, 4001, 2],
        [5002, 102, 1, 4001, 3],
        [5002, 200, 2, 4002, 5],
        [5003, 101, 1, 4003, 3],
    ]
    steno_bod = [[5001, 1, 4001], [5002, 2, 4002], [5003, 1, 4003]]
    (od / "steno.zip").write_bytes(zip_bytes({"steno.unl": unl(steno), "rec.unl": unl(rec), "steno_bod.unl": unl(steno_bod)}))

    # --- stenoprotokoly 2021: schůze 1 v ZIPu, schůze 2 jen jako samostatná stránka
    sp = root / "eknih" / "2021ps" / "stenprot"
    (sp / "zip").mkdir(parents=True, exist_ok=True)
    long_text = " ".join(["Toto je dlouhé vystoupení o rozpočtu a dopravě."] * 12)
    p1 = steno_page(
        [
            "<p><b>1. Vládní návrh zákona o něčem /sněmovní tisk 12/ - prvé čtení</b></p>",
            speaker(1, 100, "Předseda PSP Jan Předseda", "Zahajuji projednávání bodu. Slovo má pan poslanec Novák."),
            speaker(2, 101, "Poslanec Jan Novák", "Děkuji za slovo. Navrhuji zamítnutí tohoto návrhu zákona v prvém čtení. " + long_text),
            f"<p>{long_text}</p>",
            speaker(3, 100, "Předseda PSP Jan Předseda", "Děkuji. Nyní paní poslankyně Svobodová."),
        ]
    )
    p2 = steno_page(
        [
            "<p>(Pokračování.) Proto návrh nepodpořím a žádám o hlasování.</p>",
            speaker(1, 102, "Poslankyně Eva Svobodová", "Děkuji. Podporuji návrh a navrhuji zkrátit lhůtu na 30 dnů. " + long_text),
            speaker(2, 200, "Ministr dopravy ČR Petr Ministr", "Vážené poslankyně, vážení poslanci, děkuji za podporu. " + long_text),
        ]
    )
    (sp / "zip" / "001schuz.zip").write_bytes(zip_bytes({"s001001.htm": p1, "s001002.htm": p2}))
    (sp / "zip" / "index.htm").write_bytes(
        b'<html><body><div id="main-content"><a href="001schuz.zip">1. schuze</a></div></body></html>'
    )
    (sp / "002schuz").mkdir(parents=True, exist_ok=True)
    (sp / "002schuz" / "s002001.htm").write_bytes(
        steno_page([speaker(1, 101, "Poslanec Jan Novák", "Ptám se pana ministra na stav dálnice D35. " + long_text)])
    )


if __name__ == "__main__":
    build(Path(sys.argv[1]))
