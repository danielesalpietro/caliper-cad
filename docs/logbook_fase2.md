# Logbook — M2: Controlli geometrici deterministici, validati su geometrie note

Vedi [`logbook.md`](logbook.md) per il quadro generale. Dipende da M1
(protocollo calibri + modulo di interferenza) — vedi
[`logbook_fase1.md`](logbook_fase1.md).

## Obiettivo (rivisto rispetto alla proposta originale)

Proposta originale: script interni al motore fisico per collisioni
cinematiche complesse su TC1 e TC2, con forza/momento torcente applicati;
milestone: batch dei tre test case su geometrie note per validare la
precisione millimetrica del motore.

Adottato: stesso obiettivo di validazione, implementazione senza motore
fisico — per ciascun test case, il controllo diventa una query geometrica
esatta invece di una simulazione dinamica.

## TC1 — Accoppiamento albero-mozzo (clearance fit)

**Proposta originale:** spingere un perno nel foro con forza definita
lungo Z, rilevare compenetrazione dei collider prima di fine corsa.

**Rivisto:** interferenza statica **e** su sweep (perno spostato lungo Z
in step discreti, o volume spazzato calcolato analiticamente) contro un
calibro pin GO (diametro minimo) e uno NO-GO (diametro massimo). Un
clearance fit per definizione non deve avere interferenza in nessuna
posizione lungo la corsa — non serve applicare una forza: se serve forza
per inserirlo, non è più un clearance fit, è già un fallimento rilevabile
da interferenza statica. **Nota per il futuro:** un vero test di press-fit
(interferenza intenzionale, deformazione elastica, forza di inserimento)
è un problema diverso — richiede un solver dedicato (FEA), da tenere
esplicitamente fuori da questo test case finché non è il suo turno.

## TC2 — Filettatura metrica ISO

**Proposta originale:** simulazione di avvitamento con momento torcente
applicato; fallimento se la geometria si incastra o presenta sovrapposizioni
poligonali.

**Rivisto:** calibro ad anello GO/NO-GO filettato, sweep lungo il percorso
elicoidale reale (stesso passo/angolo del preset `thread` in
`presets.json`, ISO 68-1, 60°) — è la stessa identica metodologia di
collaudo filettature nella metrologia reale (calibri Go/No-Go a vite), non
un'invenzione. Il calibro GO deve poter percorrere l'intera elica senza
interferenza; il calibro NO-GO deve interferire. Elimina la sensibilità
a timestep/damping di un solver fisico iterativo, che su un'elica sottile
può dare sia falsi blocchi (jamming numerico) sia falsi pass (tunneling
attraverso una cresta sottile a step temporali larghi).

## TC3 — Giunto a scatto (snap-fit)

**Proposta originale:** array di raycast dalla base per misurare spessore
del dente di ritenzione e gap, confrontati con le quote teoriche.

**Rivisto:** stessa idea concettuale ("calibro virtuale" posizionato in
punti di misura noti), implementata con `BRepExtrema_DistShapeShape`
(distanza minima esatta tra facce/edge B-Rep) invece di raycast su mesh in
un motore fisico. I punti di misura (facce/edge di riferimento) vanno
dichiarati nella specifica L2.5 — è un'estensione dello schema, non solo
del codice del verificatore.

## Milestone (criterio di accettazione, mantenuto)

Esecuzione batch automatica dei tre test case su geometrie **note,
disegnate convenzionalmente, non generate da un LLM** — stesso principio
già usato per il Livello 3 esistente ("verificato sui due output reali del
Chatflow L2", v12). Serve prima dimostrare che il verificatore giudica
correttamente casi di controllo, prima di fidarsi che giudichi bene output
AI.

**Aggiunta:** i modelli CAD di riferimento per TC1–TC3 sono lo stesso
lavoro già segnato come TODO aperto per il Livello 6 ("Modelli CAD di
riferimento... non ancora fatto", nell'architettura) — costruirli una
volta e riusarli per entrambi gli scopi, non duplicare il lavoro.

## Stato

- [ ] TC1: calibri pin GO/NO-GO modellati, controllo interferenza
      statico+sweep implementato e verificato su geometria nota
- [ ] TC2: calibro ad anello GO/NO-GO filettato modellato, sweep elicoidale
      implementato e verificato su geometria nota
- [ ] TC3: punti di misura dichiarati nello schema L2.5, controllo distanza
      minima implementato e verificato su geometria nota
- [ ] Batch dei tre TC eseguito e documentato con risultati numerici
- [ ] Modelli CAD di riferimento riusati per popolare il campo diagnostico
      del Livello 6 (collegamento esplicito col bootstrap retroattivo)
