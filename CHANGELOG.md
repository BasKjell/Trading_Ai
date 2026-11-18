# Changelog

Alle fachlich zusammenhängenden Änderungen. Neueste zuerst.

## 2026-11-18 – Kostentabelle SPY/QQQ mit gemessenen Spreads (Vorbedingung T-008)

**Änderungen**
- `config/cost_schedule.json` und `localtrader/cost_schedule.py`: Kosten je Instrument, Währung, Seite und
  Zeitraum, getrennt nach Quelle/Messwert/Annahme, mit Validierung und Digest.
- CLI `measure-spreads`: Stichprobe der SIP-Quotes an Bar-Eröffnungen, nur Forschungszeitraum.
- Nach Review (`1404b5e`): Obergrenze fest `[holdout] start` (inkl. ganzem Anfragefenster), Eröffnungsstatistik
  in New-York-Zeit, NaN/Inf abgelehnt, Vollständigkeit je Datum im erklärten Geltungsbereich; Neumessung.
- `docs/costs/COST_SCHEDULE.md`: Belege, Messergebnisse, Vergleich mit der Konfiguration (unterschätzt
  um ca. 6 % / 19 %). Engine unverändert.

**Tests:** `tests/test_cost_schedule.py`.

## 2026-11-18 – T-008 Walk-forward-Forschungsdesign (PR #22)

**Anlass/Ziel:** Forschungsplan und maschinenlesbares Protokoll für US-Index-ETFs, einen Vergleich mit Cash und Exposure-Benchmark sowie einen kontrollierten finalen Test.

- Vier vorab definierte Varianten, drei chronologische Falten, unveränderte zentral-konfirmatorische Gates.
- Benutzerentscheidungen aus dem Methodenreview: Shadow-Gate mit unadjustierter einseitiger 95-%-Untergrenze je Vergleich. Adjustierte Schranke als Bericht mit ausgewiesener Trennschärfe. Aktualisierte Vergleichsfamilie und Budgetabgrenzung.
- Physische Forschungsexporte vor dem Holdout. Der Benutzer verwahrt die Freigabe und das Journal für maximal einen reservierten zentralen Abruf. Teilweise frühere Exposition dokumentiert.
- Vollständiger Börsenkalender, Umgang mit Anbieterausnahmen, kausaler EZB-Mittelkurs mit separaten Umtauschkosten und Definition vollständiger Stundenkerzen vorgegeben.

**Stand:** Design überarbeitet. Freeze, Implementierung und Auswertung ausstehend.

## 2026-11-18 – T-011 (Datenteil): EUR/USD-Referenzkurse der EZB (PR #25)

**Anlass/Ziel:** Voraussetzung für Ergebnisse in EUR (T-011, T-008). Nur Daten, keine Buchhaltung.

**Änderungen**
- `localtrader/fx_rates.py`: Abruf, Prüfung und Speicherung der EZB-Referenzkurse; Verfügbarkeit ab
  16:00 Europe/Berlin; kausale Abfrage mit maximalem Kursalter (5 Tage, fail closed); Holdout-Schnitt
  nach Verfügbarkeit; Digest der Reihe.
- CLI `fetch-fx`; Abschnitt in `docs/DATA_FORMAT.md`.

- Nach Review (`552ae42`): Holdout-Zeilen werden vor dem Parsen übersprungen (ein Defekt im Holdout
  bricht den Forschungsimport nicht ab); leerer `OBS_STATUS` wird verworfen.

**Tests:** `tests/test_fx_rates.py`: Veröffentlichungszeit (MEZ/MESZ), Verwerfungsgründe, Konflikt-
abbruch, Sprünge/Lücken, Nutzung erst nach Veröffentlichung, Wochenende, veralteter/fehlender Kurs,
Umrechnung, Dateirundlauf mit Manifest, Holdout-Schnitt, manipulierte Verfügbarkeit, Abruf-URL.

**Nicht enthalten (T-011, High):** Decimal-Buchhaltung, Umrechnung von Fills, Bewertung, Dividenden und
Kosten, getrennter Währungseffekt, Export.

## 2026-11-18 – T-005 Persistenter Orderzustand und Fake-Broker-Abgleich (PR #26)

Ein SQLite-Journal sichert Client-ID, Orderzustand, kumulierte Ausführungen und Abgleichalarme im Fake-Broker-Pfad. Offene Verkäufe reservieren Bestand. Nach einem Timeout oder einer Abweichung sperrt der Manager neue Orders. Der Wiederanlauf gleicht offene und abgeschlossene Orders, Cash und Bestände ab. Der Risikoprüfer kann den Abgleichstatus lesen. Tests umfassen doppelte und ungeordnete Meldungen, konkurrierende Aufrufer und einen echten Prozessabbruch mit simuliertem Broker.

## 2026-11-18 – T-007 (Teil Reports): Experimentregister und Intraday-Reports im Dashboard (PR #24)

**Anlass/Ziel:** Spec §13 / Issue #3, nur der von T-006 unabhängige Report-Teil (Denkstufe medium).

**Änderungen**
- `localtrader/report_index.py`: schreibgeschützte Übersicht über `experiments/` (alle Versuche aller
  Agenten, Gesamtzahl der Varianten, Holdout-Nutzungen) und `reports/<lauf>/summary.json|scenarios.json`
  (Backtests mit Kennzahlen und Buy-and-hold, Stresstests mit Kosten-, Verzögerungs-, Parameter- und
  Phasenszenarien). Läufe mit Holdout-Nutzung werden markiert, unlesbare Dateien als Fehler gelistet.
- Dashboard: Endpunkt `/api/reports` und zwei Tabellen (Experimentregister, Intraday-Reports).
- Betriebsansicht (Modus, Positionen, Orders, Sperren …) bleibt offen bis T-006.

- Nach Review (`83fae16`): falsch typisierte Reportdateien werden isoliert als Fehler gelistet.

**Tests:** `tests/test_report_index.py`: Register-Zählung inkl. Holdout, fehlende Verzeichnisse,
Backtest-/Stress-Einlesen inkl. NaN und kaputter Datei, HTTP-Endpunkt.

## 2026-11-17 – T-009 Zusammenarbeit mehrerer Agenten (PR #18)

**Anlass/Ziel:** Projekt mit Claude und Codex abwechselnd fortsetzbar machen (Spec §19, §20).

**Änderungen**
- `AGENTS.md` (einziges Regelwerk, inkl. Modellwahl/Denkstufe/Eskalation/Review), `CLAUDE.md` verweist darauf.
- `docs/PROJECT_SPEC.md` mit den vollständigen Anforderungen inkl. §18–20; alte Codex-Spezifikation
  nach `docs/archive/` (nicht mehr verbindlich); `docs/PLAN.md` durch `docs/STATUS.md` ersetzt;
  `docs/ARCHITECTURE.md` mit Komponenten, Schnittstellen und Statuskennzeichnung.
- Vorlagen: Handoff, Research, PR, Issue; `docs/agents/MODEL_EVALUATION.md`; ADR 0004.
- Experimentregister `localtrader/experiment_registry.py`, CLI `experiments`, Eintrag EXP-0001 (18 Varianten).
- `scripts/start_task.sh` (Branch + Worktree je Aufgabe), `scripts/check_links.py` in `make check` und CI.
- GitHub: Zustandslabels und Issues T-005 bis T-019.
- Benutzerentscheidung: Jede Sitzung beginnt mit dem Review fremder PRs; Merge nach `main` durch einen
  Integrationsagenten einer anderen Agentenart nach unabhängigem Review und grüner CI erlaubt
  (AGENTS.md §0/§2a, Spec §21). Spec-Einleitung um „gemeinsam mit anderen Entwicklern" ergänzt.

- Nach Codex-Review (PR #18, `9991e9c`): Experiment-IDs aufgabenbezogen (`EXP-T009-001`), weil ein
  globaler Zähler in getrennten Worktrees doppelte IDs vergab. AGENTS.md §0: eigene PRs auf Befunde
  prüfen und diese vorrangig beheben; REST-Fallback ohne `gh`; Unabhängigkeit nach tatsächlicher
  Bearbeitung; Freigabe an Head-SHA und Zielbranch gebunden; Merge mit erwartetem Head; Integrationsstand
  (CHANGELOG/STATUS) vor der Freigabe im PR, keine Direktänderungen an `main`.

- Benutzerentscheidung 2026-11-18: Grüne CI auf genau dem geprüften Head-SHA gilt als Nachweis für
  `make check` (AGENTS.md §0, §2a); lokaler Lauf nur noch zusätzlich, da Reviewer-Umgebungen ohne
  Netzwerk die gepinnten Werkzeuge nicht installieren können.

**Tests:** `tests/test_experiment_registry.py` (Unveränderlichkeit, Zählung über Agenten, Validierung,
umbenannte Dateien, versioniertes Register, zwei getrennte Register ohne Kollision und ohne Verlust beim Zusammenführen), `tests/test_tooling_scripts.py` (Linkprüfung, Worktree-Skript).

**Ausgeführte Prüfungen:** `make check` bestanden (106 Tests + 11 Subtests, Secret-Scan, Linkprüfung).

**Einschränkungen:** EXP-0001 wurde nachträglich registriert. Frühere Versuche des Tages-Research-Labs
(lokale SQLite auf dem Mac) sind nicht im Register enthalten.

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
- Plan (`docs/PLAN.md`, inzwischen durch `docs/STATUS.md` ersetzt) und ADR 0001.

**Tests:** neu `tests/test_secret_scan.py` (verbotene Dateien, Schlüsselmuster, saubere Dateien, Regressionstest:
noch nicht hinzugefügte Dateien werden ebenfalls gescannt).

**Ausgeführte Prüfungen:** `make check` lokal (Python 3.13): Format, Lint, mypy, 46 Tests,
Secret-Scan, Konfiguration – bestanden.

**Einschränkungen:** Coverage läuft nur in CI (lokal nicht installiert). Fachliche Lücken siehe `docs/STATUS.md`.
