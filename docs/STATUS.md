# Status

Stand: 2026-11-17. Kennzeichnung: **geplant / implementiert / getestet / integriert / blockiert**.
„Integriert" bedeutet: in `main` gemergt. Aufgaben und Zuständigkeiten: GitHub Issues (Label = Zustand).

## Integrationsstand

| Branch | Inhalt | Zustand |
|---|---|---|
| `main` | Notebook von 2021 | integriert (Altstand) |
| `dev/trading-system` | T-001 bis T-004 (#15) | getestet, CI grün, PR #17 offen – **wartet auf unabhängiges Review** (Risiko, Ausführung) |
| `agent/T-009-agent-collaboration` | T-009 (#16) | getestet, PR #18 offen (basiert auf `dev/trading-system`) |

## Funktionen

| Funktion | Zustand | Nachweis / Einschränkung |
|---|---|---|
| `make check`, CI, Secret-Scan, Linkprüfung | getestet | GitHub Actions bei Push/PR |
| Konfiguration mit harten Risikoobergrenzen | getestet | `tests/test_risk_execution.py` |
| 15-Minuten-CSV-Import, Qualität, Resampling | getestet | `tests/test_intraday_data.py`; kein Parquet, kein Feiertagskalender |
| Kostenmodell (Spread, Slippage, Gebühren, Mindestgebühr, Finanzierung) | getestet | Werte sind Annahmen, keine gemessenen Kosten |
| Risiko-Engine (0,20 %/Trade, 1 % offenes Risiko, 20 %/Instrument, DD 5/8/10 %) | getestet | ADR 0002 |
| Intraday-Engine, Trendfolge, Mean Reversion, Benchmarks, Stressszenarien | getestet | `tests/test_intraday_engine.py`; Beispielreport `docs/reports/` |
| Experimentregister | getestet | `tests/test_experiment_registry.py`, EXP-0001 |
| Offline-Paper-Demo mit Neustart und Kill-Switch | implementiert | nur Demo-Ereignisse, kein 15-Minuten-Betrieb |
| Persistenter Orderzustandsautomat, Brokerabgleich | geplant | T-005 (#1) |
| Paper-Betrieb 15 min, Modi backtest/paper/shadow/live | geplant | T-006 (#2) |
| Live-Trading | **gesperrt** | kein Live-Transport im Code; `mode = "offline"` wird erzwungen |
| Dashboard Betriebsansicht | geplant | T-007 (#3) (Research-Dashboard vorhanden) |
| Walk-forward + Champion-Challenger 15 min, Holdout-Kontrolle | geplant | T-008 (#4) |
| Dezimalbuchhaltung, FX | geplant | T-011 (#6) |
| Broker-/Datenadapter, Alpaca-Preflight | blockiert | T-012 (#7): Konflikt Instrument (DAX) vs. Broker (Alpaca, US-Märkte) – Benutzerentscheidung nötig |
| Live-Voraussetzungen §18.4–18.10 | geplant | T-013 (#8) bis T-016 (#11), T-019 (#14) |
| PostgreSQL/Parquet | geplant | T-017 (#12) (Entscheidung offen, ADR 0001 zurückgestellt) |
| Mehrjährige Intraday-Daten | blockiert | T-018 (#13): kein Datenkauf ohne Freigabe |

## Ergebnisse

Mit den verfügbaren Daten (DAX, 165 Handelstage) wurde **keine belastbare profitable Strategie gefunden**
(EXP-0001, `docs/research/EXP-0001.md`). Versuche gesamt im Register: siehe `python3 -m localtrader experiments`.

## Offene Konflikte / Benutzerentscheidungen

1. **Broker vs. Instrument (T-012):** Spec §18.2 nennt Alpaca; Alpaca handelt US-Aktien/ETFs/Krypto,
   nicht den DAX-CFD, für den die einzigen 15-Minuten-Daten vorliegen. Optionen: US-ETFs mit Alpaca-Daten,
   oder ein anderer Broker für DAX. Nicht stillschweigend entschieden.
2. **Datenbasis (T-018):** Für belastbare Aussagen fehlen mehrjährige Intraday-Daten.
3. **Speicher (T-017):** PostgreSQL/Parquet laut Spec §4 vs. derzeit SQLite/CSV (ADR 0001).

## Nächste Ziele (Reihenfolge)

1. Review und Integration von PR `dev/trading-system`.
2. T-005 Orderzustandsautomat (kritisch, höhere Denkstufe, separates Review).
3. T-008 Walk-forward/Champion-Challenger (parallel möglich; kritisch, separates Review).
4. T-006 Paper-Betrieb, danach T-007 Dashboard, T-010 (#5) Abnahme.
