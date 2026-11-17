# Betriebsanweisung für den lokalen Ollama-Research-Agenten

Diese Datei ist die maßgebliche Arbeitsanweisung für ein lokales Ollama-Modell. Das System ist eine Research- und Paper-Trading-Umgebung, kein Echtgeld-Trader und kein Renditeversprechen.

## 1. Nicht verhandelbare Grenzen

1. Erzeuge ausschließlich deklarative JSON-Strategien. Erzeuge und starte keinen Python-, Shell- oder SQL-Code.
2. Keine Shorts, kein Hebel, keine Margin, keine Futures und keine Optionen.
3. Keine Orders. Das LLM schlägt nur Research-Kandidaten vor.
4. Jede Strategie muss durch Schema-, Daten-, Risiko-, Walk-forward- und Kostenprüfungen laufen.
5. Das finale Holdout wird während der Suche nicht verwendet.
6. Ein Research-Champion wird niemals automatisch für Paper-Trading freigegeben.
7. Keine erfundenen Kurse, Events oder Ergebnisse. Marktwerte kommen aus Datenadaptern; Kennzahlen kommen aus der Engine.
8. Kein freier Datenbank- oder SQL-Zugriff. Speicherung erfolgt nur über `ResearchDB`.

## 2. Repository-Karte

- `strategy_specs/`: versionierte Baselines und Shadow-Strategien.
- `localtrader/strategy_dsl.py`: erlaubte Indikatoren, Regeln und Stop-Ausdrücke.
- `localtrader/spec_backtest.py`: Backtest validierter JSON-Spezifikationen.
- `localtrader/strategy_lab.py`: Walk-forward-Läufe, Kostenstress, Score und Champion-Auswahl.
- `localtrader/ollama_lab.py`: ausschließlich lokale Ollama-Anbindung.
- `localtrader/research_db.py`: kontrollierte SQLite-Persistenz.
- `research_registry/research.db`: lokale Research-Datenbank.
- `research_registry/research_champion.json`: bester Research-Kandidat; nie automatisch Paper-approved.
- `config/default.toml`: harte Risikolimits.

## 3. Verfügbare Daten

Für Offline-Smoke-Tests existieren deterministische synthetische Tagesbars. Für Research kann die Anwendung historische Alpaca-Aktien-/ETF- und Kryptobars in `1Min`, `5Min`, `15Min`, `30Min`, `1Hour` oder `1Day` laden. Das LLM lädt nie selbst über beliebige URLs: Seine validierte Strategie wird in eng begrenzte `DataRequestSpec`-Objekte übersetzt.

| Symbol | Klasse | Modellierung |
|---|---|---|
| SPY, QQQ | US-Index-ETF | als Equity-Bar |
| AAPL, MSFT, NVDA | US-Large-Cap-Aktie | Equity |
| BTC-USD, ETH-USD | Krypto-Spot | 24/7 |
| EUR-USD | FX-Spot-Research | vereinfachte Tagesbar |
| GLD | Gold-ETF-Proxy | Rohstoff-Proxy |
| TLT | Treasury-ETF-Proxy | Anleihe-Proxy |

Alpaca unterstützt hier SPY, QQQ, AAPL, MSFT, NVDA, GLD, TLT sowie BTC-USD und ETH-USD. EUR-USD bleibt nur synthetisch, weil dieser Adapter kein FX lädt. Nicht vorhanden sind vollständige Point-in-time-Universen, Delistings, historisches Index-Membership, News, Filings, Shortseller-Berichte und Krisenereignisse. Entsprechende Shadow-Strategien müssen als `blocked_missing_data` erscheinen.

## 4. Erlaubte Strategiefamilien

- `etf_trend`: SPY/QQQ, Long/Cash.
- `stock_momentum`: AAPL/MSFT/NVDA, Long-only Ranking.
- `crypto_trend`: BTC/ETH Spot, Long/Cash.
- `multi_asset_trend`: freie Kombination aller im lokalen Datenkatalog vorhandenen Klassen.
- `news_momentum`, `news_underreaction`, `shortseller_intelligence`, `crisis_event`: nur Shadow, solange Point-in-time-Eventdaten fehlen.

## 5. Indikatoren und Regeln

Erlaubte Indikatoren:

- `sma`, `ema`
- `momentum`
- `atr`
- `rsi`
- `volatility`
- `breakout_high`, `breakout_low`
- `relative_volume`

Erlaubte Vergleiche: `>`, `>=`, `<`, `<=`, `crosses_above`, `crosses_below`.

Fenster liegen zwischen 2 und 500 Bars. Eine Strategie verwendet 2 bis 12 Indikatoren und maximal acht Entry- beziehungsweise Exit-Regeln.

## 6. Eigene Stop-Funktionen

`stop_expression` ist ein sicherer mathematischer Ausdruck in Reverse-Polish-Notation. Erlaubte Operatoren sind `+`, `-`, `*`, `/`, `min`, `max`. Zulässig sind nur Zahlen, OHLCV-Felder und definierte Indikator-Aliase.

Beispiele:

```json
["close", 3.0, "atr20", "*", "-"]
```

entspricht `close - 3 × ATR20`.

```json
["close", 0.94, "*", "close", 4.0, "atr20", "*", "-", "max"]
```

entspricht `max(close × 0,94, close - 4 × ATR20)`.

`stop_mode` ist `fixed` oder `trailing`. Ein Trailing-Stop darf bei Long-Positionen nur nach oben nachgezogen werden. Das Ergebnis muss positiv und kleiner als der aktuelle Kurs sein. Python-Funktionsnamen, Imports, Attribute und unbekannte Tokens werden abgelehnt.

## 7. Evaluationsprotokoll

Jeder handelbare Kandidat wird auf dem Development-Bereich geprüft:

1. chronologische expandierende Walk-forward-Fenster;
2. Purge und Embargo;
3. identische Risiko- und Fill-Engine;
4. Gebühren, Spread und Slippage;
5. Kostenmultiplikatoren 1×, 2× und 3×;
6. Mindestanzahl unabhängiger Trades;
7. positive Median-Rendite und positiver Median-Sharpe;
8. positive Median-Rendite auch im 3×-Kostenstress;
9. mindestens 55 % positive Walk-forward-Folds und mindestens fünf Median-Trades;
10. harte Ablehnung ab 15 % Drawdown;
11. finales Holdout bleibt reserviert und unberührt.

Der Score bevorzugt robuste Rendite und Sharpe und bestraft Drawdown sowie schlechte 3×-Kostenresultate. Ein höherer synthetischer Score ist kein Nachweis realer Profitabilität.

Die Suche gibt dem Modell die letzten kompakten Research-Ergebnisse als Feedback. Ein Verhaltens-Fingerprint ignoriert nur Name, Beschreibung und Version. Bereits getestete Kombinationen aus Universum, Zeitrahmen, Indikatoren, Fenstern, Regeln und Stop-Logik werden vor Datenabruf und Backtest als Duplikat verworfen. Eine reine Umbenennung zählt nicht als neue Strategie.

## 8. Datenbankregeln

SQLite-Tabellen:

- `strategy_specs`: validierte Strategie-JSONs;
- `evaluations`: ausschließlich von der Engine erzeugte Resultate;
- `llm_events`: validierte Vorschläge, Ablehnungen und Research-Notizen;
- `data_catalog`: verfügbare Instrumente und Klassen.
- `data_requests`: geprüfte Anfragen, Versuchszähler, Status und Cooldown;
- `datasets`: Manifest, lokaler Pfad und Datenhash abgeschlossener Downloads.

Das LLM darf über die Anwendung Vorschläge und Datenanforderungen auslösen. Nur der Downloader darf validierte Marktdaten und nur die Backtest-Engine darf Evaluationen schreiben. Es gibt keinen freien SQL-, Datei- oder Netzwerkzugriff für Modellantworten.

Der Downloader begrenzt sich auf 180 Requests/Minute (Alpaca Basic: 200), paginiert mit maximal 10.000 Bars je Seite und begrenzt eine Anforderung standardmäßig auf geschätzt eine Million Bars. Bei HTTP 429/5xx werden `Retry-After` und `X-RateLimit-Reset` beachtet. Nach sechs Netzwerkversuchen geht die persistente Anfrage für fünf Minuten in Cooldown; `data-worker` nimmt sie danach wieder auf.

## 9. Befehle

Status und Modelle:

```bash
python -m localtrader lab-status
```

Live-Dashboard für Modell- und Strategieperformance:

```bash
python -m localtrader dashboard
```

Das Dashboard ist read-only, läuft unter `http://127.0.0.1:8501`, aktualisiert sich alle fünf Sekunden und darf nicht an eine externe Netzwerkschnittstelle gebunden werden.

Alle Baselines und Shadow-Familien prüfen:

```bash
python -m localtrader evaluate-strategies --days 900
```

Drei neue Kandidaten mit dem schnellen Modell suchen:

```bash
python -m localtrader ollama-search --model qwen3:8b --iterations 3 --days 900
```

Gründlichere, langsamere Suche:

```bash
python -m localtrader ollama-search --model qwen3-coder:30b --iterations 10 --days 900
```

Automatische Suche auf Alpaca-Historie:

```bash
python -m localtrader ollama-search --model qwen3:8b --iterations 3 --auto-data --lookback-days 900 --feed sip
```

Unbegrenzte Champion-/Challenger-Suche bis zum manuellen Abbruch:

```bash
python -m localtrader ollama-search --model qwen3-coder:30b --continuous --auto-data --lookback-days 900 --feed sip
```

Die Suche balanciert standardmäßig ETF-Trendfolge, Aktien-Momentum, BTC-/ETH-Trendfolge und Multi-Asset-Trend. Mit `--families etf_trend,stock_momentum` kann der sichere Preis-Research-Katalog eingeschränkt werden. News-, Shortseller- und Crisis-Strategien bleiben Shadow-Module, bis echte Point-in-time-Eventdaten vorhanden sind; Ollama darf dafür keine Ereignisse erfinden.

Optional begrenzt `--pause-seconds 5` die Startfrequenz zwischen zwei Iterationen. `Ctrl+C` beendet kontrolliert; abgeschlossene Kandidaten bleiben gespeichert und ein unterbrochener Download wird wieder auf `pending` gesetzt.

Manuelle Queue-Nutzung und Wiederaufnahme eines abgelaufenen Cooldowns:

```bash
python -m localtrader data-request --endpoint-type stocks --symbols SPY,QQQ --timeframe 15Min --start 2025-01-01T00:00:00Z --end 2026-01-01T00:00:00Z --purpose research:intraday
python -m localtrader data-worker
```

Jede Iteration wird sofort gespeichert. Ein Abbruch verliert bereits abgeschlossene Kandidaten nicht.

## 10. Definition eines guten Vorschlags

- wenige, verständliche Regeln;
- plausible ökonomische Begründung;
- robuste statt punktgenau optimierte Fenster;
- ATR-basierter oder mathematisch klar begrenzter Stop;
- geringe unnötige Handelsfrequenz;
- genügend Trades für eine erste Aussage;
- Verbesserung auch bei 2× und 3× Kosten;
- keine Nutzung des Holdouts;
- keine automatische Paper-Promotion.
