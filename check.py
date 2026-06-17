#!/usr/bin/env python3
"""
Book Radar — polling script.

Per ogni autore in authors.json interroga Google Books, filtra con precisione
sull'autore canonico, e notifica via Telegram i nuovi libri non ancora visti.

Notifica una sola volta per OPERA (dedup sul titolo normalizzato) e solo le
edizioni nella lingua scelta (default: italiano). La lingua e' una proprieta'
del volume, quindi i risultati sono indipendenti dalla regione del server che
fa il polling (locale o runner GitHub).

Lo "stato" vive interamente nel repo:
  - seen_books.json          → chiavi opera gia notificate ("autore::titolo"),
                               evita doppioni anche tra edizioni diverse
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
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

# MARK: - Costanti

GOOGLE_BOOKS_ENDPOINT = "https://www.googleapis.com/books/v1/volumes"
TELEGRAM_API_BASE = "https://api.telegram.org"

# Dimensione pagina richiesta a Google (limite hard dell'API e' 40).
MAX_RESULTS = 40

# Tetto di pagine da scaricare per autore (sicurezza anti-loop). 8 x 40 = fino a
# ~320 risultati; in pratica ci si ferma prima quando i risultati si esauriscono.
MAX_PAGES = 8

# Piccola pausa tra le chiamate per non martellare l'API condivisa.
REQUEST_DELAY_SECONDS = 0.5

# Lingua delle edizioni da notificare (ISO 639-1). Override: env BOOK_RADAR_LANG.
# Solo i volumi con volumeInfo.language esattamente uguale vengono considerati.
DEFAULT_LANGUAGE = "it"

# Mercato Google Books, per orientare i risultati. Override: env BOOK_RADAR_COUNTRY.
DEFAULT_COUNTRY = "IT"

ROOT = Path(__file__).resolve().parent
AUTHORS_FILE = ROOT / "authors.json"
SEEN_FILE = ROOT / "seen_books.json"
INITIALIZED_FILE = ROOT / "initialized_authors.json"

# "Battito cardiaco": aggiornati a ogni run e committati, per verificare dal repo
# che lo script gira davvero ogni giorno.
STATUS_JSON_FILE = ROOT / "last_run.json"
STATUS_MD_FILE = ROOT / "STATUS.md"
USAGE_FILE = ROOT / "usage.json"  # chiamate Google per giorno (ultimi giorni)

# Quota giornaliera della API key Google Books (per la stima "usate/totali").
GOOGLE_DAILY_QUOTA = 1000

# Contatore delle chiamate HTTP a Google Books nella run corrente.
google_call_count = 0


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


# MARK: - Stato / battito cardiaco

def update_usage(now, calls):
    """
    Accumula le chiamate Google per giorno in usage.json (tiene gli ultimi giorni).
    Ritorna il totale di chiamate di OGGI (tutte le run incluse questa).
    """
    today = now.strftime("%Y-%m-%d")
    usage = load_json(USAGE_FILE, default={})
    if not isinstance(usage, dict):
        usage = {}
    usage[today] = usage.get(today, 0) + calls
    # Conserva solo gli ultimi 14 giorni per non far crescere il file.
    cutoff = (now - timedelta(days=14)).strftime("%Y-%m-%d")
    usage = {day: count for day, count in usage.items() if day >= cutoff}
    save_json(USAGE_FILE, usage)
    return usage[today]


def write_status(authors_count, works_tracked, notifications_sent, had_errors, run_calls, today_calls):
    """
    Scrive last_run.json (machine) e STATUS.md (leggibile su GitHub) con il
    timestamp dell'ultima esecuzione e un riepilogo. Viene aggiornato a OGNI
    run (anche senza novita'), cosi' dal repo si vede che lo script gira.
    """
    now = datetime.now().astimezone()
    status = {
        "last_run": now.strftime("%Y-%m-%d %H:%M:%S %z"),
        "last_run_iso": now.isoformat(timespec="seconds"),
        "outcome": "errori durante il run" if had_errors else "ok",
        "authors_checked": authors_count,
        "works_tracked": works_tracked,
        "notifications_sent": notifications_sent,
        "google_calls_this_run": run_calls,
        "google_calls_today": today_calls,
        "google_daily_quota": GOOGLE_DAILY_QUOTA,
    }
    save_json(STATUS_JSON_FILE, status)

    outcome_icon = "⚠️" if had_errors else "✅"
    markdown = (
        "# 📚 Book Radar — stato\n\n"
        f"**Ultimo controllo:** {status['last_run']}\n\n"
        f"- Esito: {outcome_icon} {status['outcome']}\n"
        f"- Autori controllati: {authors_count}\n"
        f"- Opere monitorate: {works_tracked}\n"
        f"- Notifiche inviate in questo run: {notifications_sent}\n"
        f"- Chiamate Google in questo run: {run_calls}\n"
        f"- Chiamate Google oggi (script): {today_calls} / {GOOGLE_DAILY_QUOTA}\n\n"
        "> File aggiornato automaticamente a ogni esecuzione dello script.\n"
        "> Se questa data non avanza di giorno in giorno, il job sul Mac non sta girando.\n"
    )
    STATUS_MD_FILE.write_text(markdown, encoding="utf-8")


# MARK: - Google Books

def _fetch_page(query, start_index, api_key, lang_restrict, country):
    """Scarica una singola pagina di risultati. Solleva RuntimeError su errore HTTP."""
    params = {
        "q": query,
        "orderBy": "newest",
        "maxResults": MAX_RESULTS,
        "startIndex": start_index,
        "printType": "books",
    }
    if lang_restrict:
        params["langRestrict"] = lang_restrict
    if country:
        params["country"] = country
    if api_key:
        params["key"] = api_key

    url = f"{GOOGLE_BOOKS_ENDPOINT}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(url, headers={"Accept": "application/json"})

    # Google restituisce ogni tanto 503/errori di rete transitori: qualche retry.
    last_error = None
    for attempt in range(3):
        global google_call_count
        google_call_count += 1  # ogni richiesta HTTP consuma una unita' di quota
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                payload = json.loads(response.read().decode("utf-8"))
            return payload.get("items", []) or []
        except urllib.error.HTTPError as error:
            body = error.read().decode("utf-8", errors="replace")
            if error.code == 429:
                raise RuntimeError(
                    "quota Google Books esaurita (HTTP 429). "
                    "Riprova piu' tardi o configura GOOGLE_BOOKS_API_KEY."
                ) from error
            if error.code in (500, 502, 503, 504):
                last_error = RuntimeError(f"HTTP {error.code} da Google Books (transitorio)")
                time.sleep(1.5 * (attempt + 1))
                continue
            raise RuntimeError(f"HTTP {error.code} da Google Books: {body[:200]}") from error
        except urllib.error.URLError as error:
            last_error = RuntimeError(f"errore di rete verso Google Books: {error.reason}")
            time.sleep(1.5 * (attempt + 1))
            continue
    raise last_error


def google_books_search(query, api_key=None, lang_restrict=None, country=None):
    """
    Scarica TUTTI i volumi raggiungibili per la query, paginando con startIndex.

    Perche' paginare: `orderBy=newest` di Google NON ordina in modo affidabile per
    data e una singola pagina (~20 risultati) non copre il catalogo di un autore
    prolifico. Una nuova uscita potrebbe non comparire nella prima pagina e non
    verrebbe mai notificata. Paginando fino a esaurimento prendiamo l'intero
    catalogo raggiungibile, quindi l'ordine inaffidabile non conta piu'.

    `lang_restrict` e `country` orientano i risultati (sono solo suggerimenti: il
    filtro rigido per lingua lo fa il chiamante). Solleva RuntimeError su errore.
    """
    collected = []
    seen_ids = set()
    start = 0
    for _ in range(MAX_PAGES):
        items = _fetch_page(query, start, api_key, lang_restrict, country)
        if not items:
            break  # pagina vuota: risultati esauriti
        added = 0
        for volume in items:
            vid = volume.get("id")
            if vid and vid not in seen_ids:
                seen_ids.add(vid)
                collected.append(volume)
                added += 1
        start += len(items)
        if added == 0:
            break  # solo doppioni: Google ha smesso di restituire roba nuova
        time.sleep(REQUEST_DELAY_SECONDS)
    return collected


def author_matches(volume, canonical_author):
    """
    Filtro di precisione: True se il volume elenca davvero `canonical_author`
    tra i suoi autori (match esatto, case-insensitive, spazi normalizzati).

    Google Books con `inauthor:` restituisce a volte volumi di omonimi o di
    altri autori: questo filtro li scarta alla fonte.
    """
    target = normalize_name(canonical_author)
    authors = volume.get("volumeInfo", {}).get("authors", []) or []
    for author in authors:
        if normalize_name(author) == target:
            return True
    return False


def normalize_name(name):
    """
    Normalizza un nome autore per il confronto: rimuove i diacritici, minuscolo,
    spazi compattati. Cosi' 'Ryū Murakami', 'Ryü Murakami' e 'Ryu Murakami'
    risultano uguali (Google indicizza lo stesso autore con grafie diverse).
    """
    decomposed = unicodedata.normalize("NFD", name or "")
    without_marks = "".join(c for c in decomposed if unicodedata.category(c) != "Mn")
    return " ".join(without_marks.lower().split())


def volume_language(volume):
    """Ritorna il codice lingua del volume (es. 'it', 'en') o None se assente."""
    return volume.get("volumeInfo", {}).get("language")


def normalize_title(title):
    """Normalizza un titolo per il confronto: minuscolo, spazi compattati."""
    return " ".join((title or "").lower().split())


def work_key(canonical_author, title):
    """
    Chiave identificativa di un'OPERA: "autore::titolo normalizzato".

    Edizioni diverse con lo stesso titolo (es. ristampe) condividono la chiave,
    quindi generano una sola notifica. Lo spazio dei nomi per autore evita
    collisioni tra autori con un titolo identico. L'autore e' normalizzato senza
    diacritici cosi' le grafie diverse condividono lo stesso spazio dei nomi.
    """
    return f"{normalize_name(canonical_author)}::{normalize_title(title)}"


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

def process_author(author, seen_keys, initialized_authors, token, chat_id, dry_run):
    """
    Elabora un singolo autore.

    Muta `seen_keys` (set di chiavi opera) e `initialized_authors` (set) in
    memoria; la persistenza su file e' responsabilita' del chiamante (e viene
    saltata in dry-run).

    Pipeline: ricerca -> filtro autore esatto -> filtro lingua -> dedup per
    opera (titolo normalizzato) -> novita' = opere mai viste.

    Ritorna una tupla (notifiche_inviate, errore) dove `errore` e' True se la
    ricerca per questo autore e' fallita (es. quota Google esaurita).
    """
    name = author.get("name", "(senza nome)")
    canonical = author.get("canonicalAuthor", "")
    # Una o piu' query: ogni grafia dell'autore (diacritici) va interrogata a parte,
    # perche' inauthor e' sensibile agli accenti e l'operatore OR non funziona.
    queries = author.get("googleBooksQueries")
    if not queries:
        single = author.get("googleBooksQuery", "")
        queries = [single] if single else []

    if not canonical or not queries:
        log(f"  [SALTATO] '{name}': manca canonicalAuthor o googleBooksQuery(es).")
        return 0, True

    language = os.environ.get("BOOK_RADAR_LANG", DEFAULT_LANGUAGE)
    country = os.environ.get("BOOK_RADAR_COUNTRY", DEFAULT_COUNTRY)
    log(f"  Autore: {name}  (canonical: \"{canonical}\", query: {len(queries)}, lingua: {language})")

    api_key = os.environ.get("GOOGLE_BOOKS_API_KEY")
    volumes = []
    collected_ids = set()
    try:
        for query in queries:
            for volume in google_books_search(query, api_key=api_key,
                                              lang_restrict=language, country=country):
                vid = volume.get("id")
                if vid and vid not in collected_ids:
                    collected_ids.add(vid)
                    volumes.append(volume)
    except RuntimeError as error:
        log(f"    [ERRORE] {error}")
        return 0, True

    # Filtro: autore esatto E lingua esatta (langRestrict di Google e' solo un
    # suggerimento, quindi la lingua va ri-verificata qui in modo rigido).
    matched = [v for v in volumes
               if author_matches(v, canonical) and volume_language(v) == language]

    # Dedup per opera: tengo il primo volume per ogni chiave (orderBy=newest,
    # quindi il piu' recente) come rappresentante per il messaggio.
    works = {}
    for volume in matched:
        book = extract_book(volume)
        key = work_key(canonical, book["title"])
        works.setdefault(key, book)

    log(f"    Volumi: {len(volumes)} trovati | {len(matched)} dopo filtro "
        f"autore+lingua | {len(works)} opere distinte")

    is_first_run = canonical not in initialized_authors

    if is_first_run:
        # Primo avvio per questo autore: assorbi il catalogo storico SENZA notificare.
        seen_keys.update(works.keys())
        initialized_authors.add(canonical)
        log(f"    [INIT] Inizializzato con {len(works)} opere esistenti. Nessuna notifica inviata.")
        return 0, False

    # Autore gia' inizializzato: novita' = opere mai viste prima.
    new_works = [(key, book) for key, book in works.items() if key not in seen_keys]
    log(f"    Novita': {len(new_works)}")

    sent = 0
    for key, book in new_works:
        log(f"    -> NOVITA': \"{book['title']}\" ({book['published_date']})")
        if send_telegram(token, chat_id, format_message(book), dry_run=dry_run):
            seen_keys.add(key)
            sent += 1
    return sent, False


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

    seen_keys = set(load_json(SEEN_FILE, default=[]))
    initialized_authors = set(load_json(INITIALIZED_FILE, default=[]))

    log(f"Autori da controllare: {len(authors)}")
    log(f"Opere gia' viste: {len(seen_keys)} | Autori inizializzati: {len(initialized_authors)}")
    log("")

    total_sent = 0
    had_errors = False
    for index, author in enumerate(authors):
        sent, error = process_author(author, seen_keys, initialized_authors, token, chat_id, dry_run)
        total_sent += sent
        had_errors = had_errors or error
        if index < len(authors) - 1:
            time.sleep(REQUEST_DELAY_SECONDS)

    log("")
    log(f"Totale notifiche inviate: {total_sent}")
    log(f"Chiamate Google in questo run: {google_call_count}")

    if dry_run:
        log("[DRY-RUN] Stato NON salvato su disco.")
        return

    # Persistenza dello stato (run.sh committera' i file modificati).
    save_json(SEEN_FILE, sorted(seen_keys))
    save_json(INITIALIZED_FILE, sorted(initialized_authors))
    today_calls = update_usage(datetime.now().astimezone(), google_call_count)
    write_status(len(authors), len(seen_keys), total_sent, had_errors, google_call_count, today_calls)
    log(f"Chiamate Google oggi (script): {today_calls} / {GOOGLE_DAILY_QUOTA}")
    log("Stato salvato (seen_books.json, initialized_authors.json, last_run.json, STATUS.md, usage.json).")


if __name__ == "__main__":
    main()
