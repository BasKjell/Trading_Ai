# Local Paper Trader

Eine lokale, reproduzierbare Research- und Paper-Trading-Plattform gemäß [`CODEX_MASTER_PROMPT.md`](CODEX_MASTER_PROMPT.md). Der MVP enthält bewusst **keinen Live-Trading-Transport** und startet ausschließlich offline. Er ist Forschungssoftware, keine Anlageberatung und kein Renditeversprechen.

## Sicherheitsprofil

- kein Echtgeldmodus, keine Auszahlungen, kein Margin und keine Shorts;
- Strategien erzeugen nur Signale; die zentrale Risiko-Engine genehmigt Größen deterministisch;
- 0,25 % Standardrisiko pro Trade, hart auf 0,5 % begrenzt;
- Drawdown-Leiter bei 8 %, 12 % und 15 %, Tagesverlustlimit 1,5 %;
- unbekannte Modi, schlechte Daten und Positionsabweichungen führen zu einer Ablehnung;
- ein Broker-Endpunktwächter akzeptiert ausschließlich explizit bestätigte Paper-/Sandbox-Hosts; es existiert kein Netzwerktransport.

## Enthaltene Strategien

- ETF-Trendfolge: langfristiger Trend, mittelfristiger Trend, Ausbruch, ATR-Stop;
- Aktien-Momentum: reproduzierbares Large-Cap-Universum, Trendfilter, Relative-Stärke-Rang, Top-N;
- BTC-/ETH-Trendfolge: Spot, Long/Cash, Trend/Ausbruch, ATR-Stop;
- Multi-Asset-Trend: Long/Cash-Ranking über liquide, ungehebelte Spot- und ETF-Proxys;
- News-Momentum, News-Underreaction, Shortseller-Intelligence und Crisis/Event als fail-closed Shadow-Spezifikationen. Diese vier handeln nicht, solange echte Point-in-time-Ereignisdaten fehlen.

Die mitgelieferten Offline-Daten sind synthetisch. Zusätzlich kann die kontrollierte Datenwarteschlange historische Alpaca-Bars laden. Das heutige Aktienuniversum hat Survivorship Bias; entsprechende Resultate bleiben vorläufig.

## Lokale Befehle

Python 3.12 oder neuer:

```bash
python -m unittest discover -s tests -v
python -m localtrader backtest --days 1000 --output reports/baseline
python -m localtrader backtest --days 1000 --cost-multiplier 2 --output reports/stress_2x
python -m localtrader paper-demo
python -m localtrader kill-switch on
```

Mit installiertem Entwicklungs-Extra ist zusätzlich `python -m pytest` möglich:

```bash
python -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/python -m pytest
```

Docker:

```bash
docker compose config
docker compose build
docker compose run --rm tests
docker compose run --rm backtest
docker compose run --rm paper-demo
```

## Ergebnisse

Jeder Backtest schreibt:

- `summary.json` mit Kennzahlen, Benchmark und Warnungen;
- `equity.csv`;
- `equity.svg`, `drawdown.svg`, `monthly_returns.svg`, `annual_returns.svg`;
- `config_snapshot.toml` und `dataset_manifest.json` mit Datenhash.

Baseline und 2×/3×-Kostenstress verwenden identische Signale und dieselbe Risiko-/Fill-Logik. Signale nach einem Schlusskurs werden frühestens in der nächsten Periode zum Eröffnungskurs plus Kosten ausgeführt.

## Architektur

`data → strategies → risk → portfolio → execution → backtest/paper → reporting`

Domänenmodelle befinden sich in `localtrader/domain.py`. Der Offline-Paper-Zustand ist wiederanlauffähig, verarbeitet Ereignisse idempotent und protokolliert Entscheidungen in JSONL. News-, Shortseller- und Krisenfamilien sind als validierte Shadow-Spezifikationen und im Strategiekatalog vorbereitet; Event-Ingestion, Point-in-time-Datensätze und belastbare Event-Backtests fehlen noch.

## Grenzen

- Synthetische Daten belegen keine reale Performance.
- Der vereinfachte Börsenkalender berücksichtigt Wochenenden, aber noch keine Feiertagsbibliothek.
- Corporate Actions und Delistings benötigen einen Point-in-time-Datenanbieter.
- Die Trade-Zuordnung in den MVP-Kennzahlen ist FIFO und bildet Teilpositionen nur vereinfacht ab.
- Es existiert noch kein Walk-forward-Parameterexperiment auf echten Daten und kein finales Holdout.

Siehe [`docs/DATA_ACCESS.md`](docs/DATA_ACCESS.md) für später benötigte Datenzugänge.

## Lokaler Ollama-Research-Agent

Die Research-Umgebung kann installierte Ollama-Modelle ausschließlich über `127.0.0.1:11434` verwenden. Das Modell erzeugt validierte JSON-Spezifikationen statt ausführbarem Code. Eigene Stop-Funktionen sind als begrenzte mathematische RPN-Ausdrücke möglich. Ergebnisse, Vorschläge und der Research-Champion werden in SQLite gespeichert; eine Paper-Freigabe erfolgt niemals automatisch.

```bash
make lab-bootstrap
make lab-search
make lab-status
```

Alternativ direkt:

```bash
python -m localtrader ollama-search --model qwen3:8b --iterations 3 --days 900
```

Mit echten, vom jeweiligen Vorschlag angeforderten Alpaca-Daten:

```bash
python -m localtrader ollama-search --model qwen3:8b --iterations 3 --auto-data --lookback-days 900
```

Dauerbetrieb bis `Ctrl+C`:

```bash
python -m localtrader ollama-search --model qwen3-coder:30b --continuous --auto-data --lookback-days 900
```

Jede abgeschlossene Variante wird sofort gespeichert. Der Dauerlauf behält nur die letzten 100 Resultate im Arbeitsspeicher; die vollständige Historie bleibt in SQLite. `Ctrl+C` beendet kontrolliert und setzt einen gerade unterbrochenen Download zurück auf `pending`.

Standardmäßig verteilt die Suche ihre Auswertungen auf alle testbaren Preisfamilien, statt nur Krypto- oder Multi-Asset-Varianten zu erzeugen. Eine Einschränkung ist möglich, beispielsweise mit `--families etf_trend,stock_momentum`. Eventfamilien werden nicht in diese Suche gemischt, solange die dafür verlangten Point-in-time-Daten fehlen.

Das LLM erhält keine Zugangsdaten und keinen freien Netzwerkzugriff. Es erzeugt eine Strategie mit Symbolen und Zeitrahmen; die Anwendung übersetzt sie in eine geprüfte Datenanforderung. Proaktiv gelten 180 Abrufe/Minute. Alpaca-Cooldowns aus `Retry-After` beziehungsweise `X-RateLimit-Reset` werden abgewartet; nach ausgeschöpften Versuchen bleibt die Anfrage persistent in SQLite und wird mit `python -m localtrader data-worker` nach Ablauf des Cooldowns wieder aufgenommen. Rohdaten, normalisierte JSONL-Daten, Manifest und SHA-256-Hash liegen lokal unter `data/` und werden nicht versioniert.

Die ausführliche, auch für das lokale Modell geschriebene Anleitung steht in [`OLLAMA_AGENT_GUIDE.md`](OLLAMA_AGENT_GUIDE.md).

## Live-Dashboard

Das lokale, read-only Dashboard vergleicht Ollama-Modelle, Varianten, Scores, Rendite, Sharpe, Drawdown, Gate-Erfolgsquote, Ablehnungsgründe und Datenaktivität. Zusätzlich zeigt es die Abdeckung aller acht Strategiefamilien des Hauptanforderungsdokuments samt P0/P1/P2- und Shadow-Status. Es aktualisiert sich während der Suche alle fünf Sekunden und bindet ausschließlich an den lokalen Rechner.

Die robusten Research-Gates verlangen positive Median-Rendite, positiven Median-Sharpe, positives Ergebnis bei 3× Kosten, mindestens fünf Median-Trades, mindestens 55 % positive Walk-forward-Folds und weniger als 15 % Drawdown. Ältere Champions, die diese Regeln nicht mehr erfüllen, werden im Dashboard als veraltet markiert. Verhaltensgleiche Strategien werden unabhängig von Name und Beschreibung vor einem erneuten Download beziehungsweise Backtest abgelehnt.

```bash
PYTHONPATH=. .venv/bin/python -m localtrader dashboard
```

Danach im Browser öffnen: `http://127.0.0.1:8501`. Alternativ genügt `make dashboard`. Die laufende Ollama-Suche kann parallel in einem zweiten Terminal bleiben.
