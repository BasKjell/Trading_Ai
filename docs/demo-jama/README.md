# Demo-Jama für Trading_Ai

Status: lokale Entwicklungsdemo. [Implementierungsauftrag](../DEMO_JAMA_CODEX.md) · [Kontrollmatrix](KONTROLLMATRIX.md) · [Historischer Quellenabgleich](NACHPFLEGE.md)

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
`python -m pip install -e .` in die Demo-Umgebung installieren. Die Anbindung an den lokalen Codex-Client erfolgt vor der ersten Betriebsfreigabe.
Der Server benötigt keinen GitHub-, Jama-, Broker- oder Modell-Login.

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
- `decide` erfasst eine lokale, **unverifizierte Demo-Entscheidung** für `baseline`, `head`,
  `target`, Testlauf und Review jeweils mit ID und Revision, `stage` und `reason`. Für Release
  oder Betrieb wird zusätzlich der `artifact_digest` festgehalten. Eine lokale Entscheidung bleibt
  ein Demo-Datensatz. Die tatsächliche Freigabe auf GitHub und die Betriebsentscheidung liegen bei Bas.
- `backup PFAD` erstellt eine exklusive Kopie. Wiederherstellung im Test als separate Datei öffnen,
  mit `verify` validieren und erst nach menschlicher Prüfung als Arbeitsdatei verwenden.

## Integrationsentscheidung mit verifizierten GitHub-Nachweisen

```bash
python -m localtrader.demo_jama --db "$db" github-decide "$jama_dir/request.json" \
  --policy "$jama_dir/policy.json"
```

Der Aufruf liest GitHub mit dem lokal angemeldeten `gh` des Betreibers. Die Betreiber-CLI hält
Policy, Register und Zugangsdaten außerhalb der Agenten-Checkouts. Sie braucht nur Lesezugriff
auf PRs, Repository-Inhalte und Actions-Artefakte. MCP und die Webansicht erhalten ausschließlich
einen exportierten Stand. Der Aufruf schreibt eine Integrationsempfehlung mit Nachweisen in das
Register. Der Merge und die Betriebsfreigabe werden weiterhin separat durch Menschen ausgeführt.

**Antrag.** `request.json` enthält `pr` und `ci_run` als numerische IDs sowie `baseline`, `head`,
`target` und `reason`. Jede Requirement-Version der Baseline braucht mindestens einen
Abnahmetest. Die Risikoklasse wird aus der Baseline und der privaten Policy ermittelt.
Eine U-Einstufung gilt nur für einen dort ausdrücklich eingetragenen PR-Head.

**Vertrauensanker.** Der Betreiber erstellt die private Policy in einem eigenen Verzeichnis
mit Modus 0700. Die Datei hat Modus 0600. Sie enthält:

- `target` und die GitHub-Werte `workflow_path` und `workflow_id` für den zugelassenen PR-CI-Lauf.
- `protected_blobs` als Abbildung von Repo-Pfaden auf die geprüften Git-Blob-SHAs. Workflow,
  Makefile, Dev-Abhängigkeiten und Pytest-Konfiguration sind Pflicht. Weitere Test- und
  Hilfsdateien mit Einfluss auf die Abnahme müssen in die Schutzliste aufgenommen werden.
- `cases` als Zuordnung von `TC-ID@Revision` zu `file` und einem eindeutigen
  `junit`-Identifier `classname::name`. Die erwarteten Tests stammen aus dem separaten
  QA-Workflow. Änderungen an geschützten Dateien werden durch den Nachweischeck gesperrt
  und separat geprüft und als neue Policy aufgenommen.
- `review_families` als Zuordnung numerischer GitHub-Konto-IDs (JSON-Stringschlüssel) zu
  verifizierten Rollenfamilien, `owner_id` als numerische Konto-ID von Bas und optional
  `uncritical_pr_heads` als Zuordnung von PR-Nummer zu geprüftem Head. Ein Konto darf nur
  einer tatsächlich getrennten Identität zugeordnet sein. Reviews verschiedener Agenten
  über dasselbe GitHub-Konto reichen für die automatische Verifikation nicht aus.

**GitHub-Review.** Ein formales `APPROVED` enthält als vollständigen Body ein JSON-Objekt
mit `baseline`, `head`, `target`, `ci_run`, `base_sha`, `run_attempt`, `risk` und
`purpose: "independent_review"`. Für K gibt Bas zusätzlich ein formales Review mit
identischer Bindung und `purpose: "final_integration"` ab. Maßgeblich sind die von GitHub
authentifizierte Konto-ID, der Review-Commit und der aktuelle Review-Status. Änderungen
am Head, am Ziel, an der geprüften Basis oder am CI-Versuch erfordern neue Nachweise.

**CI.** Der Prüfer verlangt den erfolgreichen PR-CI-Lauf am aktuellen Head und an der
aktuellen Basis. Die geschützten Dateien werden an beiden Ständen anhand ihrer Blob-SHAs
geprüft. Der Prüfer lädt das JUnit-Artefakt, vergleicht den GitHub-Artefakt-Digest und
verlangt für jeden gebundenen Test einen Pass. Fehlende, übersprungene und fehlerhafte
Tests blockieren. Geschützte CI-Prozesse und getrennte Reviews bleiben Voraussetzung.
Die Demo kann die Vertrauensgrenze des laufenden CI-Systems nicht selbst attestieren.

Die lokale Audit-Hashkette erkennt Inkonsistenzen. Ein Betreiber mit vollständigen
OS-Rechten kann DB und Audit gemeinsam ersetzen. Für produktive Nachweise sind
externes Audit, Rollenvalidierung, Schutz der QA-Ausführung, Aufbewahrung und ein
dokumentierter Restore-Prozess festzulegen.


## Nachpflege

Stichtag 19.11.2026. GitHub lieferte in der vollständigen ersten Liste bei `state=all` und
`per_page=100` 52 Einträge, davon 22 Issues und 30 Pull Requests. Für die Liste gab es keine weitere
Seite. Der Seed enthält für jedes der 22 Issues eine Zuordnung. 88 Einträge leiten sich aus Issues
ab. 259 zusätzliche Kandidaten stammen aus Spec, AGENTS und ADRs. Scope, Duplikate, ergänzende
Entscheidungen aus Kommentaren und PRs sowie historische Tests sind fachlich nachzuprüfen.
Der [historische Quellenabgleich](NACHPFLEGE.md) erfasst die dazu vorhandenen Referenzen.
Keine historische Testausführung oder Abnahme wurde allein aus geschlossenem Issue oder Merge abgeleitet.
Alle importierten Requirements beginnen als `review_needed`. Aus den Kandidaten entsteht die
erste verbindliche Baseline erst nach fachlicher Prüfung durch Bas.

Für spätere Abgleiche die GitHub-Liste ab Seite 1 bis zum letzten Link abrufen, PR-Einträge
ausschließen und mit den Nummern und Revisionen im Katalog vergleichen. Neue Quellen, geänderte
Aussagen und Überschneidungen als neue Entwürfe erfassen, vor dem Import `dry-run` ausführen und
die Lücken unter `gaps` auswerten. Der Import und die Demo schreiben nicht auf GitHub.
