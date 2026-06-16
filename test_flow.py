#!/usr/bin/env python3
"""
Test d'integrazione OFFLINE del flusso completo (process_author).

Sostituisce (monkeypatch) la chiamata di rete a Google Books con risposte
finte, cosi' da verificare senza rete:
  1. primo avvio   -> inizializza, NESSUNA notifica
  2. run seguente  -> un nuovo titolo genera UNA notifica
  3. filtro lingua -> le edizioni non italiane vengono scartate
  4. dedup opera   -> due edizioni dello stesso titolo = una sola notifica
  5. omonimo       -> autore diverso scartato dal filtro

Telegram gira sempre in dry-run (nessun invio reale).

Eseguibile direttamente: `python test_flow.py`
"""

import check


def _volume(vol_id, title, authors, lang="it", date="2024-01-01"):
    return {
        "id": vol_id,
        "volumeInfo": {
            "title": title,
            "authors": authors,
            "language": lang,
            "publishedDate": date,
            "infoLink": f"https://books.google.com/{vol_id}",
        },
    }


AUTHOR = {
    "name": "John Niven",
    "canonicalAuthor": "John Niven",
    "googleBooksQuery": 'inauthor:"John Niven"',
}


def _key(title):
    return check.work_key("John Niven", title)


def test_flusso_completo():
    seen = set()
    initialized = set()

    # --- Run 1: primo avvio. Catalogo IT + un'edizione EN + un omonimo.
    check.google_books_search = lambda query, **kw: [
        _volume("it-1", "Maschio Bianco Etero", ["John Niven"], lang="it"),
        _volume("it-2", "A Volte Ritorno", ["John Niven"], lang="it"),
        _volume("en-1", "Straight White Male", ["John Niven"], lang="en"),   # lingua sbagliata
        _volume("hist-1", "Martin Van Buren", ["John Niven"], lang="it"),    # omonimo storico (titolo a se')
    ]
    sent = check.process_author(AUTHOR, seen, initialized, "tok", "chat", dry_run=True)

    assert sent == 0, "al primo avvio non si deve notificare nulla"
    assert "John Niven" in initialized, "l'autore deve risultare inizializzato"
    # Solo le opere italiane entrano nel visto (incluso l'omonimo IT, limite noto del match per nome).
    assert _key("Maschio Bianco Etero") in seen
    assert _key("A Volte Ritorno") in seen
    assert _key("Straight White Male") not in seen, "l'edizione EN va scartata dal filtro lingua"

    # --- Run 2: esce un titolo nuovo in italiano + ricompare un'edizione EN (da scartare).
    check.google_books_search = lambda query, **kw: [
        _volume("it-3", "Padri Nostri", ["John Niven"], lang="it", date="2026-02-02"),
        _volume("it-1", "Maschio Bianco Etero", ["John Niven"], lang="it"),
        _volume("en-2", "The Fathers", ["John Niven"], lang="en"),
    ]
    sent = check.process_author(AUTHOR, seen, initialized, "tok", "chat", dry_run=True)

    assert sent == 1, "deve notificare esattamente il nuovo titolo italiano"
    assert _key("Padri Nostri") in seen

    # --- Run 3: due edizioni italiane DELLO STESSO titolo gia' notificato -> 0 notifiche.
    check.google_books_search = lambda query, **kw: [
        _volume("it-3a", "Padri Nostri", ["John Niven"], lang="it"),
        _volume("it-3b", "Padri Nostri", ["John Niven"], lang="it"),  # ristampa, ID diverso
    ]
    sent = check.process_author(AUTHOR, seen, initialized, "tok", "chat", dry_run=True)
    assert sent == 0, "edizioni diverse dello stesso titolo non devono ri-notificare"


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
