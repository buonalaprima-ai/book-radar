#!/usr/bin/env python3
"""
Test del filtro di precisione (author_matches) e dell'estrazione libro.

Eseguibile direttamente: `python test_filter.py`
Compatibile anche con pytest: `pytest test_filter.py`

Non fa chiamate di rete: lavora su volumi finti che imitano la forma della
risposta Google Books.
"""

from datetime import date

from check import (
    author_matches,
    extract_book,
    format_message,
    is_recent_release,
    normalize_name,
    normalize_title,
    volume_language,
    work_key,
)


def _volume(authors, title="Un Libro"):
    """Costruisce un volume finto con la forma di Google Books."""
    return {"id": "vol-1", "volumeInfo": {"title": title, "authors": authors}}


# MARK: - Casi che DEVONO passare il filtro

def test_match_esatto():
    assert author_matches(_volume(["John Niven"]), "John Niven")


def test_match_case_insensitive():
    assert author_matches(_volume(["JOHN NIVEN"]), "john niven")


def test_match_spazi_extra_normalizzati():
    assert author_matches(_volume(["John   Niven"]), "John Niven")


def test_match_tra_piu_autori():
    # Volume con piu' autori: basta che il canonico sia presente.
    assert author_matches(_volume(["Mario Rossi", "John Niven"]), "John Niven")


def test_match_diacritici():
    # Stesso autore con grafie diverse (macron/umlaut) deve combaciare.
    assert author_matches(_volume(["Ryū Murakami"]), "Ryu Murakami")
    assert author_matches(_volume(["Ryü Murakami"]), "Ryu Murakami")
    assert author_matches(_volume(["Ryu Murakami"]), "Ryū Murakami")


def test_normalize_name_diacritici():
    assert normalize_name("Ryū Murakami") == "ryu murakami"
    assert normalize_name("Ryü Murakami") == "ryu murakami"
    assert normalize_name("  RYU   Murakami ") == "ryu murakami"


# MARK: - Casi che NON devono passare (falsi positivi da scartare)

def test_no_match_omonimo_diverso():
    # Stesso cognome ma persona diversa: deve essere scartato.
    assert not author_matches(_volume(["John Niven Smith"]), "John Niven")


def test_no_match_substring():
    # "Niven" e' contenuto ma non e' un match esatto del nome completo.
    assert not author_matches(_volume(["Larry Niven"]), "John Niven")


def test_no_match_autore_totalmente_diverso():
    assert not author_matches(_volume(["Stephen King"]), "John Niven")


def test_no_match_senza_autori():
    # Volume senza campo authors (capita): non deve crashare ne' matchare.
    assert not author_matches({"id": "x", "volumeInfo": {"title": "Y"}}, "John Niven")


# MARK: - Estrazione e formattazione

def test_extract_book_campi():
    volume = {
        "id": "abc123",
        "volumeInfo": {
            "title": "O Brother",
            "subtitle": "un romanzo",
            "authors": ["John Niven"],
            "publishedDate": "2023-08-01",
            "infoLink": "https://books.google.com/abc123",
        },
    }
    book = extract_book(volume)
    assert book["id"] == "abc123"
    assert book["title"] == "O Brother"
    assert book["published_date"] == "2023-08-01"
    assert book["link"] == "https://books.google.com/abc123"


def test_format_message_escape_html():
    book = {
        "id": "x",
        "title": "Tom & Jerry <test>",
        "subtitle": "",
        "authors": ["A & B"],
        "published_date": "2024",
        "link": "https://example.com/?a=1&b=2",
    }
    message = format_message(book)
    # I caratteri speciali HTML devono essere escapati per parse_mode=HTML.
    assert "&amp;" in message
    assert "&lt;test&gt;" in message
    assert "<b>" in message  # il grassetto del titolo resta intatto


# MARK: - Filtro data (novita' recenti vs backfill silenzioso)

def test_is_recent_release():
    oggi = date(2026, 6, 19)
    # Vecchie (oltre 365 giorni) -> NON recenti (backfill silenzioso)
    assert not is_recent_release("2014-04-29", today=oggi)
    assert not is_recent_release("2000", today=oggi)
    assert not is_recent_release("2024-06-12", today=oggi)
    # Recenti o future -> recenti (si notifica)
    assert is_recent_release("2026-05-01", today=oggi)
    assert is_recent_release("2026", today=oggi)
    assert is_recent_release("2027-01-01", today=oggi)
    # Data assente/illeggibile -> recente (nel dubbio si notifica)
    assert is_recent_release("", today=oggi)
    assert is_recent_release("data sconosciuta", today=oggi)


# MARK: - Lingua e chiave opera

def test_volume_language():
    assert volume_language(_volume(["John Niven"])) is None  # nessun campo language
    vol = {"id": "x", "volumeInfo": {"title": "Y", "language": "it"}}
    assert volume_language(vol) == "it"


def test_normalize_title():
    assert normalize_title("  Padri   Nostri ") == "padri nostri"
    assert normalize_title("MASCHIO Bianco Etero") == "maschio bianco etero"


def test_work_key_dedup_edizioni():
    # Edizioni diverse, stesso titolo+autore -> stessa chiave (una sola notifica).
    assert work_key("John Niven", "Padri Nostri") == work_key("john niven", "  padri   nostri ")


def test_work_key_namespace_autore():
    # Stesso titolo, autori diversi -> chiavi diverse.
    assert work_key("John Niven", "Ritorno") != work_key("Altro Autore", "Ritorno")


# MARK: - Runner standalone (senza pytest)

def _run_all():
    tests = [value for name, value in sorted(globals().items())
             if name.startswith("test_") and callable(value)]
    failures = 0
    for test in tests:
        try:
            test()
            print(f"  PASS  {test.__name__}")
        except AssertionError as error:
            failures += 1
            print(f"  FAIL  {test.__name__}  -> {error or 'assertion fallita'}")
    print()
    print(f"{len(tests) - failures}/{len(tests)} test passati.")
    return failures


if __name__ == "__main__":
    import sys
    sys.exit(1 if _run_all() else 0)
