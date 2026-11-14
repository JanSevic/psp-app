"""Parsování skutečných stránek stenoprotokolů z psp.cz (vzorky v tests/fixtures/steno).

- web_*: stránky tak, jak je psp.cz servíruje (kotvy `id="r<aname>"` na odkazu na osobu)
- zip_*: stejné stránky ze ZIPu `NNNschuz.zip` – bez kotev, bez tučného písma
"""

from pathlib import Path

from pipeline import fetch, steno
from pipeline.opendata import Period, StenoMeta, StenoTurn

FIX = Path(__file__).resolve().parent / "fixtures" / "steno"


def parse(name):
    return steno.parse_page(fetch.decode_html((FIX / name).read_bytes()))


def test_web_page_with_anchors():
    p = parse("web_2025_s010030.htm")
    assert p.continuation == []
    assert [(s.aname, s.link_osoba, s.label) for s in p.speeches] == [
        (1, 6418, "Místopředseda PSP Jan Skopeček"),
        (2, 6623, "Poslanec Ondřej Babka"),
    ]
    assert p.speeches[0].paragraphs[0].startswith("Děkuji. S faktickou poznámkou")
    assert len(p.speeches[1].paragraphs) == 9
    # skript s tooltipy (tippy) se do textu nedostane
    assert not any("tippy" in t for s in p.speeches for t in s.paragraphs)


def test_web_continuation_page():
    p = parse("web_2021_s001002_pokracovani.htm")
    assert p.speeches == []
    assert p.continuation[0] == "Pan poslanec Jan Berki. (Slibuji.)"
    assert len(p.continuation) == 45


def test_zip_page_without_anchors_and_headings():
    p = parse("zip_2025_s001053.htm")
    assert all(s.aname is None for s in p.speeches)
    assert [s.link_osoba for s in p.speeches] == [6214, 6211, 6214, 6105, 6214, 6178, 6105, 6211, 6105, 6211, 6105]
    # <br> v nadpisu bodu se nahradí mezerou
    assert p.headings[0] == "10. Návrh na stanovení počtu místopředsedů Poslanecké sněmovny"


def _meta(recs, marks=()):
    t = StenoTurn(28302, 174, 1, 53, None, 1, 1120)
    return StenoMeta(turns={t.id_steno: t}, by_key={(174, 1, 53): t}, rec={t.id_steno: recs},
                     bod_marks={t.id_steno: list(marks)} if marks else {})


def _period():
    from datetime import date
    return Period(num=10, organ_id=174, year=2025, od=date(2025, 10, 4), do=None)


# skutečné řádky rec.unl pro id_steno 28302 (1. schůze, turn 53)
REC_53 = [
    (6214, 1, 0, 4), (6211, 2, 0, 5), (6214, 3, 0, 4), (6105, 4, 0, 3), (6214, 5, 0, 4), (6178, 6, 0, 5),
    (6105, 7, 0, 4), (6211, 8, 57858, 5), (6105, 9, 57858, 4), (6211, 10, 57858, 5), (6105, 11, 57858, 4),
]


def _recs(rows):
    return [{"id_osoba": o, "aname": a, "id_bod": b or None, "druh": d} for o, a, b, d in rows]


def test_zip_page_matches_rec_by_order_and_roles():
    pages = {(1, 53): ("file", FIX / "zip_2025_s001053.htm")}
    speeches, stats = steno.build_speeches(_period(), pages, _meta(_recs(REC_53)))
    assert stats["by_order"] == 11 and stats["osoba_mismatch"] == 0
    assert [s.aname for s in speeches] == list(range(1, 12))
    # druh 3 = předseda PSP mluví jako řečník (poděkování za zvolení), 4 = řídí schůzi
    assert speeches[3].role == "speaker" and speeches[6].role == "chair"
    assert [s.id_bod for s in speeches][-4:] == [57858] * 4


def test_zip_page_alignment_when_counts_differ():
    pages = {(1, 53): ("file", FIX / "zip_2025_s001053.htm")}
    recs = _recs(REC_53[:3] + REC_53[4:])  # v datech chybí jedno vystoupení
    speeches, stats = steno.build_speeches(_period(), pages, _meta(recs))
    assert stats.get("by_align") == 10 and stats["osoba_mismatch"] == 0
    assert speeches[2].aname == 3 and speeches[4].aname == 5


def test_label_overrides_shifted_rec_without_person_links(tmp_path):
    """ZIPy 2021 nemají odkaz na osobu; posunuté `rec` nesmí přiřadit text jiné osobě."""
    import re
    from pipeline import notes

    html = (FIX / "zip_2025_s001053.htm").read_bytes()
    page = tmp_path / "s001053.htm"
    page.write_bytes(re.sub(rb'href="[^"]*detail\.sqw[^"]*"', b'href="#"', html))
    names = {6214: ("Věra", "Kovářová"), 6211: ("Martin", "Kolovratník"), 6105: ("Tomio", "Okamura"),
             6178: ("Jan", "Bartošek")}
    surnames = {o: p for o, (_, p) in names.items()}
    # rec posunuté o jedno: každé vystoupení dostane osobu následujícího
    rows = REC_53[1:] + REC_53[:1]
    speeches, stats = steno.build_speeches(_period(), {(1, 53): ("file", page)}, _meta(_recs(rows)),
                                           surnames, notes.NameIndex(names))
    assert [s.id_osoba for s in speeches] == [6214, 6211, 6214, 6105, 6214, 6178, 6105, 6211, 6105, 6211, 6105]
    assert stats["osoba_dle_jmenovky"] == 11
    # jmenovka sedí – rec platí beze změny i bez odkazů
    speeches, stats = steno.build_speeches(_period(), {(1, 53): ("file", page)}, _meta(_recs(REC_53)),
                                           surnames, notes.NameIndex(names))
    assert "osoba_dle_jmenovky" not in stats and speeches[6].role == "chair"


def test_missing_bod_is_outside_agenda_item():
    """psp.cz neuvádí bod u vystoupení mimo projednávaný bod – nepřilepit k předchozímu."""
    rows = [(6214, 1, 111, 4), (6211, 2, 0, 5), (6214, 3, 0, 4)] + [(o, a, 0, d) for o, a, _, d in REC_53[3:]]
    pages = {(1, 53): ("file", FIX / "zip_2025_s001053.htm")}
    speeches, stats = steno.build_speeches(_period(), pages, _meta(_recs(rows), marks=[(7, 222)]))
    # bez bodu až do značky bodu (aname 7), od ní nový bod
    assert [s.id_bod for s in speeches] == [111] + [None] * 5 + [222] * 5
    assert stats["mimo_bod"] == 5


def test_missing_bod_inside_one_debate():
    """Záznam bez bodu mezi dvěma záznamy téhož bodu patří do jeho rozpravy."""
    rows = [(o, a, 0 if a in (2, 3) else 111, d) for o, a, _, d in REC_53]
    pages = {(1, 53): ("file", FIX / "zip_2025_s001053.htm")}
    speeches, stats = steno.build_speeches(_period(), pages, _meta(_recs(rows)))
    assert [s.id_bod for s in speeches] == [111] * 11
    assert stats["bod_z_okoli"] == 2


def test_old_pages_colon_inside_label():
    p = parse("web_1996_s010010.htm")
    assert [(s.aname, s.label) for s in p.speeches[:3]] == [
        (1, "Poslankyně Zuzka Rujbrová"), (2, "Místopředseda PSP Jan Kasal"), (3, "Poslankyně Zdeňka Horníková"),
    ]
    assert p.speeches[0].paragraphs[0].startswith("...jako je zavádění")
    p = parse("web_1993_s010010.htm")
    assert p.speeches[0].aname == 1 and p.speeches[0].label == "Poslanec Dalibor Matulka"
    assert p.speeches[0].paragraphs[0].startswith("Dámy a pánové")
