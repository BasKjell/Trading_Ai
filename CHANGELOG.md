# Changelog

Alle fachlich zusammenhängenden Änderungen. Neueste zuerst.

## 2026-11-17 – Kostenbuchung korrigiert, Risikolimits gemäß Vorgaben

**Anlass/Ziel:** Kosten wurden doppelt belastet; Risikoparameter entsprachen nicht den Projektvorgaben.

**Änderungen**
- Fehlerbehebung: Spread und Slippage werden nur noch im Ausführungspreis berücksichtigt, nicht zusätzlich
  vom Cash abgezogen. Trade-Kennzahlen ziehen nur noch Gebühren ab. Einstiegsgebühren werden anteilig
  im realisierten Ergebnis berücksichtigt.
- Mindestgebühren (`equity_min_fee`, `crypto_min_fee`) und Round-Trip-Kostenschätzung für die Positionsgröße.
- Risiko-Engine: 0,20 % je Trade, 1 % offenes Stop-Risiko, 20 % je Instrument, Korrelationsgruppen (40 %),
  Volumenanteil, Mindestorderwert, Drawdown-Leiter 5/8/10 % mit persistentem Kill-Switch und
  `deleveraging_required`; Ein-/Auszahlungen bereinigen die High-Water-Mark; harte Obergrenzen für Konfigurationen.
- Startkapital 10.000 EUR.
- ADR 0002.

**Tests:** neu `tests/test_costs.py` (Regressionstest doppelte Kosten, Mindestgebühr, Kostenstress,
Round-Trip-Schätzung); `tests/test_risk_execution.py` neu geschrieben für die neuen Limits (Größen, offenes
Risiko, Gruppen, Liquidität, Drawdown-Stufen, Tagesverlust, HWM, harte Grenzen); Kill-Switch-Neustarttest.
Die bisherigen Tests zu 0,5 %-/12 %-Regeln wurden ersetzt, weil sich die fachliche Vorgabe geändert hat.

**Ausgeführte Prüfungen:** `make check` (67 Tests + 6 Subtests) bestanden; Tages-Backtest-Smoke-Lauf
(synthetisch, 600 Tage) läuft durch.

**Einschränkungen:** Kontrollierter Positionsabbau nach Kill-Switch folgt im Intraday-Engine.
Rücknahme: Revert dieses Commits stellt die alten Limits wieder her (nicht empfohlen wegen Kostenfehler).

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
