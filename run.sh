#!/bin/bash
#
# Wrapper eseguito dal job launchd (vedi com.bookradar.check.plist).
# Gira sul Mac (IP italiano) cosi' Google Books restituisce le edizioni italiane.
#
# Flusso:
#   1. git pull   -> recupera eventuali modifiche di authors.json fatte dall'app iOS
#   2. check.py   -> polling + notifiche Telegram + aggiorna lo stato locale
#   3. git push   -> ripubblica seen_books.json/initialized_authors.json nel repo
#
# Tutto l'output (compresi gli errori) finisce in book-radar.log (gitignorato).

set -uo pipefail

REPO="/Users/matteopuccinelli/book-radar"
cd "$REPO" || exit 1

# launchd parte con un PATH minimale: aggiungo le posizioni tipiche di git/python.
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"

# Redirige tutto nel log con timestamp.
exec >> "$REPO/book-radar.log" 2>&1
echo ""
echo "===== $(date '+%Y-%m-%d %H:%M:%S') ====="

# 1. Allinea il repo (autostash per non perdere modifiche di stato non committate).
git pull --rebase --autostash --quiet || echo "[run.sh] Attenzione: git pull fallito, proseguo con la copia locale."

# 2. Polling vero e proprio.
python3 check.py
status=$?
if [ $status -ne 0 ]; then
    echo "[run.sh] check.py terminato con errore (exit $status)."
fi

# 3. Ripubblica lo stato. last_run.json/STATUS.md cambiano a ogni run (timestamp),
#    quindi normalmente c'e' sempre almeno un commit: e' il "battito" giornaliero
#    che conferma dal repo che lo script ha girato.
git add seen_books.json initialized_authors.json last_run.json STATUS.md
if [ -n "$(git status --porcelain seen_books.json initialized_authors.json last_run.json STATUS.md)" ]; then
    git commit -q -m "chore: aggiorna stato e timestamp ultimo controllo [skip ci]"
    git push -q && echo "[run.sh] Stato ripubblicato sul repo." || echo "[run.sh] git push fallito."
else
    echo "[run.sh] Nessuna modifica di stato da pubblicare."
fi
