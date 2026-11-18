# ADR 0004: Zusammenarbeit mehrerer manuell gestarteter Agenten

Datum: 2026-11-17 · Status: angenommen

## Kontext

Der Benutzer arbeitet abwechselnd mit Claude und Codex. Chatverläufe sind keine Projektquelle (Spec §19).
Bisher lagen Anforderungen nur in den claude.ai-Projektanweisungen und einer älteren Codex-Spezifikation.

## Entscheidung

- `AGENTS.md` ist das einzige Regelwerk (Codex liest es nativ); `CLAUDE.md` verweist nur darauf.
- `docs/PROJECT_SPEC.md` enthält die vollständigen Benutzeranforderungen inkl. §20 (Modellwahl).
  `CODEX_MASTER_PROMPT.md` wird nach `docs/archive/` verschoben und ist nicht mehr verbindlich
  (abweichende Werte: 0,25 % Risiko, DD 8/12/15 %, 100.000 Kapital).
- `docs/PLAN.md` wird durch `docs/STATUS.md` ersetzt (eine Quelle für den Stand).
- Aufgaben als GitHub Issues mit Task-ID `T-xxx` und Zustandslabels; Claim per Kommentar, frühester
  serverseitiger Zeitstempel gewinnt.
- Je Aufgabe Branch `agent/T-xxx-…` und eigener Worktree (`scripts/start_task.sh`).
- Änderungsnotizen in `changelog.d/`, Zusammenführung in `CHANGELOG.md` bei der Integration.
- Experimentregister: eine unveränderliche JSON-Datei je Experiment unter `experiments/`
  (keine parallelen Schreibkonflikte), Gesamtzahl der Versuche per CLI.
- GitHub GraphQL ist aus Claude-Code-Sitzungen gesperrt; Agenten nutzen `gh api` (REST).

## Alternativen

- Gemeinsame SQLite-Registry: verworfen (parallele Schreibkonflikte, nicht versionierbar).
- Automatischer Orchestrator: laut Spec ausgeschlossen.

## Konsequenzen

- Mehr Dokumentationspflege je Aufgabe; dafür ist jede Aufgabe ohne Chatverlauf fortsetzbar.
- Integration nach `main`: Benutzerentscheidung vom 2026-11-17 erlaubt den Merge durch einen Integrationsagenten
  einer anderen Agentenart nach unabhängigem Review und grüner CI (AGENTS.md §2a, Spec §21). Eigene PRs nie.
- Jede Sitzung beginnt mit dem Review offener fremder PRs (AGENTS.md §0).
