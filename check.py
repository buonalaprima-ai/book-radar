#!/usr/bin/env python3
"""
Book Radar — polling script.

Per ogni autore in authors.json interroga Google Books, filtra con precisione
sull'autore canonico, e notifica via Telegram i nuovi libri non ancora visti.

Lo "stato" vive interamente nel repo:
  - seen_books.json          → ID volume gia notificati (evita doppioni)
  - initialized_authors.json → autori gia "inizializzati" (evita di notificare
                               l'intero catalogo storico al primo avvio)

Uso tipico:
  python check.py              # run reale (manda Telegram, aggiorna lo stato)
  python check.py --dry-run    # simula: stampa cosa notificherebbe, non scrive nulla
  python check.py --test-notification   # manda un messaggio Telegram di prova ed esce

Bot token e chat id si leggono da variabili d'ambiente o da un file .env locale
(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID). Opzionale: GOOGLE_BOOKS_API_KEY.

Dipendenze: nessuna (solo standard library).
"""

import argparse
import html
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

# MARK: - Costanti

GOOGLE_BOOKS_ENDPOINT = "https://www.googleapis.com/books/v1/volumes"
TELEGRAM_API_BASE = "https://api.telegram.org"

# Numero massimo di volumi richiesti per autore (limite hard di Google e' 40).
MAX_RESULTS = 40

# Piccola pausa tra le chiamate per non martellare l'API condivisa.
REQUEST_DELAY_SECONDS = 0.5

ROOT = Path(__file__).resolve().parent
AUTHORS_FILE = ROOT / "authors.json"
SEEN_FILE = ROOT / "seen_books.json"
INITIALIZED_FILE = ROOT / "initialized_authors.json"


# MARK: - Logging

def log(message):
    """Stampa un messaggio con flush immediato (utile nei log della Action)."""
    print(message, flush=True)


# MARK: - .env / environment

def load_dotenv(path=ROOT / ".env"):
    """
    Parser minimale per un file .env (KEY=VALUE per riga).

    Non sovrascrive variabili gia presenti nell'ambiente: in CI vincono i Secrets.
    Evita una dipendenza esterna (python-dotenv) per una cosa cosi semplice.
    """
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


# MARK: - JSON helpers

def load_json(path, default):
    """Carica un file JSON; ritorna `default` se il file non esiste."""
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        log(f"[ERRORE] {path.name} non e' un JSON valido: {error}")
        sys.exit(1)


def save_json(path, data):
    """Scrive un file JSON formattato (indentazione 2, newline finale)."""
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


# MARK: - Google Books

def google_books_search(query, api_key=None, max_results=MAX_RESULTS):
    """
    Interroga l'endpoint volumes ordinando per data (orderBy=newest).

    Ritorna la lista di volumi (puo' essere vuota). Solleva RuntimeError sugli
    errori HTTP cosi che il chiamante possa decidere se proseguire con gli altri
    autori senza far esplodere l'intera run.
    """
    params = {
        "q": query,
        "orderBy": "newest",
        "maxResults": max_results,
        "printType": "books",
    }
    if api_key:
        params["key"] = api_key

    url = f"{GOOGLE_BOOKS_ENDPOINT}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(url, headers={"Accept": "application/json"})

    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        if error.code == 429:
            raise RuntimeError(
                "quota Google Books esaurita (HTTP 429). "
                "Riprova piu' tardi o configura GOOGLE_BOOKS_API_KEY."
            ) from error
        raise RuntimeError(f"HTTP {error.code} da Google Books: {body[:200]}") from error
    except urllib.error.URLError as error:
        raise RuntimeError(f"errore di rete verso Google Books: {error.reason}") from error

    return payload.get("items", []) or []


def author_matches(volume, canonical_author):
    """
    Filtro di precisione: True se il volume elenca davvero `canonical_author`
    tra i suoi autori (match esatto, case-insensitive, spazi normalizzati).

    Google Books con `inauthor:` restituisce a volte volumi di omonimi o di
    altri autori: questo filtro li scarta alla fonte.
    """
    target = " ".join(canonical_author.lower().split())
    authors = volume.get("volumeInfo", {}).get("authors", []) or []
    for author in authors:
        if " ".join(author.lower().split()) == target:
            return True
    return False


def extract_book(volume):
    """Estrae i campi che ci interessano da un volume Google Books."""
    info = volume.get("volumeInfo", {})
    return {
        "id": volume.get("id", ""),
        "title": info.get("title", "(senza titolo)"),
        "subtitle": info.get("subtitle", ""),
        "authors": info.get("authors", []) or [],
        "published_date": info.get("publishedDate", "data sconosciuta"),
        "link": info.get("infoLink") or info.get("canonicalVolumeLink", ""),
    }


# MARK: - Telegram

def format_message(book):
    """Compone il testo Telegram (parse_mode HTML) per un libro."""
    title = html.escape(book["title"])
    if book["subtitle"]:
        title = f"{title}: {html.escape(book['subtitle'])}"
    authors = html.escape(", ".join(book["authors"]) or "autore sconosciuto")
    date = html.escape(book["published_date"])

    lines = [
        f"\U0001F4DA <b>{title}</b>",
        f"✍️ {authors}",
        f"\U0001F4C5 {date}",
    ]
    if book["link"]:
        lines.append(f"\U0001F517 {html.escape(book['link'])}")
    return "\n".join(lines)


def send_telegram(token, chat_id, text, dry_run=False):
    """
    Invia un messaggio via Bot API (sendMessage). In dry-run non manda nulla.

    Ritorna True se inviato (o se dry-run), False in caso di errore.
    """
    if dry_run:
        log("      [DRY-RUN] messaggio NON inviato. Anteprima:")
        for line in text.splitlines():
            log(f"        {line}")
        return True

    if not token or not chat_id:
        log("      [ERRORE] TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID mancanti.")
        return False

    url = f"{TELEGRAM_API_BASE}/bot{token}/sendMessage"
    data = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": "false",
    }).encode("utf-8")

    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=30) as response:
            result = json.loads(response.read().decode("utf-8"))
        if not result.get("ok"):
            log(f"      [ERRORE] Telegram ha risposto: {result}")
            return False
        return True
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        log(f"      [ERRORE] Telegram HTTP {error.code}: {body[:200]}")
        return False
    except urllib.error.URLError as error:
        log(f"      [ERRORE] rete Telegram: {error.reason}")
        return False


# MARK: - Elaborazione di un autore

def process_author(author, seen_ids, initialized_authors, token, chat_id, dry_run):
    """
    Elabora un singolo autore.

    Muta `seen_ids` (set) e `initialized_authors` (set) in memoria; la
    persistenza su file e' responsabilita' del chiamante (e viene saltata
    in dry-run).

    Ritorna il numero di notifiche inviate per questo autore.
    """
    name = author.get("name", "(senza nome)")
    canonical = author.get("canonicalAuthor", "")
    query = author.get("googleBooksQuery", "")

    if not canonical or not query:
        log(f"  [SALTATO] '{name}': manca canonicalAuthor o googleBooksQuery.")
        return 0

    log(f"  Autore: {name}  (canonical: \"{canonical}\")")

    api_key = os.environ.get("GOOGLE_BOOKS_API_KEY")
    try:
        volumes = google_books_search(query, api_key=api_key)
    except RuntimeError as error:
        log(f"    [ERRORE] {error}")
        return 0

    matched = [extract_book(v) for v in volumes if author_matches(v, canonical)]
    log(f"    Volumi trovati: {len(volumes)} | dopo filtro autore: {len(matched)}")

    is_first_run = canonical not in initialized_authors

    if is_first_run:
        # Primo avvio per questo autore: assorbi il catalogo storico SENZA notificare.
        for book in matched:
            seen_ids.add(book["id"])
        initialized_authors.add(canonical)
        log(f"    [INIT] Inizializzato con {len(matched)} libri esistenti. Nessuna notifica inviata.")
        return 0

    # Autore gia' inizializzato: novita' = volumi mai visti prima.
    new_books = [book for book in matched if book["id"] and book["id"] not in seen_ids]
    log(f"    Novita': {len(new_books)}")

    sent = 0
    for book in new_books:
        log(f"    -> NOVITA': \"{book['title']}\" ({book['published_date']})")
        if send_telegram(token, chat_id, format_message(book), dry_run=dry_run):
            seen_ids.add(book["id"])
            sent += 1
    return sent


# MARK: - Notifica di test

def run_test_notification(token, chat_id, dry_run):
    """Manda un messaggio Telegram di prova per verificare token/chat/formato."""
    sample = {
        "id": "test-volume",
        "title": "Libro di Prova",
        "subtitle": "verifica configurazione Book Radar",
        "authors": ["Autore Di Prova"],
        "published_date": time.strftime("%Y-%m-%d"),
        "link": "https://books.google.com/",
    }
    log("Invio notifica di test...")
    ok = send_telegram(token, chat_id, format_message(sample), dry_run=dry_run)
    if ok and not dry_run:
        log("Notifica di test inviata. Controlla Telegram.")
    elif not ok:
        log("Invio della notifica di test fallito. Controlla token/chat id.")
        sys.exit(1)


# MARK: - Main

def main():
    parser = argparse.ArgumentParser(description="Book Radar polling script.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simula tutto: stampa cosa notificherebbe, non manda Telegram, non scrive file.",
    )
    parser.add_argument(
        "--test-notification",
        action="store_true",
        help="Manda un singolo messaggio Telegram di prova ed esce.",
    )
    args = parser.parse_args()

    load_dotenv()
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")

    # Permette anche BOOK_RADAR_DRY_RUN=1 come variabile d'ambiente (comodo in CI).
    dry_run = args.dry_run or os.environ.get("BOOK_RADAR_DRY_RUN", "").lower() in ("1", "true", "yes")

    if args.test_notification:
        run_test_notification(token, chat_id, dry_run)
        return

    if dry_run:
        log("=== MODALITA' DRY-RUN: nessun Telegram inviato, nessun file modificato ===")

    authors = load_json(AUTHORS_FILE, default=[])
    if not authors:
        log(f"[AVVISO] {AUTHORS_FILE.name} e' vuoto o assente. Niente da fare.")
        return

    seen_ids = set(load_json(SEEN_FILE, default=[]))
    initialized_authors = set(load_json(INITIALIZED_FILE, default=[]))

    log(f"Autori da controllare: {len(authors)}")
    log(f"Libri gia' visti: {len(seen_ids)} | Autori inizializzati: {len(initialized_authors)}")
    log("")

    total_sent = 0
    for index, author in enumerate(authors):
        total_sent += process_author(author, seen_ids, initialized_authors, token, chat_id, dry_run)
        if index < len(authors) - 1:
            time.sleep(REQUEST_DELAY_SECONDS)

    log("")
    log(f"Totale notifiche inviate: {total_sent}")

    if dry_run:
        log("[DRY-RUN] Stato NON salvato su disco.")
        return

    # Persistenza dello stato (la Action committera' i file modificati).
    save_json(SEEN_FILE, sorted(seen_ids))
    save_json(INITIALIZED_FILE, sorted(initialized_authors))
    log("Stato salvato (seen_books.json, initialized_authors.json).")


if __name__ == "__main__":
    main()
