# 📚 Book Radar

Notifiche Telegram quando esce un nuovo libro di un autore che segui.

Niente backend sempre attivo: lo **stato vive interamente nel repo GitHub**. Un
job schedulato (`launchd`) sul Mac fa polling su Google Books una volta al
giorno, e un'app iOS (cartella `ios/`, vedi PARTE 2) fa da backoffice per
gestire la lista autori.

> **Perché sul Mac e non su una GitHub Action?** Google Books restituisce le
> edizioni in base alla geolocalizzazione dell'IP della richiesta. I runner
> GitHub hanno IP USA e **non vedono le edizioni italiane**. Girando dal Mac
> (IP italiano) il filtro "solo italiano" funziona davvero. Lo stato resta
> comunque nel repo: il job fa `git pull` prima e `git push` dopo.

---

## Come funziona

```
authors.json ──> check.py ──> Google Books API ──> filtro autore ──> nuovi libri?
                                                                          │
                              seen_books.json <── aggiorna stato <── notifica Telegram
```

- **`authors.json`** — la tua lista di autori seguiti.
- **`seen_books.json`** — chiavi delle **opere** gia notificate (`autore::titolo`).
  Si ragiona per opera, non per singola edizione: così ristampe/edizioni diverse
  dello stesso titolo non generano notifiche doppie.
- **`initialized_authors.json`** — autori gia "inizializzati", così aggiungere un
  nuovo autore non ti sommerge di notifiche del suo catalogo storico.

### Lingua e dedup (importante)

Google Books elenca la stessa opera in più edizioni e lingue, ognuna con un ID
di volume diverso, **e il set di edizioni restituito dipende dalla regione del
server** che fa la richiesta. Per avere notifiche pulite e deterministiche:

1. **Filtro lingua**: vengono considerate solo le edizioni con
   `volumeInfo.language` uguale alla lingua scelta (default **italiano**,
   configurabile con la variabile d'ambiente `BOOK_RADAR_LANG`). La lingua è una
   proprietà del volume, quindi il risultato non dipende da dove gira il polling.
2. **Dedup per opera**: i volumi vengono raggruppati per titolo normalizzato →
   **una sola notifica per titolo**.

> Nota: due opere diverse con titolo tradotto diverso (es. l'originale inglese
> *The Fathers* e l'edizione italiana *Padri nostri*) restano notifiche separate,
> perché il titolo è diverso. Con `BOOK_RADAR_LANG=it` riceverai comunque solo
> l'edizione italiana.
- **`check.py`** — lo script di polling (solo standard library Python, zero dipendenze).
- **`run.sh`** — wrapper eseguito dal job: `git pull` → `check.py` → `git push`.
- **`com.bookradar.check.plist`** — il job `launchd` (schedulazione quotidiana).

### Il filtro di precisione

Google Books con `inauthor:"..."` a volte restituisce libri di **omonimi** o altri
autori. Per ogni volume lo script verifica che il campo `authors` contenga davvero
il `canonicalAuthor` (match esatto, case-insensitive). Così niente falsi positivi.

### Primo avvio di un autore

La prima volta che un autore viene processato, lo script **assorbe tutto il suo
catalogo attuale in `seen_books.json` senza notificare**, e lo segna in
`initialized_authors.json`. Da lì in poi notifica solo i libri nuovi.

---

## Setup passo-passo

### 1. Crea il bot Telegram (BotFather)

1. Su Telegram apri una chat con **[@BotFather](https://t.me/BotFather)**.
2. Manda `/newbot` e segui le istruzioni (nome + username che finisce per `bot`).
3. BotFather ti dà un **token** tipo `123456789:ABCdef...`. Questo è il tuo
   `TELEGRAM_BOT_TOKEN`. Tienilo segreto.

### 2. Ottieni il tuo chat id

1. Apri una chat col bot appena creato e manda un messaggio qualsiasi (es. `ciao`).
   Questo passaggio è **obbligatorio**: il bot non può scriverti per primo.
2. Visita (sostituendo `<TOKEN>`):
   `https://api.telegram.org/bot<TOKEN>/getUpdates`
3. Nel JSON cerca `"chat":{"id":123456789,...}`. Quel numero è il tuo
   `TELEGRAM_CHAT_ID`.

> Per ricevere le notifiche in un **gruppo**: aggiungi il bot al gruppo, manda un
> messaggio nel gruppo, poi rileggi `getUpdates`: il chat id del gruppo è negativo
> (es. `-100123...`).

### 3. Test in locale (consigliato prima di tutto)

```bash
# 1. Configura i segreti localmente (NON verranno committati: .env è gitignorato)
cp .env.example .env
#   poi apri .env e incolla TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID

# 2. Verifica che Telegram funzioni: manda un messaggio di prova
python3 check.py --test-notification

# 3. Lancia i test (nessuna rete richiesta)
python3 test_filter.py
python3 test_flow.py

# 4. DRY RUN: fa tutto il polling e stampa cosa NOTIFICHEREBBE,
#    senza mandare Telegram e senza scrivere su disco
python3 check.py --dry-run

# 5. Run reale in locale (manda Telegram, aggiorna i file di stato)
python3 check.py
```

> **Quota Google Books:** l'endpoint funziona senza API key, ma la quota anonima è
> condivisa e a volte restituisce `HTTP 429`. Per uso personale (1 run/giorno) di
> norma basta. Se ci sbatti spesso, crea una API key su Google Cloud Console
> (abilita "Books API") e mettila in `.env` come `GOOGLE_BOOKS_API_KEY`.

### 4. Crea la repo GitHub e fai il primo push

```bash
cd book-radar
git init
git add .
git commit -m "Book Radar: setup iniziale"
# Crea una repo PRIVATA su github.com, poi:
git remote add origin https://github.com/<tuo-utente>/book-radar.git
git branch -M main
git push -u origin main
```

> Verifica che `.env` **non** sia tra i file committati (`git status` non deve
> mostrarlo). È protetto da `.gitignore`. Il push di `seen_books.json` userà le
> stesse credenziali (token GitHub, vedi sotto), già memorizzate nel keychain.

### 5. Schedulazione sul Mac (job giornaliero)

Il polling gira sul Mac tramite `launchd`, così Google Books vede le edizioni
italiane (vedi nota in cima al README). Sono due comandi:

```bash
# Installa il job (copia il plist tra i LaunchAgents dell'utente)
cp com.bookradar.check.plist ~/Library/LaunchAgents/

# Caricalo (da ora gira ogni giorno alle 09:00)
launchctl load ~/Library/LaunchAgents/com.bookradar.check.plist
```

Per **lanciarlo subito a mano** (test, senza aspettare le 09:00):

```bash
launchctl start com.bookradar.check
# poi guarda il log:
tail -n 30 book-radar.log
```

Per **disattivarlo**:

```bash
launchctl unload ~/Library/LaunchAgents/com.bookradar.check.plist
```

> Se il Mac è spento o sospeso alle 09:00, `launchd` esegue il job al primo
> risveglio successivo. Per cambiare orario, modifica `Hour`/`Minute` nel plist,
> poi `unload` + `load` di nuovo.

### Verificare che lo script giri davvero ogni giorno

A ogni esecuzione lo script aggiorna e ripubblica nel repo:

- **`STATUS.md`** — leggibile direttamente su GitHub (apri il file nel repo):
  mostra **data e ora dell'ultimo controllo**, esito, numero di opere monitorate
  e notifiche inviate. Se quella data non avanza di giorno in giorno, il job non
  sta girando.
- **`last_run.json`** — la stessa informazione in formato machine-readable (utile
  anche all'app iOS per mostrarti "ultimo controllo" in una schermata).

Inoltre la **cronologia commit** del repo (`chore: aggiorna stato e timestamp...`)
è di per sé una prova: dovresti vederne uno nuovo ogni giorno.

---

## Personal Access Token GitHub (per l'app iOS)

L'app iOS modifica `authors.json` via GitHub API e ha bisogno di un token.
**Scope minimi:**

- **Fine-grained token** (consigliato): Repository access → solo la repo
  `book-radar`; Permissions → **Contents: Read and write**. Nient'altro.
- **Token classico** (alternativa): scope **`repo`** (purtroppo è il più granulare
  disponibile per i classici su repo private).

Genera da: **GitHub → Settings → Developer settings → Personal access tokens**.
Il token va inserito nell'app (salvato in Keychain), **mai** scritto nel codice o
nel repo.

---

## Comandi rapidi

| Comando | Cosa fa |
|---------|---------|
| `python3 check.py` | Run reale: polling + Telegram + salva stato |
| `python3 check.py --dry-run` | Simula tutto, non manda nulla, non scrive |
| `python3 check.py --test-notification` | Manda un Telegram di prova ed esce |
| `python3 test_filter.py` | Test del filtro di precisione |
| `python3 test_flow.py` | Test d'integrazione offline del flusso |
| `launchctl start com.bookradar.check` | Lancia subito il job schedulato |
| `tail -f book-radar.log` | Segui il log del job in tempo reale |

Variabili d'ambiente: `BOOK_RADAR_DRY_RUN=1` (dry-run), `BOOK_RADAR_LANG=it`
(lingua delle edizioni, default `it`).

---

## Aggiungere un autore a mano (senza l'app)

Aggiungi un oggetto a `authors.json`:

```json
{
  "name": "H.P. Lovecraft",
  "canonicalAuthor": "H. P. Lovecraft",
  "googleBooksQuery": "inauthor:\"H. P. Lovecraft\""
}
```

- `name`: etichetta leggibile, come la scrivi tu.
- `canonicalAuthor`: la stringa autore **esatta** come appare in Google Books.
- `googleBooksQuery`: di norma `inauthor:"<canonicalAuthor>"`.

Al prossimo run l'autore viene inizializzato (catalogo storico assorbito senza
notifiche) e da lì in poi riceverai solo le novità.
