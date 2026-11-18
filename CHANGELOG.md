# Changelog

Alle fachlich zusammenhängenden Änderungen. Neueste zuerst.

## 2026-11-18 – Kostentabelle SPY/QQQ mit gemessenen Spreads (Vorbedingung T-008)

**Änderungen**
- `config/cost_schedule.json` und `localtrader/cost_schedule.py`: Kosten je Instrument, Währung, Seite und
  Zeitraum, getrennt nach Quelle/Messwert/Annahme, mit Validierung und Digest.
- CLI `measure-spreads`: Stichprobe der SIP-Quotes an Bar-Eröffnungen, nur Forschungszeitraum.
- `docs/costs/COST_SCHEDULE.md`: Belege, Messergebnisse, Vergleich mit der Konfiguration (unterschätzt
  um ca. 6 % / 19 %). Engine unverändert.

**Tests:** `tests/test_cost_schedule.py`.

## 2026-11-17 – Korrekturen nach unabhängigem Review (PR #17)

**Anlass/Ziel:** Drei von Codex reproduzierte Fehler beheben (Review am Commit `1cd6e14`).

**Änderungen**
- `localtrader/intraday_engine.py`: Bars werden nach Verfügbarkeit (`received_at`) verarbeitet. Eine
  verspätete Bar war vorher schon in früheren Entscheidungen sichtbar (Look-ahead). `earliest_execution`
  ist jetzt bindend; Intraday-Strategien setzen ihn auf den Signalzeitpunkt (Ausführung an der nächsten
  Eröffnung bleibt unverändert).
- Ausstehende Einstiege werden auf der Ausführungsbar erneut geprüft: Bar nach Datenlücke, außerhalb der
  Handelszeit oder zu altes Signal → verworfen (`entry_cancelled`). Ausstiege werden nie blockiert.
- `localtrader/intraday_data.py`: NaN/±Inf in OHLCV werden verworfen (`non_finite_value`).
- Beispielreport mit neuen Zahlen; auf den DAX-Daten entfallen 2 bzw. 5 Einstiege außerhalb der Handelszeit.
- Zweites Review (Head `be6bf16`): Das vollständige Tief der laufenden Bar beeinflusste über den Stop schon
  den Positionsstatus früherer Entscheidungen innerhalb derselben Bar. Die Engine verarbeitet jetzt jeden
  Schritt kausal (Entscheidungen vor der Eröffnung, Eröffnung, Entscheidungen während der Bar, Stops/
  Drawdown/Kill-Switch am Barende); Stops werden am Barende verbucht.

**Tests:** Regressionstests für verspätete Bars, bindendes `earliest_execution`, ausstehenden Einstieg nach
Datenlücke, nicht blockierten Ausstieg, veraltetes Signal über Nacht und NaN/Inf je Feld; alle schlugen
mit dem alten Code fehl. Zusätzlich: späteres Tief verändert keine frühere Entscheidung (ein und zwei
Instrumente inkl. Kill-Switch).

## 2026-11-17 – Intraday-Engine, Trendfolge und Mean Reversion

**Anlass/Ziel:** Zwei nachvollziehbare Basisstrategien auf 15-Minuten-Daten zeitlich korrekt und
kostenbewusst backtesten; Benchmarks und Robustheitsprüfungen.

**Änderungen**
- `localtrader/intraday_strategies.py`: Trendfolge (1-h-Trendfilter, Ausbruch, ATR-Stop, Donchian-Exit,
  optionaler Zeitstopp) und Mean Reversion (z-Score zum gleitenden Mittel, Efficiency-Ratio-Filter,
  Rückkehr zum Mittel, Stop, max. Haltedauer) mit begründeten Hypothesen.
- `localtrader/intraday_engine.py`: ereignisbasierter Engine (siehe ADR 0003), Kennzahlen inkl.
  geometrischer Monatsrendite, Block-Bootstrap-Intervall, Drawdown-Dauer, Haltedauer, Turnover,
  Kosten nach Art, Exposure; Buy-and-hold- und Cash-Benchmark.
- `localtrader/intraday_report.py`: Reports (JSON, CSV, JSONL, SVG) und Szenarien: doppelte Kosten,
  dreifacher Spread, verzögerte Ausführung, Parameter ±20 %, drei Marktphasen.
- `FillModel` mit instrumentbezogenen Kostenprofilen und Spread-Stress; `Portfolio.charge()`.
- CLI `intraday-backtest`, `intraday-stress`; Make-Ziele `intraday`, `intraday-stress`.
- Konfiguration `[instruments.DAX]`, `[intraday]`, `[strategies.intraday_trend]`, `[strategies.mean_reversion]`.
- Beispielreport `docs/reports/2026-11-17_dax_baseline.md`, ADR 0003.

**Tests:** neu `tests/test_intraday_engine.py`: Ausführung an der nächsten Eröffnung, nur abgeschlossene
Bars sichtbar, Verzögerung, Stop bei Kurslücke/innerhalb der Bar/in der Einstiegsbar, Slippage am Stop,
Teilausführung, Finanzierung, Kosten, Sprung nach Datenlücke, Kill-Switch-Abbau, Strategielogik,
Bootstrap, deterministischer End-to-End-Test (Import → Signal → Risiko → Order → Fill → Portfolio → Report)
mit Buchhaltungsidentität, Szenarioabdeckung.
Fehler beim Entwickeln gefunden und durch die Tests abgesichert: Orders wurden durch einen
1-µs-Vergleich eine Bar zu spät ausgeführt.

**Ausgeführte Prüfungen:** `make check` (97 Tests + 6 Subtests) bestanden; `make intraday`,
`make intraday-stress` auf den DAX-Daten ausgeführt.

**Ergebnis:** keine belastbare profitable Strategie (siehe Beispielreport).

**Einschränkungen:** Kostenparameter sind Annahmen; Stops füllen vollständig; < 1 Jahr Daten;
noch kein Walk-forward-Training.

## 2026-11-17 – 15-Minuten-Datenimport und Qualitätsprüfung

**Anlass/Ziel:** Intraday-Daten zeitlich korrekt und nachvollziehbar einlesen.

**Änderungen**
- `localtrader/intraday_data.py`: CSV-Import (MetaTrader- und ISO-Format) mit Quellzeitzone → UTC,
  Verfügbarkeitszeit, Verwerfen ungültiger Zeilen, Lückenerkennung ohne Auffüllen, Sprungmeldung,
  Datenmanifest mit Datenversion; `resample()` für 1 h/1 d ohne Look-ahead; Handelssitzungen;
  klar gekennzeichnete synthetische Intraday-Daten.
- `Market.INDEX` ergänzt.
- `docs/DATA_FORMAT.md` mit Format, Qualitätsregeln und Beschreibung der DAX-Beispieldatei.

**Tests:** neu `tests/test_intraday_data.py` (Zeitzonen inkl. Sommerzeit, Verfügbarkeit, alle
Verwerfungsgründe, Lücken innerhalb/außerhalb der Sitzung, Sprünge, ISO-Format, Datenversion,
DAX-Beispieldatei inkl. Plausibilisierung der Zeitzone, Resampling ohne Look-ahead).

**Ausgeführte Prüfungen:** `make check` bestanden.

**Einschränkungen:** Zeitzone der DAX-Datei ist eine belegte Annahme; kein Feiertagskalender; kein Parquet.

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
