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
