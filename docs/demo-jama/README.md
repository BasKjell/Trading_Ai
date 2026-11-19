# Demo-Jama für Trading_Ai

Status: lokale Entwicklungsdemo. [Implementierungsauftrag](../DEMO_JAMA_CODEX.md) · [Kontrollmatrix](KONTROLLMATRIX.md)

## Start

Python 3.12 oder neuer. Daten außerhalb des Checkouts ablegen. Die Webansicht und MCP lesen einen
vom Betreiber freigegebenen Export. Die Schreib-CLI läuft ausschließlich unter dem privaten
Betreiberkonto. Für Agenten separate Benutzer oder Container mit ausschließlich dem
schreibgeschützt eingebundenen Export verwenden.

```bash
python3.12 -m venv .venv-demo-jama
. .venv-demo-jama/bin/activate
python -m pip install -r requirements-demo-jama.txt
jama_dir="$HOME/.trading-ai/demo-jama"
mkdir -m 700 -p "$jama_dir"
umask 077
db="$jama_dir/register.sqlite"
python -m localtrader.demo_jama --db "$db" dry-run docs/demo-jama/catalog.json
python -m localtrader.demo_jama --db "$db" import docs/demo-jama/catalog.json
python -m localtrader.demo_jama --db "$db" verify
python -m localtrader.demo_jama --db "$db" export > "$jama_dir/snapshot.json"
python -m localtrader.demo_jama ui "$jama_dir/snapshot.json"
```

Die lokale Ansicht läuft auf [127.0.0.1:8765](http://127.0.0.1:8765). Sie ist lesend und für lokale
Prozesse sichtbar. Für einen isolierten MCP-Client den Export separat bereitstellen und genau dessen
Pfad eintragen. Export vor Weitergabe prüfen. Beim Austausch des Exports den MCP-Prozess neu starten.
Keine Zugangsdaten an Clients vererben. MCP nutzt stdio. Beispiel für Codex `config.toml`:

```toml
[mcp_servers.demo_jama]
command = "/ABSOLUTER/PFAD/.venv-demo-jama/bin/python"
args = ["-m", "localtrader.demo_jama", "mcp", "/ABSOLUTER/PFAD/snapshot.json"]
```

Der MCP-Client muss das Projekt im Python-Importpfad haben. Alternativ das Projekt vorab per
`python -m pip install -e .` in die Demo-Umgebung installieren. Die Anbindung am Mac kommt nach
dem unabhängigen Review. Der Server benötigt keinen GitHub-, Jama-, Broker- oder Modell-Login.

## Rollen und lokale Nachweise

Requirements- und QA-Agent legen getrennte JSON-Entwürfe vor. Der Betreiber prüft Herkunft und
Inhalt und übernimmt die Entwürfe über `requirement` und `test-case`. Der Implementierungs- und
Review-Agent erhalten den Export lesend. Schreibrechte werden über die Prozess-/OS-Grenze
durchgesetzt. Diese Version startet keine Agenten und reicht keine Agentenberechtigungen durch.

Die CLI-Aufrufe für Entwürfe, Baseline, Evidenz und Entscheidung verwenden
`python -m localtrader.demo_jama --db "$db" BEFEHL DATEI.json`.

- `requirement` akzeptiert das Schema eines Eintrags im [Katalog](catalog.json).
- `test-case` akzeptiert `id`, `requirement_id`, `requirement_revision`, `preconditions`, `test_data`,
  `steps`, `expected`, `test_type` und `procedure`.
- `baseline` fixiert eine Liste aus `{"id":"REQ-…","revision":1}`-Referenzen. Eine Baseline mit
  Abnahmetests muss dieselben Requirement-Versionen enthalten, die die Fälle prüfen.
- `test-run` erfasst `baseline`, `head` (Commit-SHA), `target`, `result`, `ci_run`,
  `environment`, `timestamp`, `evidence_url` und `cases`. Ein Fall enthält `id`, `revision`,
  `result`, `actual` und bei Ausfall oder Überspringen `reason`. Soll steht in der fixierten
  Testfallversion.
- `review` erfasst denselben Stand mit `author_family`, `reviewer_family`, `evidence_id`,
  `evidence_revision`, `reference`, `blocking_count` und `result`.
- `decide` erfasst die ausdrücklich vom Betreiber getroffene Entscheidung für `baseline`, `head`,
  `target`, Testlauf und Review jeweils mit ID und Revision, `stage` und `reason`. Für Release
  oder Betrieb wird zusätzlich der `artifact_digest` festgehalten. Eine lokale Entscheidung bleibt
  ein Demo-Datensatz. Die tatsächliche Freigabe auf GitHub und die Betriebsentscheidung liegen bei Bas.
- `backup PFAD` erstellt eine exklusive Kopie. Wiederherstellung im Test als separate Datei öffnen,
  mit `verify` validieren und erst nach menschlicher Prüfung als Arbeitsdatei verwenden.

Der Betreiber verantwortet den Abgleich von CI-Herkunft, tatsächlichem PR-Head und Review-Identität.
Die Demo prüft die Übereinstimmung erfasster Nachweise. Eine serverseitige GitHub-/CI-Verifikation,
geschützte Review-Identitäten und ein unabhängiges Auditlog bleiben offen. Der Betreiber mit
vollen OS-Rechten kann DB und Audit gemeinsam ersetzen. Die Hashkette erkennt Inkonsistenzen,
sie erbringt keinen externen Zeit- oder Identitätsnachweis. Die Ablage und Backups bleiben privat.
Die Aufbewahrungsdauer und das Löschen prüft und entscheidet der Betreiber vor Nutzung mit Echtdaten.

## Nachpflege

Stichtag 19.11.2026. GitHub lieferte in der vollständigen ersten Liste bei `state=all` und
`per_page=100` 52 Einträge, davon 22 Issues und 30 Pull Requests. Für die Liste gab es keine weitere
Seite. Der Seed enthält für jedes der 22 Issues eine Zuordnung. 88 Einträge leiten sich aus Issues
ab. 259 zusätzliche Kandidaten stammen aus Spec, AGENTS und ADRs. Scope, Duplikate, ergänzende
Entscheidungen aus Kommentaren und PRs sowie historische Tests sind fachlich nachzuprüfen.
Keine historische Testausführung oder Abnahme wurde allein aus geschlossenem Issue oder Merge abgeleitet.
Alle importierten Requirements beginnen als `review_needed`. Aus den Kandidaten entsteht die
erste verbindliche Baseline erst nach fachlicher Prüfung durch Bas.

Für spätere Abgleiche die GitHub-Liste ab Seite 1 bis zum letzten Link abrufen, PR-Einträge
ausschließen und mit den Nummern und Revisionen im Katalog vergleichen. Neue Quellen, geänderte
Aussagen und Überschneidungen als neue Entwürfe erfassen, vor dem Import `dry-run` ausführen und
die Lücken unter `gaps` auswerten. Der Import und die Demo schreiben nicht auf GitHub.
