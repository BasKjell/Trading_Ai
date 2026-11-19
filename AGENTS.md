# AGENTS.md – verbindliche Arbeitsregeln

Gilt für alle Agenten (Claude, Codex, andere) und Menschen, die an diesem Repository arbeiten.
Ausführliche Anforderungen: [`docs/PROJECT_SPEC.md`](docs/PROJECT_SPEC.md). Bei Widerspruch gelten
`AGENTS.md` + `PROJECT_SPEC.md` + ausdrückliche Benutzerentscheidungen. Konflikte dokumentieren und
klären, nicht stillschweigend interpretieren.

## 0. Erster Schritt jeder Sitzung: offene Reviews

Vor jeder neuen Aufgabe:
1. **Alle** offenen Pull Requests abrufen, samt Reviews, Review-Kommentaren und PR-Kommentaren
   (`gh api repos/BasKjell/Trading_Ai/pulls`, `…/pulls/<n>/reviews`, `…/pulls/<n>/comments`,
   `…/issues/<n>/comments`). Fehlt `gh` oder ist es nicht angemeldet, lesend über die REST-API
   (`curl https://api.github.com/repos/BasKjell/Trading_Ai/...`) und dem Benutzer melden, dass
   Schreibaktionen (Kommentare, Labels, Reviews, Push) nicht möglich sind.
2. **Eigene PRs** (gleiche Agentenart/Modellfamilie laut Feld „Agent" bzw. Co-Authored-By): nicht
   reviewen, aber auf Befunde anderer prüfen. Liegt ein Review mit „Änderungen nötig" oder ein
   unbeantworteter Befund vor, hat dessen Behebung Vorrang vor neuen Aufgaben: auf dem bestehenden
   Branch mit Regressionstests beheben, im PR auf jeden Befund antworten (behoben in `<SHA>` / begründet
   abgelehnt) und erneutes Review anfordern. Das ist Fehlerbehebung, kein Selbstreview. Erst wenn eigene
   PRs keine offenen Befunde haben, gilt §0 für sie als erledigt.
3. Fremde PRs je nach **Risikoklasse** vor dem Merge prüfen. Klasse K: vollständiges unabhängiges
   Review mit `make check`-Nachweis, Code- und Testprüfung sowie den für die Änderung relevanten
   Schutzgrenzen. Klasse U: ein separater KI-Agent prüft Einstufung, Änderung, Tests und CI im
   erforderlichen Umfang. Die Empfehlung und Befunde im PR dokumentieren.
4. Klasse K zur finalen Integrationsentscheidung an Bas geben. Klasse U darf ein separater
   Integrationsagent nach §2a manuell integrieren.
5. Dem Benutzer am Sitzungsende melden, welche PRs reviewt, gemergt, zurückgewiesen oder (eigene)
   nach Befunden korrigiert wurden.

**Review nach Risikoklasse** (Benutzerentscheidung 2026-11-18)

Jeder PR nennt seine Klasse (PR-Vorlage); der Integrationsagent prüft die Einstufung. Im Zweifel und bei
gemischten PRs gilt **K**. Ziel: Schutz dort, wo Fehler unumkehrbar sind (gesehene Ergebnisse,
verbrauchter Holdout, echte Orders), und weniger Runden für alles andere.

| Klasse | Umfang | Vorgehen |
|---|---|---|
| **K – kritisch** | Holdout und Datenverfügbarkeit, Kausalität/Engine, Orderausführung und Broker, Risikoregeln, Buchhaltung, Kosten- und Dividendenbuchung, statistische Validierung und Auswahl, Live-Sperren und Sicherheitsregeln (§5), Rechte automatischer Agenten. **Außerdem:** mögliche Offenlegung von Zugangsdaten, geschützten Daten oder Ergebnissen (Zugriffskontrollen, Artefakt- und Reportveröffentlichung, auch lesende Dashboards mit Holdout- oder Kontodaten); dauerhafter Zustand (Migration, Restore, Retention oder Löschung von Order-, Audit-, Versuchs- und Holdoutjournalen sowie der Modellregistry); Kontrollmechanismen (CI-Pflichtprüfungen, Branch-Schutz, Deployment- und Mergebefugnisse, normative Projektregeln wie AGENTS.md und Spec) | unabhängiges Review und ausdrückliche finale Freigabe durch Bas **vor** dem Merge, Label `needs-independent-review` |
| **U – unkritisch** | Dokumentation ohne normative Regeln, Anzeige ohne Zugriff auf geschützte Daten, Werkzeuge ohne Daten-, Order- oder Holdoutzugriff, reine Testergänzungen und Sammel-PRs, die weder Schutzwirkung noch normative Regeln ändern, Konfiguration außerhalb von Risiko/Kosten/Holdout | ein separater KI-Agent prüft Klasse und Inhalt **vor** dem Merge; Integration im Rahmen von §2a ohne zusätzliche Einzelabnahme durch Bas |

- **Nur P1/P2 blockieren einen Merge.** P3-Befunde werden als Issue mit Label `follow-up` erfasst und im
  Review verlinkt; sie lösen keine weitere Review-Runde aus.
- **Gleicher Befund nach zwei Runden nicht geschlossen:** Benutzer informieren; ggf. stärkeres Modell (§3).
- **Historischer Übergang zu #32:** Die damaligen Regeln gelten ab dem Merge-Commit des PRs, der sie einführt (#32), für alle PRs,
  die danach geöffnet oder neu freigegeben werden. #32 selbst wird nach der vorher geltenden Regel
  integriert (unabhängiges Review vor dem Merge, Integrationsstand im PR). Die beim Erstellen dieser Regel
  offenen PRs #20, #21, #28, #29 und #31 sind Klasse K; ihre offenen Befunde, Abhängigkeiten sowie Head- und
  Zielbindung bleiben bestehen, und sie dürfen ihre vorhandenen `CHANGELOG.md`-/`docs/STATUS.md`-Einträge
  behalten. Die fachliche Freigabe von #31 gilt nur gegen den Branch von #21; nach dem Umstellen auf
  `main` braucht #31 eine neue zielgebundene Freigabe am neuen Head und grüne CI.

Für noch offene PRs gelten diese aktuellen Gates beim nächsten Integrationsversuch. Bereits
integrierte Änderungen werden über die Nachpflege in §2b erfasst.

**Unabhängigkeit und Form des Reviews**
- Herkunft und Unabhängigkeit richten sich nach der dokumentierten tatsächlichen Bearbeitung (Feld
  „Agent", Co-Authored-By, Handoff), nicht nach dem GitHub-Autor: Alle Agenten nutzen denselben
  GitHub-Account.
- **Nachweis von `make check`** (Benutzerentscheidung 2026-11-18): Ein grüner CI-Lauf auf **genau dem
  geprüften Head-SHA** gilt als Nachweis, weil die CI dieselben Prüfungen mit den gepinnten Versionen aus
  `requirements-dev.txt` ausführt. Das Review nennt Lauf-ID, Head-SHA und Ergebnis. Ein lokaler Lauf ist
  zusätzlich willkommen, aber nicht Pflicht, wenn die eigene Umgebung die Werkzeuge oder das Netzwerk
  nicht bereitstellt. Ein roter, abgebrochener oder zu einem anderen SHA gehörender CI-Lauf ist kein
  Nachweis; ändert sich die CI-Konfiguration im PR selbst, prüft der Reviewer diese Änderung gesondert.
- Weil derselbe Account keinen eigenen PR formal freigeben kann, wird ein Agentenreview als
  `COMMENT`-Review abgegeben, das beginnt mit `<Agent>: unabhängiges Review`, den geprüften
  **Head-SHA** und Zielbranch nennt und mit „Empfehlung: FREIGEBEN" oder „Empfehlung: ÄNDERUNGEN NÖTIG"
  endet. Vorhandene GitHub-Pflicht-Approvals und Branch-Schutzregeln bleiben unverändert zu erfüllen.
- Eine KI-Reviewempfehlung gilt nur für den genannten Head-SHA und Zielbranch. Nach jedem neuen **inhaltlichen**
  Commit, Rebase oder Umstellen des Zielbranches ist sie verfallen; Review der Änderungen und CI sind
  erneut nötig. **Ausnahme:** Kommt nach der Freigabe nur ein Merge von `main` hinzu, ohne Konflikt in
  Code- oder Testdateien des PRs, genügt eine **Merge-Diff-Prüfung**: (a) der Diff des PRs gegen `main`
  enthält außer dem bereits geprüften Inhalt nichts Neues; (b) die neue Basis wird darauf geprüft, ob sie
  Verträge, Schnittstellen, gemeinsam genutzte Grenzen (z. B. Holdout-Cutoff, Datenformate),
  Schutzmechanismen oder Pflichtprüfungen ändert, von denen der PR abhängt – betroffene Teile werden
  erneut inhaltlich geprüft; (c) CI grün am neuen Head. Sie wird als kurzes Review mit neuem Head-SHA und
  demselben Zielbranch dokumentiert.

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
  tatsächlich ausgeführte Ergebnisse berichten; übersprungene Prüfungen ausdrücklich nennen. Fehlen
  lokal die Werkzeuge: venv mit `pip install -r requirements-dev.txt`; ist das nicht möglich, den Lauf als
  „lokal nicht ausgeführt" melden und die CI am gepushten SHA abwarten.
- Jede ausführbare Änderung mit aussagekräftigen Tests; jede Fehlerbehebung mit Regressionstest.
  Erwartete Testergebnisse nie anpassen, nur damit fehlerhafter Code besteht.
- Änderungsnotiz **nur** in `changelog.d/T-xxx.md` (eine neue Datei je Aufgabe, daher konfliktfrei).
  PRs ändern weder `CHANGELOG.md` noch `docs/STATUS.md`; beides pflegt der tägliche Sammel-PR (§2a).
  Architekturentscheidungen unter `docs/decisions/`.
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

1. **Klasse K:** Das vollständige unabhängige Review und die ausdrückliche finale
   Integrationsfreigabe von Bas müssen für exakt denselben PR-Head und Zielbranch vorliegen.
   Bei Codex als Autor kommt das unabhängige K-Review von Claude Code oder Bas. Review und
   finale Entscheidung werden getrennt dokumentiert. Den Merge führt Bas aus.
2. **Klasse U:** Ein separater KI-Agent einer anderen Agentenart/Modellfamilie als der Autor
   dokumentiert Klassenprüfung, angemessene inhaltliche Prüfung und positive Empfehlung
   für den aktuellen Head und Zielbranch. Danach darf ein vom Autor getrennter
   Integrationsagent den Merge manuell auslösen. Für diese Einzeländerung genügt das KI-Review.
3. In beiden Klassen ist `main` aktuell einbezogen und CI einschließlich `make check` für
   genau den zu mergenden Head grün. Es bestehen keine offenen Blockaden, WIP-Commits oder
   ungeklärten Benutzerentscheidungen. Geschützte Branches und Pflichtprüfungen gelten.
4. Ein neuer Head oder Zielbranch erfordert für Klasse K eine erneute ausdrückliche Freigabe
   von Bas. Der Merge bindet den erwarteten Head und erfolgt per Merge-Commit. Automerge und
   Force-Push bleiben gesperrt. Bas erteilt die finale Release-/Betriebsfreigabe separat
   für den geprüften Stand. Die Live-Sperren gemäß Spec §18 gelten weiter.

**Täglicher Sammel-PR für den Integrationsstand** (Klasse U): Ein Agent übernimmt einmal täglich (oder
nach mehreren Merges) alle `changelog.d/T-xxx.md` gemergter PRs in `CHANGELOG.md`, löscht sie und
aktualisiert `docs/STATUS.md` („integriert (PR #n)"). Einzel-PRs enthalten diese Dateien nicht mehr; das
beseitigt die meisten Merge-Konflikte. **Keine ungeprüften Direktänderungen an `main`** – auch der
Sammel-PR geht über die Klassenprüfung.
Nach dem Merge nur noch außerhalb von Git: Issue auf `done`, abhängige PRs auf `main` umstellen (deren
Freigaben verfallen damit, siehe §0; reine `main`-Merges: Merge-Diff-Prüfung).
Integration in `main` ist **keine Livefreigabe** (Spec §18).

## 2b. Anforderungen, Testnachweise und Nachpflege in Demo-Jama

- Demo-Jama ist das Register für **alle** belegbaren Projektanforderungen einschließlich der
  bisherigen offenen und geschlossenen GitHub-Issues. Verknüpfe Issue, Spec, ADR und weitere
  bestätigte Quellen mit stabilen Requirement-IDs und Versionsständen. Jedes Issue bekommt
  eine Zuordnung, eine begründete Kennzeichnung ohne eigenes Requirement oder einen offenen
  Klärpunkt. Anforderungen ohne Issue werden ebenfalls erfasst.
- Requirements-Agent, QA-Agent, Implementierungsagent und Review-Agent arbeiten in getrennten
  Rollen und Läufen. Requirements und freigegebene Abnahmetests liegen außerhalb der Schreibrechte
  des Implementierers. CI liefert die Testergebnisse. Neue oder geänderte Nachweise verknüpfen
  Requirement-Version, Testfall-Version, CI-Lauf und PR-Head. Historische Lücken bleiben offen.
- Bas bestätigt die erste und sicherheitsrelevant geänderte fachliche Baseline und erteilt die
  Freigaben gemäß §2a. Agenten erstellen Entwürfe, Tests und Reviewempfehlungen. Bei Klasse U
  genügt das separate KI-Review zur Integration. Die vollständige Nachpflege ist erst belegt,
  wenn ein paginierter Abgleich jedes Issue berücksichtigt und der Lückenbericht vorliegt.
- Der Übergang und die Abnahme für Demo-Jama/MCP stehen in
  [der Codex-Implementierungsanweisung](docs/DEMO_JAMA_CODEX.md). Solange die Demo noch nicht
  verfügbar ist, erfasse Quellen und Nachweislücken im PR. Ein Link oder ein geschlossenes Issue
  gilt allein weder als Abnahme noch als bestandener Test.

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

- Experiment-IDs sind aufgabenbezogen: `EXP-T<nnn>-<lfd>` (z. B. `EXP-T009-001` für T-009), nächste ID
  mit `next_experiment_id("T-009")`. Nur der Agent, der die Aufgabe beansprucht hat, vergibt IDs dieser
  Aufgabe; so kollidieren getrennte Worktrees nicht. `EXP-0001` ist die einzige Alt-ID.
- Vor jedem Experiment `docs/research/EXP-T<nnn>-<lfd>.md` (Vorlage `docs/research/TEMPLATE.md`): Hypothese,
  Plausibilität, Unterschied zur bestehenden Methode, Daten und Verfügbarkeit, Vergleichsmodell,
  Parameterraum, Bewertungs-/Abbruchkriterien, Budget. Ohne Budget nur lokale Experimente.
- Ergebnisse als unveränderliche Datei im Experimentregister `experiments/EXP-T<nnn>-<lfd>.json`
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

## 7. Lokaler Coding-Runner (T-020, technisch gesperrt)

Der Benutzer hat am 2026-11-17 das Konzept für automatisierte Medium-Coding-Aufgaben beschlossen.
Die implementierte Ausführung ist technisch gesperrt. Verfügbar sind Planung, Diagnose und Offline-Tests.
Labels und Konfiguration schalten keine Worker frei. Details: [CODING_RUNNER](docs/CODING_RUNNER.md).

Eine spätere Freischaltung erfordert einen unabhängig geprüften isolierten Ausführungsbereich,
beschränkte Werkzeuge, einen separaten Clone ohne `.env` oder Broker-Zustand und getrennte GitHub-Rechte.
Worker erhalten keine GitHub-Schreibidentität. Der Steuerprozess kontrolliert Veröffentlichung und
GitHub-Mutationen separat. Branch-Schutz für `main` verlangt Pull Requests und Status-Checks. Der erste
echte Lauf benötigt ausdrückliche Benutzerfreigabe.

Für eine spätere freigegebene Ausführung gelten:

- Nur Benutzer setzen `auto-ready` und `effort:medium`; Coding-Issues benötigen zusätzlich `work:coding`.
- Reihenfolge: eigene aktuelle Review-Befunde, fremde Reviews, freie freigegebene Coding-Aufgaben.
- Kritische Aufgaben und höhere Denkstufen bleiben manuell. Modell- und Denkstufenwechsel erfolgen bewusst.
- Ein Worker je Runner, persistente Claims, isolierte Worktrees und kein Übernehmen aktiver manueller Arbeit.
- Merges, Forschung, Strategieexperimente, Liveorders und bezahlte API-Fallbacks sind ausgeschlossen.
- Die Regeln zu Prüfungen, Handoffs und Geheimnissen bleiben verbindlich. Bei Blockaden anhalten.
