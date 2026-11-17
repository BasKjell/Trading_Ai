# Changelog

Alle fachlich zusammenhängenden Änderungen. Neueste zuerst.

## 2026-11-17 – Übernahme localtrader, Tooling und CI

**Anlass/Ziel:** Arbeitsfähige, geprüfte Basis im Repository schaffen.

**Änderungen**
- `localtrader`-MVP (Daten, Strategien, Risiko, Ausführung, Backtest, Paper, Forschung, Dashboard) ins Repo
  übernommen; RL-Notebook von 2021 nach `legacy/` verschoben.
- `.gitignore` schließt `.env`, Schlüssel, Datenbanken, Logs, Laufzeitzustand und heruntergeladene Daten aus;
  die DAX-Beispieldatei bleibt versioniert. `.env.example` mit Platzhaltern ergänzt.
- `make check` (ruff format/lint, mypy, pytest, Secret-Scan, Konfigurationsprüfung) und
  GitHub-Actions-Workflow mit JUnit- und Coverage-Artefakten.
- Lint-/Typfehler behoben, u. a. ein Fehler, bei dem Stop-/Exit-Signale positional `None` als
  Strategieversion statt als Stop-Preis übergaben.
- Plan (`docs/PLAN.md`) und ADR 0001.

**Tests:** neu `tests/test_secret_scan.py` (verbotene Dateien, Schlüsselmuster, saubere Dateien, Regressionstest:
noch nicht hinzugefügte Dateien werden ebenfalls gescannt).

**Ausgeführte Prüfungen:** `make check` lokal (Python 3.13): Format, Lint, mypy, 46 Tests,
Secret-Scan, Konfiguration – bestanden.

**Einschränkungen:** Coverage läuft nur in CI (lokal nicht installiert). Fachliche Lücken siehe `docs/PLAN.md`.
