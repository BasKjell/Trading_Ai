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
| Duplikat, nicht auf Raster liegender Zeitstempel, OHLC-Widerspruch, Preis ≤ 0, negatives Volumen, unlesbare Zeile | Zeile verworfen, gezählt |
| Lücke innerhalb einer Handelssitzung | gemeldet, **nicht aufgefüllt**; erste Bar danach blockiert neue Einstiege |
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
