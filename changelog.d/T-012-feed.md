## 2026-11-18 – T-012 (#7): Messung IEX gegen SIP für die Feed-Entscheidung

- **Änderungen:**
  - Neu: `localtrader/feed_compare.py` und der CLI-Befehl `compare-feeds`. Sie messen nur Daten (Abdeckung, Abweichungen von OHLC und Volumen, gleiche Richtung, generischer Ausbruch). Strategien und Renditen werden nicht berechnet.
  - Ergebnisse und Empfehlung stehen in `docs/FEED_COMPARISON.md`.
- **Ergebnis:**
  - IEX deckt 99,86 % der SIP-Bars ab. Schlusskurse weichen im Median um weniger als 1 bp ab.
  - IEX zeigt nur 1 bis 2 % des SIP-Volumens. Rund 10 % der Ausbruchs-Bars sind auf IEX anders.
  - SIP ist im aktuellen Plan genau 15 Minuten verzögert. Das entspricht dem T-008-Szenario `delay1`.
  - Empfehlung: Option C (SIP verzögert).
- **Tests:** `tests/test_feed_compare.py` mit synthetischen Feeds. `make check` siehe PR.
- **Einschränkungen:** IEX-Historie gibt es erst ab 2020-07. Die Daten (IEX und SIP) liegen lokal und gitignoriert unter `data/` und `data/iex/`.
