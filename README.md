# 📚 Book Radar

Notifiche Telegram quando esce un nuovo libro di un autore che segui.

Niente backend sempre attivo: lo **stato vive interamente nel repo GitHub**. Una
GitHub Action quotidiana fa polling su Google Books, e un'app iOS (cartella
`ios/`, vedi PARTE 2) fa da backoffice per gestire la lista autori.

---

## Come funziona

```
authors.json ──> check.py ──> Google Books API ──> filtro autore ──> nuovi libri?
                                                                          │
                              seen_books.json <── aggiorna stato <── notifica Telegram
```

- **`authors.json`** — la tua lista di autori seguiti.
- **`seen_books.json`** — ID dei volumi gia notificati (evita doppioni).
- **`initialized_authors.json`** — autori gia "inizializzati", così aggiungere un
  nuovo autore non ti sommerge di notifiche del suo catalogo storico.
- **`check.py`** — lo script di polling (solo standard library Python, zero dipendenze).
- **`.github/workflows/check.yml`** — la GitHub Action schedulata.

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
git remote add origin git@github.com:<tuo-utente>/book-radar.git
git branch -M main
git push -u origin main
```

> Verifica che `.env` **non** sia tra i file committati (`git status` non deve
> mostrarlo). È protetto da `.gitignore`.

### 5. Configura i GitHub Secrets

Nella repo su GitHub: **Settings → Secrets and variables → Actions → New repository secret**.
Aggiungi:

| Nome | Valore |
|------|--------|
| `TELEGRAM_BOT_TOKEN` | il token di BotFather |
| `TELEGRAM_CHAT_ID` | il tuo chat id |
| `GOOGLE_BOOKS_API_KEY` | *(opzionale)* solo se usi una API key |

### 6. Prima run manuale (senza aspettare il cron)

1. Vai nella tab **Actions** della repo.
2. Seleziona il workflow **Book Radar** → **Run workflow**.
3. Controlla i log dello step "Esegui il controllo nuove uscite": per ogni autore
   vedrai quanti volumi trovati, quanti dopo il filtro, quante novità.
4. Alla **prima** run gli autori vengono inizializzati (nessuna notifica) e lo step
   di commit aggiorna `seen_books.json` / `initialized_authors.json`.

### 7. Attiva lo schedule quotidiano

Solo **dopo** una run manuale andata a buon fine: apri
`.github/workflows/check.yml`, togli il `#` davanti al blocco `schedule:` /
`- cron:` e committa. Da quel momento gira ogni giorno alle 08:00 UTC.

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

Variabile d'ambiente alternativa al flag: `BOOK_RADAR_DRY_RUN=1 python3 check.py`.

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
