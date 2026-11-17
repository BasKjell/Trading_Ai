# Architektur- und Reproduzierbarkeitsnotizen

## Entscheidungsfluss

1. Ein Adapter liefert timezone-aware Bars und Datenmetadaten.
2. Die Qualitätsprüfung lehnt Duplikate, unsortierte Zeitpunkte, unplausible OHLC-Werte und negative Volumina ab.
3. Strategien erzeugen reine `Signal`-Objekte ohne Executor-Zugriff.
4. Aus dem Signal entsteht ein `OrderIntent` mit stabiler Client-ID.
5. Die Risiko-Engine prüft Stop-Distanz, Cash, Konzentration, Exposure, Tagesverlust, Drawdown, Datenstatus, Pause und Positionsabgleich.
6. Das Fill-Modell berechnet Spread, Slippage und Gebühren. Backtest und Paper-Simulator verwenden dieselbe Implementierung.
7. Portfolio, Audit und Berichte werden aktualisiert.

## Zeitlogik

Ein Tagessignal entsteht erst am Ende der Bar. Die früheste Ausführung liegt danach; der Backtest füllt an der nächsten verfügbaren Bar zum Open inklusive Kosten. Schutzstops berücksichtigen Gaps durch `min(Open, Stop)` bei Long-Positionen.

## Reproduzierbarkeit

Der Seed, der SHA-256-Datenhash und der SHA-256-Konfigurationshash werden gespeichert. Jeder Bericht enthält einen Konfigurationssnapshot und ein Dataset-Manifest. Die synthetische Quelle ist ausdrücklich als solche markiert.

`localtrader.research` stellt chronologische, expandierende Walk-forward-Splits mit expliziten Purge- und Embargo-Zonen sowie eine getrennte finale Holdout-Grenze bereit. Der synthetische Smoke-Lauf nimmt keine Parameterwahl vor; ein echtes finales Holdout wird erst nach Anbindung eines Point-in-time-Datensatzes einmalig ausgewertet.

## Bewusst offene P1/P2-Punkte

- vollständiger börsenspezifischer Feiertagskalender;
- persistente partielle Orders und detaillierter Positionsabgleich;
- echte Point-in-time-Datenadapter und Walk-forward-Experimentmanager;
- Champion/Challenger-Registry mit manuellem Promotion-Gate;
- ML ausschließlich als Shadow-Filter;
- News-, Shortseller- und Crisis-Research.

## Lokale LLM-Forschungsumgebung

Ollama ist nur über einen Loopback-Endpunkt zugelassen. Structured Outputs liefern eine deklarative Strategie gemäß festem JSON-Schema. Die Anwendung validiert Universum, Indikatoren, Regeln und sichere Stop-Ausdrücke, bevor ein Kandidat ausgeführt wird. Es gibt keine `exec`-, Shell-, Import- oder freie SQL-Schnittstelle.

Die SQLite-Datenbank trennt LLM-Vorschläge von engine-generierten Evaluationen. Automatisch kann nur der `research_champion` wechseln; `paper_approved` bleibt ohne manuelle Freigabe immer `false`.
