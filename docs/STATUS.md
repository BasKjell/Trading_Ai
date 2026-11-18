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
| `agent/T-011-fx-data` | T-011 Datenteil (#6): EZB EUR/USD, kausale Abfrage, Holdout-Schnitt | **integriert (PR #25)** – gilt nach dessen Merge; Buchhaltung in EUR offen |
| `agent/T-007-report-view` | T-007 Report-Teil (#3): Experimentregister und Intraday-Reports im Dashboard | **integriert** (PR #24); Betriebsansicht offen (T-006) |
| `agent/T-008-research-design` | T-008 Forschungsdesign (#4) | **integriert (PR #22)** – gilt nach dessen Merge. Forschungsdesign im Entwurf, Implementierung und Freeze ausstehend |
| `agent/T-008-journal-registry` | T-008 Bausteine: Protokollprüfung, Versuchsjournal (atomare Reservierung, kanonischer zentraler Slot), Modellregistry | **integriert (PR #23)** – gilt nach dessen Merge; Anbindung an den T-008-Lauf ausstehend |
| `agent/T-008-folds` | T-008 Falten, Purge/Embargo, Warm-up (#4) | **integriert (PR #30)** – gilt nach Merge; Anbindung und historische Auswertung ausstehend |
| `agent/T-019-operating-rules` | T-019 (#14): verbindliche Betriebsregeln, Prüfung beim Start | **integriert (PR #27)** – gilt nach dessen Merge; Durchsetzung im Paper-/Livebetrieb teilweise geplant |
| `agent/T-008-cost-schedule` | Kostentabelle SPY/QQQ mit gemessenen Spreads (Vorbedingung T-008) | **integriert (PR #28)** – gilt nach dessen Merge; Engine noch nicht angebunden |
| `agent/T-009-review-tiers` | T-009 (#16): Review nach Risikoklasse K/U, Merge-Diff-Prüfung, täglicher Sammel-PR | **integriert (PR #32)** – gilt nach dessen Merge |

## Funktionen

| Funktion | Zustand | Nachweis / Einschränkung |
|---|---|---|
| `make check`, CI, Secret-Scan, Linkprüfung | getestet | GitHub Actions bei Push/PR |
| Konfiguration mit harten Risikoobergrenzen | getestet | `tests/test_risk_execution.py` |
| 15-Minuten-CSV-Import, Qualität, Resampling | getestet | `tests/test_intraday_data.py`; kein Parquet, kein Feiertagskalender |
| Kostenmodell (Spread, Slippage, Gebühren, Mindestgebühr, Finanzierung) | getestet | Werte sind Annahmen, keine gemessenen Kosten |
| Risiko-Engine (0,20 %/Trade, 1 % offenes Risiko, 20 %/Instrument, DD 5/8/10 %) | getestet | ADR 0002 |
| Intraday-Engine, Trendfolge, Mean Reversion, Benchmarks, Stressszenarien | getestet | `tests/test_intraday_engine.py`; Beispielreport `docs/reports/` |
| Experimentregister | getestet | `tests/test_experiment_registry.py`, EXP-0001; IDs je Aufgabe (`EXP-T009-001`) |
| Offline-Paper-Demo mit Neustart und Kill-Switch | implementiert | nur Demo-Ereignisse, kein 15-Minuten-Betrieb |
| Persistenter Orderzustandsautomat und Fake-Broker-Abgleich | getestet | T-005 (#1), PR #26; `tests/test_order_manager.py`. Periodischer Abgleich im Paper-Dienst folgt mit T-006. |
| Paper-Betrieb 15 min, Modi backtest/paper/shadow/live | geplant | T-006 (#2) |
| Live-Trading | **gesperrt** | kein Live-Transport im Code; `mode = "offline"` wird erzwungen |
| Dashboard: Experimentregister und Intraday-Reports | integriert mit PR #24 | `tests/test_report_index.py` |
| Dashboard Betriebsansicht | geplant | T-007 (#3) (Research-Dashboard vorhanden) |
| Walk-forward + Champion-Challenger 15 min, Holdout-Kontrolle | geplant | T-008 (#4) |
| Dezimalbuchhaltung, FX | geplant | T-011 (#6) |
| Broker-/Datenadapter, Alpaca-Preflight | geplant | T-012 (#7): Instrumentenfrage entschieden (US-ETFs, ADR 0005 in #21); Preflight offen |
| Live-Voraussetzungen §18.4–18.10 | geplant | T-013 (#8) bis T-016 (#11), T-019 (#14) |
| PostgreSQL/Parquet | geplant | T-017 (#12) (Entscheidung offen, ADR 0001 zurückgestellt) |
| Mehrjährige Intraday-Daten | review | T-018 (#13), PR #21: SPY/QQQ 2016–2026 über Alpaca (kostenlos) |

## Ergebnisse

Mit den verfügbaren Daten (DAX, 165 Handelstage) wurde **keine belastbare profitable Strategie gefunden**
(EXP-0001, `docs/research/EXP-0001.md`). Versuche gesamt im Register: siehe `python3 -m localtrader experiments`.

## Offene Konflikte / Benutzerentscheidungen

1. **Speicher (T-017):** PostgreSQL/Parquet laut Spec §4 vs. derzeit SQLite/CSV (ADR 0001).

Entschieden 2026-11-17/18: Instrument frei wählbar → US-Index-ETFs mit Alpaca-Daten (T-012/T-018,
ADR 0005); grüne CI am geprüften Head-SHA gilt als `make check`-Nachweis (AGENTS.md §0).

## Nächste Ziele (Reihenfolge)

1. Integration von #21 und #25 nach Behebung der offenen Review-Befunde.
2. Methodenreview von #22, danach T-008-Implementierung (High) auf den ETF-Daten.
3. T-011 Buchhaltung in EUR (High).
4. T-006 Paper-Betrieb, danach T-007 Betriebsansicht, T-010 (#5) Abnahme.
