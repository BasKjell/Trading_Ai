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
| Abruf | `python3 -m localtrader fetch-intraday --symbol SPY --env <pfad/zur/.env>` (ebenso QQQ); lädt höchstens bis vor den Holdout-Start, ein späteres `--end` wird abgelehnt |
| Quelle | Alpaca Market Data v2, Feed SIP, Paper-Key aus `.env` (kostenlos, Basic-Plan) |
| Handelbare Preise | `data/<SYMBOL>_15m.csv`, `adjustment=raw`: Signale, Ausführung, Stops, Bewertung |
| Analysepreise | `data/<SYMBOL>_15m_adjusted.csv`, `adjustment=all`: nur Analyse, nie Ausführung |
| Dividenden | `data/<SYMBOL>_dividends.csv` (`ex_date,pay_date,amount,source`) aus Alpacas Corporate-Actions-Endpunkt; Gegenprobe gegen die aus Roh-/bereinigter Serie abgeleiteten Beträge (> 5 % Abweichung = Abbruch); Forderung am Ex-Tag, Cash am Zahltag, abzüglich 15 % Quellensteuer; Bindung an die Datenversion des Laufs |
| Splits | nicht unterstützt: Abruf bricht ab |
| Herkunftsnachweis | `data/<SYMBOL>_15m.csv.manifest.json` (Request-IDs, Abrufzeit, Datei-Hashes, Dividendenlücken) und Rohseiten unter `data/raw/alpaca/<request_id>/` |
| Handelszeit | 09:30–16:00 New York, an NYSE-Halbtagen bis 13:00 (`localtrader/calendars.py`); nach 2026-12-31 kein Handel |
| Holdout | Zeilen, die nicht vollständig vor 2025-11-01 verfügbar sind, werden beim Import vor jeder Prüfung verworfen; nur die Anzahl wird gemeldet. Der Herkunftshash umfasst dann nur die Forschungszeilen (`research-rows:<sha256>`), Dividenden ab dem Holdout-Tag werden nicht geladen; eine Änderung nur im Holdout-Teil lässt alle Forschungsausgaben unverändert |

Stand des Abrufs vom 2026-11-18, **nur Forschungszeitraum** (Benutzerentscheidung: keine Holdout-Daten in
Forschungsdateien; alle Dateien nicht versioniert):

| Symbol | Bars Handelszeit | Zeitraum | Dividenden | Dividendenlücken (Alpaca) |
|---|---|---|---|---|
| SPY | 64.069 | 2016-01-04 bis 2025-10-31 | 37 (14 Zahltage angenommen) | 2016-01..2016-06 (Ex-Tag März 2016), 2018-03..2018-09 (Juni 2018) |
| QQQ | 64.068 | 2016-01-04 bis 2025-10-31 | 40 (16 Zahltage angenommen) | keine; 2022-09-19 doppelt gelistet (späterer Zahltag verwendet, in Alpacas bereinigter Serie doppelt eingerechnet) |

Die Halbtage 2016–2025 im Kalender stimmen genau mit den Tagen überein, an denen das Volumen nach
13:00 New York auf nachbörsliches Niveau fällt; kein weiterer Tag zeigt dieses Muster.

## EUR/USD-Referenzkurse der EZB (T-011, Datenteil)

| Feld | Wert |
|---|---|
| Abruf | `python3 -m localtrader fetch-fx` (Standard ab 2015-12-01 bis heute) |
| Quelle | EZB Data Portal, Serie `EXR.D.USD.EUR.SP00.A` (USD je EUR, tägliche Referenzkurse), frei zugänglich, ohne Schlüssel |
| Datei | `data/EURUSD_ecb.csv` (`date,usd_per_eur,available_at`) + `.manifest.json` (Quelle, Hash, Qualitätsbericht), nicht versioniert |
| Verfügbarkeit | ab 16:00 Europe/Berlin am Kurstag (EZB legt die Kurse gegen 14:10 fest und veröffentlicht sie gegen 16:00); frühere Nutzung wäre Look-ahead |
| Abfrage | `FxSeries.rate_at(t)`: letzter bis `t` veröffentlichter Kurs; älter als 5 Tage → Fehler (fail closed) |
| Holdout | `read_fx_file(..., available_until=…)` lädt nur bis zur Grenze veröffentlichte Kurse |
| Prüfungen | nur finale Werte (`OBS_STATUS=A`), plausibel 0,5–2,5, keine widersprüchlichen Duplikate; Sprünge > 4 % und Lücken > 5 Tage werden gemeldet |

Stand 2026-11-18: 2.807 Kurse vom 2015-12-01 bis 2026-11-17, nichts verworfen, keine Sprünge > 4 %,
keine Lücke > 5 Tage. Für alle 70.853 SPY-Bars der Handelszeit liegt ein veröffentlichter Kurs vor;
das größte Kursalter beträgt 4 Tage 23 Stunden 45 Minuten (Ostern).

Der Referenzkurs ist ein indikativer Mittelkurs, kein handelbarer Kurs: Umtauschkosten des Brokers
(Spread, Gebühr) müssen getrennt modelliert werden (Kostentabelle).
