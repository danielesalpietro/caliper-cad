# [M7] flowiseai/flowise:3.1.4 crasha al boot su QUALSIASI installazione
# Docker con la configurazione di default (DATABASE_TYPE non impostata ->
# "sqlite"): l'immagine spedisce connect-sqlite3@0.9.17, la cui API
# richiede che l'opzione `db` sia gia' un'istanza sqlite3.Database aperta
# (e' diventata una peerDependency in quella major, vedi
# node_modules/connect-sqlite3/package.json dentro l'immagine), mentre il
# codice enterprise di Flowise che inizializza le sessioni JWT
# (dist/enterprise/middleware/passport/SessionPersistance.js) gli passa
# ancora una stringa nuda (`db: 'database.sqlite'`), comportamento della
# vecchia API di connect-sqlite3 (apriva il file da se'). Risultato:
# "TypeError: this.db.exec is not a function" ad ogni avvio — mai visto
# nei run M6 perche' li' Flowise girava nativo via npm (non
# nell'immagine Docker ufficiale). Primo bug di topologia mai esercitato
# in un `docker build` di questo progetto (M7, C9).
#
# Fix minimale, sul componente rotto (connect-sqlite3), non sul chiamante
# (Flowise): se `options.db` arriva come stringa, apri tu stesso il file
# con `sqlite3.Database` — sqlite3 e' gia' presente come dipendenza
# sorella nello stesso node_modules (Flowise lo usa per il proprio
# datastore TypeORM), quindi `require('sqlite3')` risolve senza aggiungere
# nulla. Nessun'altra funzione del file tocca `db` in modo incompatibile
# con una vera istanza sqlite3.Database (get/run/all/exec — verificato
# leggendo l'intero file, 193 righe).
FROM flowiseai/flowise:3.1.4

RUN node -e " \
  const fs = require('fs'); \
  const p = '/usr/local/lib/node_modules/flowise/node_modules/connect-sqlite3/lib/connect-sqlite3.js'; \
  let s = fs.readFileSync(p, 'utf8'); \
  const needle = 'this.db = options.db;'; \
  if (!s.includes(needle)) { throw new Error('connect-sqlite3: riga da patchare non trovata, la sorgente e cambiata rispetto a 0.9.17 - rivedere il fix'); } \
  const patched = \"var sqlite3 = require('sqlite3'); var path = require('path'); this.db = (typeof options.db === 'string') ? new sqlite3.Database(path.join(options.dir || '.', options.db)) : options.db;\"; \
  s = s.split(needle).join(patched); \
  fs.writeFileSync(p, s); \
  console.log('connect-sqlite3 patchato: db stringa ora apre sqlite3.Database internamente'); \
"
