# AGENTS.md – verbindliche Arbeitsregeln

Gilt für alle Agenten (Claude, Codex, andere) und Menschen, die an diesem Repository arbeiten.
Ausführliche Anforderungen: [`docs/PROJECT_SPEC.md`](docs/PROJECT_SPEC.md). Bei Widerspruch gelten
`AGENTS.md` + `PROJECT_SPEC.md` + ausdrückliche Benutzerentscheidungen. Konflikte dokumentieren und
klären, nicht stillschweigend interpretieren.

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
- Push auf den eigenen Branch, dann PR (Vorlage `.github/PULL_REQUEST_TEMPLATE.md`). **Kein Merge nach
  `main` durch Agenten, kein Force-Push, keine Historienänderung.**
- Zentrale Datentypen, Schemas, Konfiguration, Abhängigkeiten, öffentliche Schnittstellen nur nach
  dokumentierter Abstimmung ändern. Kein beiläufiges Refactoring fremder Komponenten.
- Isolation: eigene temporäre Dateien, Testdatenbanken, Laufzeitverzeichnisse, Ports und
  Docker-Compose-Projektnamen je Worktree. Keine gemeinsam beschreibbaren Registries/Paper-Konten.
- Geheimnisse nur in `.env` (Vorlage `.env.example`). Nie in Git, Logs, Handoffs, Issues oder Commits.
- Dokumentationsaussagen kennzeichnen: **geplant / implementiert / getestet / integriert / blockiert**.
  „Integriert" heißt: in `main` gemergt. Geplantes nie als vorhanden beschreiben.

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
