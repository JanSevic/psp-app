from pipeline import summarize as S

SRC = "Okradli jste důchodce o tisíc korun! Rvete prachy na Ukrajinu. Prostě lžete tady, jako když tiskne."


def test_verbatim_quotes_pass():
    assert S.bad_quotes(["Tvrdí: „Okradli jste důchodce o tisíc korun!“"], SRC) == []
    assert S.bad_quotes(["Říká „okradli jste důchodce … na Ukrajinu“."], SRC) == []


def test_changed_form_is_bad():
    assert S.bad_quotes(["Tvrdí, že koalice „okradla důchodce“."], SRC) == ["okradla důchodce"]


def test_strip_marks_paraphrase_and_keeps_good_quotes():
    out = S.strip_bad_quotes(["Tvrdí, že koalice „okradla důchodce“ a „lžete tady“."], SRC)
    assert out == ["Tvrdí, že koalice okradla důchodce a „lžete tady“. (parafráze)"]


def test_effort_for_important_and_batches_keep_order():
    base = {"bod": "Vládní návrh zákona o něčem", "bod_typ": 1, "words": 500, "text": "Text."}
    assert S.effort_for(base, 10) == "medium"
    assert S.effort_for(base | {"bod_typ": 5}, 10) == "high"  # třetí čtení
    assert S.effort_for(base | {"bod": "Projednání návrhu na vyslovení nedůvěry vládě"}, 10) == "high"
    assert S.effort_for(base | {"words": 5000}, 10) == "high"
    assert S.effort_for(base, 80) == "high"
    assert S.effort_for(base | {"text": "(Hluk v sále.) (Výkřiky.) (Bouchání do lavic.)"}, 10) == "high"
    gs = [{"words": 100, "_effort": e, "n": i} for i, e in enumerate(["high", "medium", "high"])]
    assert [[g["n"] for g in b] for b in S.make_batches(gs)] == [[0, 2], [1]]
