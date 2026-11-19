# Architektur

Statuskennzeichnung: **geplant / implementiert / getestet / integriert / blockiert**
(„integriert" = in `main` gemergt). Stand 2026-11-17: Noch nichts ist in `main` integriert; alles
Folgende ist auf `dev/trading-system` **getestet** (PR zur Integration offen).

Ein modularer Python-Dienst (Paket `localtrader`), Laufzeit nur Standardbibliothek (ADR 0001).

## Komponenten und Verantwortlichkeiten

| Bereich (Spec §4) | Modul | Status | Verantwortung / Schnittstelle |
|---|---|---|---|
| Konfiguration und Validierung | `config.py`, `config/default.toml` | getestet | `load_config()` → `AppConfig`; `validate_risk()` mit harten Obergrenzen (`HARD_LIMITS`) |
| Domänenmodelle | `domain.py` | getestet | `Bar`, `Signal`, `OrderIntent`, `RiskDecision`, `Fill`, `Position`, `PortfolioSnapshot`; alle Zeiten tz-aware |
| Marktdatenaufnahme 15 min | `intraday_data.py` | getestet | `import_csv(CsvImportSpec, SessionSpec)` → `ImportedDataset(bars, manifest, after_gap)`; `resample()`; `synthetic_intraday()` |
| Datenqualität | `intraday_data.py`, `data.py` | getestet | Verwerfen ungültiger Zeilen, Lückenmeldung ohne Auffüllen, `DatasetManifest` mit Datenversion |
| Feature-Berechnung | `intraday_strategies.py` (`sma`, `atr`, `efficiency_ratio`) | getestet | reine Funktionen über abgeschlossene Bars |
| Strategien/Signale | `intraday_strategies.py` | getestet | Protokoll `IntradayStrategy.evaluate(StrategyContext) -> Signal \| None` |
| Risikosteuerung | `risk.py` | getestet | `RiskEngine.decide(intent, portfolio, prices, now, day_start_equity, bar_volume)` → `RiskDecision` |
| Portfolio/Buchhaltung | `portfolio.py` | getestet | `apply_fill`, `charge`, `deposit`/`withdraw`, `snapshot` (Float-Arithmetik; Dezimal: geplant T-011) |
| Ausführung/Kosten | `execution.py` | getestet | `FillModel` mit `CostProfile` je Instrument; gemeinsam für Backtest und Paper |
| Backtesting 15 min | `intraday_engine.py` | getestet | `IntradayEngine.run(datasets)` → `EngineResult`; `compute_metrics`, `buy_and_hold` |
| Reporting | `intraday_report.py`, `reporting.py` | getestet | `write_intraday_report`, `run_scenarios` (Kosten-, Spread-, Verzögerungs-, Parameter-, Phasenstress) |
| Experimentregister | `experiment_registry.py`, `experiments/` | getestet | eine unveränderliche JSON-Datei je Experiment |
| Orderverwaltung (Zustandsautomat) | – | geplant (T-005) | persistente Orderzustände, Timeouts, Abgleich |
| Brokeradapter | `broker_guard.py`, `alpaca_check.py` | implementiert (nur Endpunktwächter und lesender Check) | Ordertransport geplant (T-012) |
| Paper-Betrieb | `paper.py` | implementiert (Demo-Ereignisse, Neustart, Kill-Switch) | 15-Minuten-Schleife geplant (T-006) |
| Betriebsmodi backtest/paper/shadow/live | – | geplant (T-006) | Live technisch gesperrt: kein Live-Transport, `mode = "offline"` erzwungen |
| Forschung (Tages-Lab, Ollama) | `strategy_lab.py`, `strategy_dsl.py`, `ollama_lab.py`, `research*.py` | implementiert (Tagesdaten) | Walk-forward/Champion-Challenger für 15 min geplant (T-008) |
| Dashboard | `dashboard.py` | implementiert (Research-Ansicht) | Betriebsansicht geplant (T-007) |
| Legacy-Tagesbacktest | `backtest.py`, `spec_backtest.py`, `strategies.py`, `data.py` | getestet | synthetische Tagesdaten, Research-Lab |

## Entscheidungsfluss (15 Minuten)

```
CSV → import_csv (UTC, Qualität, Manifest) → IntradayEngine
  je Bar-Eröffnung: fällige Orders → RiskEngine.decide → FillModel.fill → Portfolio.apply_fill
                    Schutzstop in der Bar → min(Open, Stop)
  je Bar-Schluss:   History/Stundenbars aktualisieren → Snapshot → Drawdown-Leiter
                    → Strategie.evaluate (nur in Handelszeit, nicht nach Datenlücke) → Signal (pending)
→ EngineResult → compute_metrics / write_intraday_report / run_scenarios
```

Zeitregeln, Stops, Teilausführungen und Finanzierung: Docstring von `intraday_engine.py` und ADR 0003.
Risikodefinitionen (High-Water-Mark, Drawdown, Freigabe): Docstring von `risk.py` und ADR 0002.

## Persistenz

| Daten | Ablage | Status |
|---|---|---|
| Paper-Zustand, Audit | `state/*.json`, `state/*.jsonl` (nicht versioniert) | implementiert |
| Research-Lab | `research_registry/research.db` (SQLite, nicht versioniert) | implementiert |
| Experimentregister | `experiments/EXP-T<nnn>-<lfd>.json` (versioniert, unveränderlich, IDs je Aufgabe) | getestet |
| Orders, Trades, Zustände in PostgreSQL | – | geplant (T-017, Entscheidung offen) |
| Marktdaten Parquet | – | geplant (T-017) |

## Reproduzierbarkeit

Seed, Datenversion (SHA-256 über akzeptierte Bars), Konfigurations-Digest und Parameter stehen in
jedem Report (`summary.json`) und jedem Registereintrag.

## Lokaler Coding-Runner (T-020)

`scripts/coding_runner.py` verwendet die GitHub CLI für Diagnose und Jobvorschau. Die
Worker-Ausführung ist technisch gesperrt. Vor einer Freischaltung ist ein getrennt geprüfter
Ausführungsbereich erforderlich. Siehe [CODING_RUNNER.md](CODING_RUNNER.md).

## Entwicklungsorchestrator (T-024)

**Implementiert und offline getestet.** Separater Python-Steuerprozess mit SQLite für den
Entwicklungsablauf. Isolierte Container bearbeiten Git-Snapshots. Nur der Controller führt
Schreibaktionen auf GitHub aus. Ausführung und automatischer Merge sind in der mitgelieferten
Konfiguration deaktiviert. Siehe [DEV_ORCHESTRATOR.md](DEV_ORCHESTRATOR.md).
