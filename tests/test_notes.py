from pipeline import notes

# vzorky jsou skutečné poznámky ze stenoprotokolů
IDX = notes.NameIndex({
    1: ("Lubomír", "Volný"), 2: ("Jan", "Volný"), 3: ("Jaroslav", "Faltýnek"),
    4: ("Miroslav", "Kalousek"), 5: ("Markéta", "Pekarová Adamová"), 6: ("Radek", "Vondráček"),
})


def resolve(note):
    return IDX.resolve(notes.subject_tokens(note))


def test_subject_with_full_name_and_surname():
    assert resolve("Poslanec Lubomír Volný od pultíku při vypnutém mikrofonu: Vy jste mě předtím zkrátil!") == 1
    assert resolve("Poslanec Faltýnek z lavice: Zopakovat ten návrh!") == 3
    assert resolve("Poslanec Kalousek gestikuluje ze svého místa.") == 4
    assert resolve("Poslankyně Pekarová Adamová: Děkuji.") == 5


def test_colon_ends_name():
    assert notes.subject_tokens("Poslanec Vondráček: Nesouhlas.") == ["Vondráček"]


def test_ambiguous_surname_is_not_assigned():
    assert resolve("Poslanec Volný: Ano.") is None


def test_notes_without_named_subject_are_skipped():
    found = notes.find_notes(["Text. (Hluk v sále.) (Ministr: Nesouhlas.) (Poslanec spěchá nahoru.) Konec."])
    assert found == []
    found = notes.find_notes(["Děkuji. (Poslanec Faltýnek: Zopakovat!) Pokračuji."])
    assert found == [(0, "Poslanec Faltýnek: Zopakovat!", ["Faltýnek"])]


NAMES = {1: ("Lubomír", "Volný"), 2: ("Jan", "Volný")}


def test_disambiguate_by_inflected_full_name_in_context():
    ctx = "…otevírám obecnou rozpravu, do které je přihlášen pan poslanec Jana Volného. (Posl. Volný je přihlášen…)"
    assert notes.disambiguate({1, 2}, NAMES, ctx, set(), set()) == (2, "kontext")
    ctx = "Upozorňuji pana poslance Lubomíra Volného na jednací řád."
    assert notes.disambiguate({1, 2}, NAMES, ctx, set(), set()) == (1, "kontext")


def test_disambiguate_by_nearby_speaker_then_nearby_note():
    assert notes.disambiguate({1, 2}, NAMES, "", {2, 99}, set()) == (2, "vystoupeni")
    assert notes.disambiguate({1, 2}, NAMES, "", set(), {1}) == (1, "poznamky")


def test_disambiguate_gives_up_when_both_fit():
    assert notes.disambiguate({1, 2}, NAMES, "", {1, 2}, set()) == (None, None)
    assert notes.disambiguate({1, 2}, NAMES, "", set(), set()) == (None, None)
