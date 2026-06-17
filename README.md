# 📚 Book Radar

Notifiche Telegram quando esce un nuovo libro (edizione **italiana**) di un autore che segui.

Niente backend sempre attivo: lo **stato vive interamente nel repo GitHub**. Un job
schedulato (`launchd`) sul Mac fa il polling su Google Books **una volta al giorno**,
e una piccola **interfaccia web** (GitHub Pages) fa da backoffice per gestire la lista
autori — leggendo/scrivendo `authors.json` nel repo via GitHub API, senza server.

> **Perché sul Mac e non su una GitHub Action?** Google Books restituisce le edizioni
> in base alla geolocalizzazione dell'IP della richiesta. I runner GitHub hanno IP USA
> e **non vedono le edizioni italiane**. Girando dal Mac (IP italiano) il filtro "solo
> italiano" funziona davvero. Lo stato resta comunque nel repo: il job fa `git pull`
> prima e `git push` dopo.

---

## Come funziona

```
                    ┌── interfaccia web (GitHub Pages) ──> modifica authors.json via GitHub API
                    │
authors.json ──> check.py (sul Mac, ogni giorno) ──> Google Books ──> filtro autore+lingua
                    │                                                          │
                    │                              dedup per opera ──> nuova? ──> Telegram
                    └──> seen_books.json / STATUS.md / usage.json  <── aggiorna stato + git push
```

### File nel repo

| File | Contenuto |
|------|-----------|
| `authors.json` | la tua lista di autori seguiti |
| `seen_books.json` | chiavi delle **opere** già notificate (`autore::titolo`) |
| `initialized_authors.json` | autori già "inizializzati" (catalogo storico assorbito) |
| `last_run.json` / `STATUS.md` | "battito": data ultimo controllo + riepilogo |
| `usage.json` | chiamate Google per giorno (stima quota) |
| `check.py` | lo script di polling (solo standard library Python, zero dipendenze) |
| `run.sh` | wrapper del job: `git pull` → `check.py` → `git push` |
| `com.bookradar.check.plist` | il job `launchd` (schedulazione quotidiana 09:00) |
| `docs/index.html` | l'interfaccia web (single-file, pubblicata da GitHub Pages) |
| `test_filter.py` / `test_flow.py` | test (nessuna rete richiesta) |

---

## Concetti chiave

### Solo edizioni italiane + dedup per opera

Google Books elenca la stessa opera in più edizioni e lingue (ognuna con un ID diverso).
Per avere notifiche pulite:

1. **Filtro lingua**: solo i volumi con `volumeInfo.language` uguale alla lingua scelta
   (default **italiano**, override con `BOOK_RADAR_LANG`). La lingua è una proprietà del
   volume, quindi indipendente da dove gira il polling.
2. **Dedup per opera**: i volumi vengono raggruppati per **titolo normalizzato** → una sola
   notifica per titolo, anche se esistono più edizioni/ristampe.

### Copertura completa (paginazione)

`orderBy=newest` di Google **non** ordina in modo affidabile per data, e una singola pagina
(~20 risultati) non copre i cataloghi prolifici. Lo script **pagina fino a esaurire i
risultati**, così l'ordine inaffidabile non conta e una nuova uscita viene sempre catturata.

### Filtro di precisione (autore esatto, insensibile ai diacritici)

Per ogni volume lo script verifica che il campo `authors` contenga davvero l'autore canonico
(match esatto, **case-insensitive e senza diacritici**: `Ryū` = `Ryü` = `Ryu`). Così scarta
gli omonimi e gestisce i nomi accentati.

### Grafie multiple per autore

Google a volte indicizza lo stesso autore con grafie diverse (es. `Ryu`/`Ryū`/`Ryü Murakami`),
e `inauthor:"..."` è sensibile agli accenti mentre l'operatore `OR` non funziona. Per questo un
autore può avere **più query** (`googleBooksQueries`): lo script le interroga tutte e unisce i
risultati. L'interfaccia web costruisce automaticamente queste query in fase di aggiunta.

### Primo avvio di un autore

La prima volta che un autore viene processato, lo script **assorbe tutto il suo catalogo
italiano attuale in `seen_books.json` senza notificare**, e lo segna in
`initialized_authors.json`. Da lì in poi notifica solo i libri **nuovi**.

---

## Formato di `authors.json`

```json
[
  {
    "name": "Ryu Murakami",
    "canonicalAuthor": "Ryu Murakami",
    "googleBooksQueries": [
      "inauthor:\"Ryū Murakami\"",
      "inauthor:\"Ryü Murakami\""
    ]
  }
]
```

- `name`: etichetta leggibile, come la scrivi tu.
- `canonicalAuthor`: nome usato per il confronto (insensibile a maiuscole/diacritici).
- `googleBooksQueries`: una query `inauthor` per ogni grafia con edizioni italiane.
  (È supportato anche il vecchio campo singolo `googleBooksQuery` per compatibilità.)

Normalmente non lo modifichi a mano: ci pensa l'**interfaccia web**.

---

## Setup passo-passo

### 1. Bot Telegram (BotFather)

1. Su Telegram apri **[@BotFather](https://t.me/BotFather)** (con la spunta blu).
2. Manda `/newbot`, scegli nome e username (deve finire per `bot`).
3. Ottieni un **token** tipo `123456789:ABCdef...` → è il tuo `TELEGRAM_BOT_TOKEN`.

### 2. Chat id

1. Apri una chat col tuo bot e mandagli un messaggio (es. `ciao`) — obbligatorio: il bot non
   può scriverti per primo.
2. Visita `https://api.telegram.org/bot<TOKEN>/getUpdates` e cerca `"chat":{"id":...}`.
   Quel numero è il tuo `TELEGRAM_CHAT_ID`.

### 3. API key Google Books (obbligatoria)

La quota anonima condivisa di Google è cronicamente esaurita, quindi **serve una API key**.

1. Vai su [Google Cloud Console](https://console.cloud.google.com/), crea un progetto.
2. Abilita la **Books API**: <https://console.cloud.google.com/apis/library/books.googleapis.com>.
3. **Credentials → Create credentials → API key**. Copia la chiave `AIza...`.
   (Con un'organizzazione Google può servire restringere la chiave alla Books API: in tal caso
   abilita prima la Books API, poi crea la chiave.)

### 4. Configura i segreti locali (`.env`)

```bash
cp .env.example .env
# poi apri .env e incolla:
#   TELEGRAM_BOT_TOKEN=...
#   TELEGRAM_CHAT_ID=...
#   GOOGLE_BOOKS_API_KEY=AIza...
```

Il file `.env` è **gitignorato**: non finisce mai nel repo.

### 5. Schedulazione sul Mac (job giornaliero)

```bash
# Installa il job tra i LaunchAgents dell'utente
cp com.bookradar.check.plist ~/Library/LaunchAgents/

# Caricalo (da ora gira ogni giorno alle 09:00)
launchctl load ~/Library/LaunchAgents/com.bookradar.check.plist
```

> **Importante:** il progetto deve stare **fuori** da `~/Desktop`, `~/Documenti`, `~/Download`
> (cartelle protette da macOS/TCC), altrimenti `launchd` non può accedervi. Posizione
> consigliata: `~/book-radar`.

- Mac acceso alle 09:00 → parte alle 09:00.
- Mac spento/sospeso alle 09:00 → parte al **primo risveglio/login successivo** (recupera
  l'esecuzione mancata una volta). Quindi serve accendere il Mac almeno una volta al giorno.
- Cambiare orario: modifica `Hour`/`Minute` nel plist, poi `unload` + `load`.

### 6. Interfaccia web (GitHub Pages)

Il repo deve essere **pubblico** (Pages gratis richiede repo pubblico). Nessun segreto è nel
repo: il `.env` è escluso, e il token GitHub dell'interfaccia vive solo nel browser.

1. **Settings → Pages → Source: Deploy from a branch → `main` / cartella `/docs`** → Save.
2. Dopo ~1 minuto Pages ti dà l'indirizzo: `https://<tuo-utente>.github.io/book-radar/`.
3. Apri l'indirizzo, poi **⚙️ Impostazioni** e inserisci:
   - **GitHub token** (vedi sotto)
   - **Google Books API key** (la stessa del `.env`)

#### Token GitHub per l'interfaccia

L'interfaccia modifica `authors.json` via GitHub API. Crea un **fine-grained token**:

- **Repository access** → solo la repo `book-radar`
- **Permissions → Contents: Read and write** (più `Workflows: Read and write` se il token serve
  anche al `git push` da Terminale)

Genera da **GitHub → Settings → Developer settings → Personal access tokens → Fine-grained**.
Il token viene salvato **solo nel browser** (localStorage), mai nel repo.

---

## Usare l'interfaccia web

- **Lista autori**: mostra il contenuto di `authors.json`.
- **Aggiungi autore**: scrivi un nome → "Cerca varianti". L'interfaccia scopre le grafie (anche
  accentate), **verifica il conteggio reale con `inauthor`** e ti mostra i libri italiani
  effettivi. Scegli la variante e premi "Aggiungi al repo".
- **Rimuovi autore**: bottone "Rimuovi".
- **Anteprima**: i libri italiani che il sistema seguirà per quell'autore.
- **Quota**: in alto, stima delle chiamate Google usate oggi (vedi sotto).
- **↻ Ricarica**: ri-legge `authors.json` dal repo (non lancia il controllo).

Le notifiche le manda **solo lo script sul Mac**: l'interfaccia gestisce solo la lista autori.

---

## Verificare che lo script giri ogni giorno

A ogni esecuzione lo script aggiorna e ripubblica nel repo:

- **[`STATUS.md`](STATUS.md)** — leggibile su GitHub: data/ora ultimo controllo, esito, opere
  monitorate, notifiche inviate, chiamate Google. Se la data non avanza, il job non sta girando.
- **`last_run.json`** — le stesse info in formato machine-readable.
- La **cronologia commit** (`chore: aggiorna stato e timestamp...`) è di per sé una prova.

---

## Quota Google

La API key ha **1.000 chiamate/giorno**. La usano sia lo script (dal `.env`) sia l'interfaccia
(dalla key salvata nel browser).

- Lo script conta le sue chiamate per run e il totale giornaliero in `usage.json`, mostrato in
  `STATUS.md` (es. *Chiamate Google oggi (script): 24 / 1000*).
- L'interfaccia mostra una **stima** "usate oggi" = script (da `usage.json`) + interfaccia
  (contatore nel browser). Google non espone la quota residua via API, quindi è una stima per
  eccesso. Con un uso personale normale si resta ampiamente sotto il limite.

---

## Comandi rapidi

| Comando | Cosa fa |
|---------|---------|
| `python3 check.py` | Run reale: polling + Telegram + salva stato |
| `python3 check.py --dry-run` | Simula tutto, non manda nulla, non scrive |
| `python3 check.py --test-notification` | Manda un Telegram di prova ed esce |
| `python3 test_filter.py` / `test_flow.py` | Test (nessuna rete) |
| `launchctl start com.bookradar.check` | Lancia subito il job schedulato |
| `tail -f book-radar.log` | Segui il log del job |
| `launchctl unload ~/Library/LaunchAgents/com.bookradar.check.plist` | Disattiva il job |

Variabili d'ambiente: `BOOK_RADAR_DRY_RUN=1` (dry-run), `BOOK_RADAR_LANG` (lingua edizioni,
default `it`), `BOOK_RADAR_COUNTRY` (mercato, default `IT`).
