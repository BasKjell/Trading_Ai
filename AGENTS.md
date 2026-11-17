# AGENTS.md – verbindliche Arbeitsregeln

Gilt für alle Agenten (Claude, Codex, andere) und Menschen, die an diesem Repository arbeiten.
Ausführliche Anforderungen: [`docs/PROJECT_SPEC.md`](docs/PROJECT_SPEC.md). Bei Widerspruch gelten
`AGENTS.md` + `PROJECT_SPEC.md` + ausdrückliche Benutzerentscheidungen. Konflikte dokumentieren und
klären, nicht stillschweigend interpretieren.

## 0. Erster Schritt jeder Sitzung: offene Reviews

Vor jeder neuen Aufgabe:
1. Offene Pull Requests mit Label `needs-independent-review` oder `review` abrufen
   (`gh api repos/BasKjell/Trading_Ai/pulls`).
2. Eigene PRs überspringen: PRs, deren Feld „Agent" in der Beschreibung oder deren Co-Authored-By-Zeilen
   dieselbe Agentenart/Modellfamilie nennen (z. B. Claude reviewt keine Claude-PRs, Codex keine Codex-PRs).
3. Fremde PRs reviewen: Code und Tests lesen, `make check` im eigenen Worktree ausführen, Look-ahead,
   Kosten, Risikogrenzen, Orderlogik und Validierung gezielt prüfen. Ergebnis als PR-Review mit klarer
   Empfehlung (freigeben / Änderungen nötig) und gefundenen Fehlern; Fehler in
   `docs/agents/MODEL_EVALUATION.md` beim verursachenden Eintrag nachtragen.
4. Bei Freigabe als Integrationsagent mergen (Abschnitt 2a). Sonst Label `review` belassen und Befunde
   im PR dokumentieren.
5. Dem Benutzer am Sitzungsende melden, welche PRs reviewt, gemergt oder zurückgewiesen wurden.

## 1. Einstieg (vor jeder Änderung)

1. Diese Datei, dann `docs/PROJECT_SPEC.md`, `docs/ARCHITECTURE.md`, `docs/STATUS.md` lesen.
2. Tatsächlichen Code, `git status`, Remote-Branches prüfen (`git fetch --all`).
3. Offene GitHub Issues und Pull Requests prüfen (REST: `gh api repos/BasKjell/Trading_Ai/issues`).
4. Eine freie, abgegrenzte Aufgabe wählen (Label `backlog`). Vor **und** direkt nach dem Beanspruchen
   prüfen, ob ein anderer Agent sie übernommen hat. Der zuerst serverseitig bestätigte Claim-Kommentar
   gewinnt. Nie nur wegen vergangener Zeit übernehmen – bei Unklarheit Benutzer fragen.
5. Übernahme im Issue dokumentieren (Kommentar `claimed by <agent>/<modell>`, Label `claimed`).
6. Eigenen Branch und Worktree anlegen: `scripts/start_task.sh T-xxx kurzbeschreibung`
   (Branch `agent/T-xxx-kurzbeschreibung`). Nie mit mehreren Agenten im selben Checkout arbeiten.
7. `docs/handoffs/T-xxx.md` und relevante `docs/decisions/` lesen.
8. **Modell und Denkstufe prüfen** (Abschnitt 3).

## 2. Arbeiten

- Kleine, fachlich zusammenhängende, geprüfte Commits; Präfixe `feat|fix|test|docs|refactor|chore`.
- Vor jedem Commit: `make check` (Format, Lint, Typen, Tests, Secret-Scan, Konfiguration). Nur
  tatsächlich ausgeführte Ergebnisse berichten; übersprungene Prüfungen ausdrücklich nennen.
- Jede ausführbare Änderung mit aussagekräftigen Tests; jede Fehlerbehebung mit Regressionstest.
  Erwartete Testergebnisse nie anpassen, nur damit fehlerhafter Code besteht.
- Änderungsnotiz in `changelog.d/T-xxx.md` (nicht direkt in `CHANGELOG.md`; das wird bei der
  Integration zusammengeführt). Architekturentscheidungen unter `docs/decisions/`.
- Handoff `docs/handoffs/T-xxx.md` laufend und spätestens vor jeder Pause pflegen (Vorlage
  `docs/handoffs/TEMPLATE.md`). WIP-Commits auf dem eigenen Branch sind erlaubt, klar als `WIP`
  markiert, nie als fertig gemeldet.
- Push auf den eigenen Branch, dann PR (Vorlage `.github/PULL_REQUEST_TEMPLATE.md`). Feld „Agent" mit
  Agentenart und Modell ausfüllen. **Kein Force-Push, keine Historienänderung, nie eigene PRs mergen.**
- Zentrale Datentypen, Schemas, Konfiguration, Abhängigkeiten, öffentliche Schnittstellen nur nach
  dokumentierter Abstimmung ändern. Kein beiläufiges Refactoring fremder Komponenten.
- Isolation: eigene temporäre Dateien, Testdatenbanken, Laufzeitverzeichnisse, Ports und
  Docker-Compose-Projektnamen je Worktree. Keine gemeinsam beschreibbaren Registries/Paper-Konten.
- Geheimnisse nur in `.env` (Vorlage `.env.example`). Nie in Git, Logs, Handoffs, Issues oder Commits.
- Dokumentationsaussagen kennzeichnen: **geplant / implementiert / getestet / integriert / blockiert**.
  „Integriert" heißt: in `main` gemergt. Geplantes nie als vorhanden beschreiben.

## 2a. Integration (Merge nach `main`)

Benutzerentscheidung vom 2026-11-17: Ein Agent darf als Integrationsagent nach `main` mergen, wenn **alle**
Bedingungen erfüllt sind:
1. Der PR stammt von einer anderen Agentenart/Modellfamilie oder vom Benutzer (nie eigene PRs).
2. Ein unabhängiges Review mit Empfehlung „freigeben" liegt vor (bei Risiko, Orderausführung,
   Validierung zwingend; sonst durch den mergenden Agenten selbst).
3. Der PR enthält den aktuellen Stand von `main`; Konflikte sind fachlich gelöst (nie pauschal
   ours/theirs); `make check` lokal und die CI des PRs sind grün.
4. Keine offenen Blockaden, keine WIP-Commits, keine ungeklärten Benutzerentscheidungen im PR.
5. Merge per Merge-Commit (kein Squash/Rebase der fremden Historie), kein Force-Push.

Danach im selben Integrationsschritt: `changelog.d/T-xxx.md` in `CHANGELOG.md` übernehmen und löschen,
`docs/STATUS.md` auf „integriert" setzen, Issue auf `done`, abhängige PRs auf `main` umstellen.
Integration in `main` ist **keine Livefreigabe** (Spec §18).

## 3. Modellwahl, Denkstufe und Eskalation

- **Vor jeder Aufgabe prüfen, ob gewähltes Modell und Denkstufe angemessen sind.** Ist ein Wechsel
  sinnvoll, empfohlenes Modell, Denkstufe und den konkreten Grund nennen.
- Kann der Agent die Einstellung nicht technisch ändern, **keinen Wechsel behaupten**. Stattdessen eine
  kurze Übergabe (`docs/handoffs/T-xxx.md`) erstellen, damit der Benutzer manuell wechseln kann.
- Ausgangspunkt für klar abgegrenzte Implementierungen: **Medium**.
- **Höhere Denkstufe** bei komplexen Abhängigkeiten, unklaren Anforderungen oder Änderungen mit hohen
  Fehlerfolgen (Risikosteuerung, Orderausführung, Buchhaltung, statistische Validierung, Live-Sperren).
- **Nach zwei erfolglosen Lösungsversuchen:** keine weiteren spekulativen Änderungen. Übergabe mit
  Fehlerreproduktion, Befunden und verworfenen Ansätzen erstellen und eine unabhängige Analyse durch ein
  stärkeres Modell empfehlen.
- Änderungen an **Risikosteuerung, Orderausführung und statistischer Validierung** benötigen vor der
  Integration ein **separates Review** durch einen anderen Agenten oder den Benutzer – unabhängig davon,
  welches Modell implementiert hat. PR-Label `needs-independent-review`.
- Modelle werden anhand **bestandener Abnahmekriterien, gefundener Fehler und Gesamtaufwand
  einschließlich Nacharbeit** beurteilt. Einträge in [`docs/agents/MODEL_EVALUATION.md`](docs/agents/MODEL_EVALUATION.md).
- Übereinstimmung mehrerer Modelle ist kein empirischer Nachweis.

## 4. Forschung

- Vor jedem Experiment `docs/research/EXP-xxxx.md` (Vorlage `docs/research/TEMPLATE.md`): Hypothese,
  Plausibilität, Unterschied zur bestehenden Methode, Daten und Verfügbarkeit, Vergleichsmodell,
  Parameterraum, Bewertungs-/Abbruchkriterien, Budget. Ohne Budget nur lokale Experimente.
- Ergebnisse als unveränderliche Datei im Experimentregister `experiments/EXP-xxxx.json`
  (`localtrader/experiment_registry.py`). Alle Varianten inkl. verworfener erfassen; die Gesamtzahl der
  Versuche aller Agenten (`python3 -m localtrader experiments`) bei der Bewertung berücksichtigen.
- Der finale unabhängige Testzeitraum wird zentral kontrolliert; Forschungsagenten werten ihn nicht aus.
- Kein kostenpflichtiger Dienst, kein Datenkauf, keine echte Order ohne ausdrückliche Freigabe.

## 5. Sicherheit

- Standardmodus ist Paper. Live ist technisch gesperrt (kein Live-Transport vorhanden). Commit, Push,
  Test oder trainiertes Modell sind **keine** Livefreigabe (Spec Abschnitt 18).
- Der Forschungsprozess darf Risikolimits nicht erhöhen, Gebühren nicht entfernen, Testdaten nicht
  umdeklarieren und Produktionscode nicht im laufenden Betrieb überschreiben.

## 6. Nützliche Befehle

```bash
make check                                  # alle Pflichtprüfungen
scripts/start_task.sh T-005 order-state     # Worktree + Branch für eine Aufgabe
make intraday && make intraday-stress       # Backtests auf DAX-Beispieldaten
python3 -m localtrader experiments          # Experimentregister und Gesamtzahl der Versuche
```
