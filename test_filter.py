#!/usr/bin/env python3
"""
Test del filtro di precisione (author_matches) e dell'estrazione libro.

Eseguibile direttamente: `python test_filter.py`
Compatibile anche con pytest: `pytest test_filter.py`

Non fa chiamate di rete: lavora su volumi finti che imitano la forma della
risposta Google Books.
"""

from check import author_matches, extract_book, format_message


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
