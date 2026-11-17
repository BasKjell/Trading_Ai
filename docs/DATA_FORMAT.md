# Intraday-Datenformat und Datenqualität

Modul: `localtrader/intraday_data.py`.

## Importformat (CSV)

Zwei Varianten, Spaltennamen ohne Groß-/Kleinschreibung:

| Variante | Spalten | Zeitstempel |
|---|---|---|
| MetaTrader | `Date,Time,Open,High,Low,Close,Volume` | `2020.10.06`, `14:00` in der angegebenen Quellzeitzone |
| ISO | `timestamp,open,high,low,close,volume` | ISO-8601; ohne Offset gilt die Quellzeitzone |

- Zeitstempel bezeichnen den **Beginn** einer Bar. `end = start + Intervall`.
- `received_at` (Verfügbarkeitszeit) = `end` + optionale Verzögerung (`availability_delay_seconds`).
  Strategien sehen eine Bar erst ab `received_at`.
- Intern ist alles UTC. Die Quellzeitzone ist Pflichtangabe (`source_timezone`).
- Parquet wird noch nicht unterstützt (keine Abhängigkeit installiert, siehe ADR 0001).

## Qualitätsregeln

| Befund | Behandlung |
|---|---|
| Duplikat, nicht auf Raster liegender Zeitstempel, OHLC-Widerspruch, Preis ≤ 0, negatives Volumen, NaN/±Inf in einem OHLCV-Feld, unlesbare Zeile | Zeile verworfen, gezählt |
| Lücke innerhalb einer Handelssitzung | gemeldet, **nicht aufgefüllt**; erste Bar danach blockiert neue Einstiege (auch bereits ausstehende) |
| Lücke über Nacht/Wochenende | erwartet, nicht gemeldet |
| Sprung Open/vorheriger Close > 3 % | gemeldet, nicht entfernt |

Jeder Import erzeugt ein `DatasetManifest` mit Instrument, Handelsplatz, Quelle, Quellzeitzone,
Intervall, inhaltlicher Datenversion (SHA-256 über die akzeptierten Bars), Datei-Hash und Qualitätsbericht.

## Höhere Zeitintervalle

`resample()` bildet 1-Stunden- oder Tagesbars in einer lokalen Zeitzone. Das Ende einer aggregierten Bar
ist das Ende ihrer letzten Teilbar; sie ist also erst verfügbar, wenn alle Teile bekannt sind.
Buckets werden nie aufgefüllt.

## Handelskalender

Es ist keine Feiertagsbibliothek installiert. Handelssitzungen werden pro Instrument als lokale
Uhrzeiten konfiguriert (z. B. Xetra 09:00–17:30 Europe/Berlin, Montag–Freitag). Feiertage ergeben sich
aus den Daten: Fehlt ein ganzer Tag, gibt es keine Bars und damit keine Signale.

## Versionierte Beispieldaten: `data/DAX3015.csv`

| Eigenschaft | Wert |
|---|---|
| Inhalt | DAX-CFD/Indexkurs, 15 Minuten, 2020-10-06 bis 2021-05-28 |
| Herkunft | unbekannt (aus dem Repository von 2021); Spaltenformat entspricht einem MetaTrader-Export |
| Quellzeitzone | **Annahme `Europe/Athens`** (übliche MT-Serverzeit). Beleg: Das Volumenmaximum liegt nach Umrechnung bei 09:00 Berlin (Xetra-Eröffnung), der zweite Anstieg bei 15:30 Berlin (US-Eröffnung). |
| Volumen | Tick-Volumen des Anbieters, kein Börsenumsatz |
| Qualität | 14.584 Zeilen, 14.564 akzeptiert, 20 nicht auf dem 15-Minuten-Raster, 1 Lücke im Xetra-Handel (7 Bars), keine Duplikate |
| Corporate Actions | Indexkurs: keine Splits/Dividendenanpassung nötig. Für Einzelaktien/ETFs wären adjustierte Analysepreise und unadjustierte handelbare Preise getrennt zu führen. |

**Aussagekraft:** knapp acht Monate eines einzigen Instruments in einer überwiegend steigenden Marktphase.
Das reicht für technische Prüfungen, aber **nicht** für belastbare Aussagen zur Profitabilität.

## US-ETF-Daten von Alpaca (T-018, ADR 0005)

| Feld | Wert |
|---|---|
| Abruf | `python3 -m localtrader fetch-intraday --symbol SPY --env <pfad/zur/.env>` (ebenso QQQ) |
| Quelle | Alpaca Market Data v2, Feed SIP, `adjustment=all`, Paper-Key aus `.env` (kostenlos, Basic-Plan) |
| Datei | `data/<SYMBOL>_15m.csv` (Spalten `timestamp` in UTC ISO, `open,high,low,close,volume`), nicht versioniert |
| Herkunftsnachweis | `data/<SYMBOL>_15m.csv.manifest.json` (Request-ID, Abrufzeit, Bar-Zahlen, Datei-Hash) und Rohseiten unter `data/raw/alpaca/<request_id>/` |
| Inhalt | nur reguläre Handelszeit 09:30–16:00 New York |
| Holdout | Bars ab 2025-11-01 werden aus Forschungsläufen entfernt (`[holdout]`) |

Stand des Abrufs vom 2026-11-17:

| Symbol | Bars gesamt / Handelszeit | Zeitraum | Datenversion | Lücken in der Handelszeit | Sprünge > 3 % |
|---|---|---|---|---|---|
| SPY | 171.396 / 71.101 | 2016-01-04 bis 2026-11-17 | `521f66df08a5c5d8` | 1 (1 Bar) | 21 |
| QQQ | 168.642 / 71.096 | 2016-01-04 bis 2026-11-17 | `95c1721523a14210` | 6 (7 Bars) | 30 |

Keine Zeile verworfen. Sprünge sind überwiegend Eröffnungslücken nach Nachrichten/Wochenenden und bleiben
erhalten. Halbtage (z. B. vor Feiertagen) enden um 13:00 und werden nicht als Lücke erkannt.
