# Runbook RunPod — prerequisiti e procedura (da fare PRIMA di accendere)

Questo documento è ciò che mancava al primo bring-up: l'elenco
**completo e dichiarato in anticipo** di tutto il lavoro manuale, le
chiavi, i permessi e i costi necessari, in un unico posto, da eseguire
in ordine. Nessuno step va scoperto a pod acceso (vedi il fallimento in
`docs/retrospettiva_m6_bringup.md`). Se uno step della **Fase 0** non è
verde, **non si accende il pod**.

Legenda: ⛔ bloccante · ⚠️ consigliato · 💰 impatto costo.

---

## Fase 0 — prerequisiti, a costo zero (NIENTE pod acceso)

Tutto qui si fa senza affittare GPU. Finché non è tutto spuntato, il pod
resta spento.

### 0.1 — Credenziali da procurarsi (una volta)

| Credenziale | ⛔/⚠️ | Dove | Note |
|---|---|---|---|
| `OPENAI_API_KEY` | ⛔ | platform.openai.com/api-keys → *Create secret key* | serve credito attivo (Billing). Usata dal nodo ChatOpenAI del chatflow L2. **Mai in chat — solo env/Secret.** |
| `ANTHROPIC_API_KEY` **oppure** `claude login` nel pod | ⛔ | console.anthropic.com/settings/keys | serve solo se `claude` gira *dentro* il pod; se guidi da una sessione locale già autenticata, non serve nel pod |
| `GITHUB_TOKEN` (fine-grained, repo `caliper-cad`, **Contents: R/W**) | ⛔ per push/harvest/bus | github.com/settings/personal-access-tokens | senza, il pod non può fare push né attivare il bus di supervisione |
| Chiave SSH pubblica | ⚠️ | `ssh-keygen` locale → RunPod *Account → Settings → SSH Public Keys* | abilita `sshd` sul pod automaticamente |

Regola ferrea: le chiavi si mettono **solo** come Environment Variable /
Secret del template (nome=valore), mai incollate in una conversazione.
Se una finisce in chat → revocala e rigenerala.

### 0.2 — Gate tecnico: l'immagine deve BOOTARE, non solo compilare

- Workflow `.github/workflows/publish-images.yml` verde = immagine
  **compilata** e su GHCR. Non basta.
- Workflow `.github/workflows/pod-boot-smoke.yml` verde = immagine
  **avviata** su runner CPU e validata (`start.sh --selftest`). **Questo**
  è il via libera. Se è rosso, si corregge prima di affittare.
- Package GHCR `caliper-pod` **pubblico** (o credenziali registry nel
  template), altrimenti RunPod non fa pull.

### 0.3 — Parametri del template (compilare tutti prima di creare)

| Campo | Valore |
|---|---|
| Container Image | `ghcr.io/danielesalpietro/caliper-pod:git-<sha-ultimo-boot-verde>` |
| Docker Command | *(vuoto — CMD già nell'immagine)* |
| GPU | 1× RTX 3090 24GB, Secure Cloud, DC EU con Network Volume |
| Container Disk | 60 GB |
| Volume | Network Volume `caliper-artifacts` (100 GB) → mount `/workspace` |
| HTTP Ports | 3000, 3010, 6333, 8000, 8500, 8600, 8700, 11434 |
| TCP Ports | 22 |
| Environment Variables | ⛔ `OPENAI_API_KEY`, `GITHUB_TOKEN` · ⚠️ `ANTHROPIC_API_KEY` (solo se `claude` gira nel pod), `PUBLIC_KEY` (per sshd), `CALIPER_GIT_REF` · `FLOWISE_USERNAME`/`FLOWISE_PASSWORD` **non più necessarie**: il bootstrap automatico (`flowise_bootstrap.py`, validato dal vivo) genera la password, crea account/API key/credential e le persiste in `/workspace/.caliper_env` — **ognuna di quelle passate va col valore** |

Dettaglio campi e "problemi noti" in `ops/runpod/README.md`.

---

## Fase 1 — accensione (💰 qui inizia il costo GPU)

1. Crea il Network Volume `caliper-artifacts` (persiste tra pod; ~7 $/mese).
2. Crea il pod dal template della Fase 0.3.
3. Guarda il log di avvio: `start.sh` stampa `[ok]`/`[MANCA]` per ogni
   variabile e `SELFTEST`/`claude`/`sshd`/`agent_bus`. Se vedi `[MANCA]`
   su una bloccante → correggi l'env e riavvia, non proseguire.
4. Attendi che i modelli Ollama si scarichino (primo avvio, poi
   persistono sul volume).

## Fase 2 — esecuzione (M6)

- La sessione esecutrice è `claude` nel pod (o la tua sessione locale via
  SSH). Handoff completo: issue #18 e `docs/handoff_m6.md`.
- Se `GITHUB_TOKEN` c'è, il bus col supervisore parte da solo: da lì il
  supervisore co-guida e verifica via GitHub.
- `harvest.sh <tag>` dopo **ogni** TC-E2E — gli output finiscono sul
  volume, e con `--push` anche su git.

## Fase 3 — spegnimento (💰 ferma il costo GPU)

1. `bash ops/runpod/harvest.sh <tag> --push` → deve uscire **verde**.
2. Verifica su git che `runs/<data>-<tag>/` sia arrivato.
3. **Solo allora** stoppa il pod dal pannello. Il volume `/workspace`
   (lavoro, modelli, log, host key SSH) persiste: un pod successivo
   riparte in minuti dallo stesso stato.

Regola di costo: davanti a un blocco di configurazione su un pod acceso,
la prima mossa è **harvest + stop**, non il debug a ore fatturate.

---

## Stima costi (ordine di grandezza)

- RTX 3090 Secure Cloud: ~0,2–0,4 $/h.
- Network Volume 100 GB: ~7 $/mese, indipendente dall'accensione.
- Una sessione M6 completa: pochi $ di GPU se l'ambiente è pronto
  (Fase 0 verde). Il costo esplode solo se si debugga il setup a pod
  acceso — che questo runbook esiste per evitare.
