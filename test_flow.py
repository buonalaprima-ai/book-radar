#!/usr/bin/env python3
"""
Test d'integrazione OFFLINE del flusso completo (process_author).

Sostituisce (monkeypatch) la chiamata di rete a Google Books con risposte
finte, cosi' da verificare senza rete:
  1. primo avvio  -> inizializza, NESSUNA notifica
  2. run seguente -> un nuovo libro genera UNA notifica
  3. gli omonimi vengono scartati dal filtro

Telegram gira sempre in dry-run (nessun invio reale).

Eseguibile direttamente: `python test_flow.py`
"""

import check


def _volume(vol_id, title, authors, date="2024-01-01"):
    return {
        "id": vol_id,
        "volumeInfo": {
            "title": title,
            "authors": authors,
            "publishedDate": date,
            "infoLink": f"https://books.google.com/{vol_id}",
        },
    }


AUTHOR = {
    "name": "John Niven",
    "canonicalAuthor": "John Niven",
    "googleBooksQuery": 'inauthor:"John Niven"',
}


def test_flusso_completo():
    seen = set()
    initialized = set()

    # --- Run 1: primo avvio. Catalogo storico + un omonimo da scartare.
    check.google_books_search = lambda query, api_key=None, max_results=40: [
        _volume("book-1", "Catalogo Storico 1", ["John Niven"]),
        _volume("book-2", "Catalogo Storico 2", ["John Niven"]),
        _volume("book-omonimo", "Fantascienza", ["Larry Niven"]),  # va scartato
    ]
    sent = check.process_author(AUTHOR, seen, initialized, "tok", "chat", dry_run=True)

    assert sent == 0, "al primo avvio non si deve notificare nulla"
    assert "John Niven" in initialized, "l'autore deve risultare inizializzato"
    assert seen == {"book-1", "book-2"}, "il catalogo storico va assorbito nel visto"
    assert "book-omonimo" not in seen, "l'omonimo non deve entrare nel visto"

    # --- Run 2: esce un libro nuovo. Resta l'omonimo (sempre da scartare).
    check.google_books_search = lambda query, api_key=None, max_results=40: [
        _volume("book-3", "Romanzo Nuovo 2024", ["John Niven"], "2024-09-01"),
        _volume("book-1", "Catalogo Storico 1", ["John Niven"]),
        _volume("book-omonimo-2", "Altro Larry", ["Larry Niven"]),
    ]
    sent = check.process_author(AUTHOR, seen, initialized, "tok", "chat", dry_run=True)

    assert sent == 1, "deve notificare esattamente il libro nuovo"
    assert "book-3" in seen, "il libro nuovo va aggiunto al visto"
    assert "book-omonimo-2" not in seen, "l'omonimo non deve entrare nel visto"

    # --- Run 3: nessuna novita'. Stesso identico catalogo.
    sent = check.process_author(AUTHOR, seen, initialized, "tok", "chat", dry_run=True)
    assert sent == 0, "senza nuovi libri non si deve notificare"


def _run_all():
    failures = 0
    for name, value in sorted(globals().items()):
        if name.startswith("test_") and callable(value):
            try:
                value()
                print(f"  PASS  {name}")
            except AssertionError as error:
                failures += 1
                print(f"  FAIL  {name}  -> {error}")
    print()
    print("Tutti i test di flusso passati." if not failures else f"{failures} test falliti.")
    return failures


if __name__ == "__main__":
    import sys
    sys.exit(1 if _run_all() else 0)
