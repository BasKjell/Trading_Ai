# Status

Stand: 2026-11-18. Kennzeichnung: **geplant / implementiert / getestet / integriert / blockiert**.
„Integriert" bedeutet: in `main` gemergt. Aufgaben und Zuständigkeiten: GitHub Issues (Label = Zustand).

## Integrationsstand

| Branch | Inhalt | Zustand |
|---|---|---|
| `main` | T-001–T-004: localtrader-Basis, korrigierte Kosten/Risikolimits, 15-Minuten-Import, kausale Intraday-Engine (geprüfte Codebasis `1dfb421`) | **integriert** (PR #17) |
| `agent/T-009-agent-collaboration` | T-009 (#16): AGENTS.md, Spec, Register, Vorlagen | **integriert** (PR #18) |
| `agent/T-005-order-state` | T-005 (#1): Persistenter Orderzustandsautomat und Fake-Broker-Abgleich | **integriert (PR #26)** – gilt nach dessen Merge |
| `agent/T-018-alpaca-etf-data` | T-018 (#13): SPY/QQQ 2016–2026 (Rohkurse, Dividenden mit Zahltag, NYSE-Halbtage), Holdout ab 2025-11-01 | **integriert (PR #21)** – gilt nach dessen Merge |
| `agent/T-008-nyse-calendar` | Vollständiger NYSE-Kalender + Slot-Inventur (Vorbedingung T-008) | **integriert (PR #31)** – gilt nach dessen Merge; Basis #21 |
| `agent/T-011-fx-data` | T-011 Datenteil (#6): EZB EUR/USD, kausale Abfrage, Holdout-Schnitt | **integriert (PR #25)** – gilt nach dessen Merge; Buchhaltung in EUR offen |
| `agent/T-007-report-view` | T-007 Report-Teil (#3): Experimentregister und Intraday-Reports im Dashboard | **integriert** (PR #24); Betriebsansicht offen (T-006) |
| `agent/T-008-research-design` | T-008 Forschungsdesign (#4) | **integriert (PR #22)** – gilt nach dessen Merge. Forschungsprotokoll im Entwurf, Runner in #37 integriert, Freeze ausstehend |
| `agent/T-008-journal-registry` | T-008 Bausteine: Protokollprüfung, Versuchsjournal (atomare Reservierung, kanonischer zentraler Slot), Modellregistry | **integriert (PR #23)** – gilt nach dessen Merge; im Forschungsrunner #37 angebunden |
| `agent/T-008-folds` | T-008 Falten, Purge/Embargo, Warm-up (#4) | **integriert (PR #30)** – gilt nach Merge; im Forschungsrunner #37 angebunden, historische Auswertung ausstehend |
| `agent/T-019-operating-rules` | T-019 (#14): verbindliche Betriebsregeln, Prüfung beim Start | **integriert (PR #27)** – gilt nach dessen Merge; Durchsetzung im Paper-/Livebetrieb teilweise geplant |
| `agent/T-008-cost-schedule` | Kostentabelle SPY/QQQ mit gemessenen Spreads (Vorbedingung T-008) | **integriert (PR #28)** – gilt nach dessen Merge; im Forschungsrunner #37 angebunden |
| `agent/T-009-review-tiers` | T-009 (#16): Review nach Risikoklasse K/U, Merge-Diff-Prüfung, täglicher Sammel-PR | **integriert (PR #32)** – gilt nach dessen Merge |
| `agent/T-011-fx-holdout-lock` | T-011 Folge: `fetch-fx` nur bis vor den Holdout-Start, Filter vor Validierung und Manifest | **integriert (PR #29)** |
| `agent/T-011-fx-help` | Hilfetext `fetch-fx --end` (Issue #33) | **integriert (PR #34)** |
| `agent/T-020-local-coding-runner` | T-020 (#19): Vorschau und Diagnose für lokale Coding-Aufgaben | **integriert (PR #20)** – gilt nach Merge; Worker-Ausführung technisch gesperrt |
| `agent/T-008-validation-runner` | T-008 (#4): Walk-forward-Runner und separater zentraler Holdout-Aufruf | **integriert (PR #37)**. Datenbelege, Freeze und wirtschaftlicher Forschungslauf ausstehend. Zentraler Holdout geschlossen |

## Funktionen

| Funktion | Zustand | Nachweis / Einschränkung |
|---|---|---|
| `make check`, CI, Secret-Scan, Linkprüfung | getestet | GitHub Actions bei Push/PR |
| Konfiguration mit harten Risikoobergrenzen | getestet | `tests/test_risk_execution.py` |
| 15-Minuten-CSV-Import, Qualität, Resampling | getestet | `tests/test_intraday_data.py`; NYSE-Kalender mit Halbtagen und Slot-Inventur (PR #31); kein Parquet |
| Kostenmodell (Spread, Slippage, Gebühren, Mindestgebühr, Finanzierung) | getestet | Versionierte Kostentabelle mit Quellen und gemessenen Spreads (PR #28) ist im T-008-Forschungsrunner (#37) angebunden |
| Risiko-Engine (0,20 %/Trade, 1 % offenes Risiko, 20 %/Instrument, DD 5/8/10 %) | getestet | ADR 0002 |
| Intraday-Engine, Trendfolge, Mean Reversion, Benchmarks, Stressszenarien | getestet | `tests/test_intraday_engine.py`; Beispielreport `docs/reports/` |
| Multi-Asset-Trendfolge mit gemeinsamem Risikobudget | **integriert (PR #48)** | T-021 (#41): EXP-T021-001 und EXP-T021-002 ohne Vorteil für das getestete Sechs-ETF-Portfolio |
| Experimentregister | getestet | `tests/test_experiment_registry.py`, EXP-0001; IDs je Aufgabe (`EXP-T009-001`) |
| Offline-Paper-Demo mit Neustart und Kill-Switch | implementiert | nur Demo-Ereignisse, kein 15-Minuten-Betrieb |
| Persistenter Orderzustandsautomat und Fake-Broker-Abgleich | getestet | T-005 (#1), PR #26; `tests/test_order_manager.py`. Periodischer Abgleich im Paper-Dienst folgt mit T-006. |
| Paper-Betrieb 15 min, Modi backtest/paper/shadow/live | geplant | T-006 (#2) |
| Live-Trading | **gesperrt** | kein Live-Transport im Code; `mode = "offline"` wird erzwungen |
| Dashboard: Experimentregister und Intraday-Reports | integriert mit PR #24 | `tests/test_report_index.py` |
| Dashboard Betriebsansicht | geplant | T-007 (#3) (Research-Dashboard vorhanden) |
| Walk-forward + Champion-Challenger 15 min, Holdout-Kontrolle | **integriert (PR #37)** | T-008 (#4): Prüfung der Daten- und Verwahrbelege, Freeze und wirtschaftlicher Forschungslauf ausstehend. Zentraler Holdout geschlossen |
| Dezimalbuchhaltung, FX | teilweise | T-011 (#6): EZB-Kurse integriert (PR #25, #29); Buchhaltung in EUR geplant |
| Broker-/Datenadapter, Alpaca-Preflight | geplant | T-012 (#7): Instrumentenfrage entschieden (US-ETFs, ADR 0005 in #21); Preflight offen |
| Live-Voraussetzungen §18.4–18.10 | geplant | T-013 (#8) bis T-016 (#11), T-019 (#14) |
| PostgreSQL/Parquet | geplant | T-017 (#12) (Entscheidung offen, ADR 0001 zurückgestellt) |
| Mehrjährige Intraday-Daten | integriert | T-018 (#13), PR #21: SPY/QQQ 2016 bis 31.10.2025 (Forschungszeitraum) über Alpaca, Holdout physisch ausgeschlossen |
| Lokaler Coding-Runner | integriert | T-020 (#19), PR #20: Vorschau/Diagnose; Worker technisch gesperrt |

## Ergebnisse

Mit den verfügbaren Daten (DAX, 165 Handelstage) wurde **keine belastbare profitable Strategie gefunden**
(EXP-0001, `docs/research/EXP-0001.md`). Versuche gesamt im Register: siehe `python3 -m localtrader experiments`.

## Offene Konflikte / Benutzerentscheidungen

1. **Speicher (T-017):** PostgreSQL/Parquet laut Spec §4 vs. derzeit SQLite/CSV (ADR 0001).

Entschieden 2026-11-17/18: Instrument frei wählbar → US-Index-ETFs mit Alpaca-Daten (T-012/T-018,
ADR 0005); grüne CI am geprüften Head-SHA gilt als `make check`-Nachweis (AGENTS.md §0).

## Nächste Ziele (Reihenfolge)

1. T-008 (#4): Daten- und Verwahrbelege prüfen, das Protokoll einfrieren und danach den wirtschaftlichen Forschungslauf durchführen. Der zentrale Holdout bleibt geschlossen, bis der Benutzer ihn nach bestandenem Forschungsurteil separat freigibt.
2. T-012 Alpaca-Preflight (Paper-Konto, nur lesend), danach T-006 Paper-Betrieb.
3. T-011 Buchhaltung in EUR (High); T-013 Instanzsperre/Backup.
4. T-007 Betriebsansicht, T-010 (#5) Abnahme.
