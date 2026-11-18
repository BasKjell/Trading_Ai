# Kostentabelle SPY/QQQ (Vorbedingung T-008)

Maschinenlesbar: [`config/cost_schedule.json`](../../config/cost_schedule.json), geprüft von
`localtrader/cost_schedule.py`: keine Überlappungen, endliche nichtnegative Werte, jede Zeile mit Beleg und
jede Pflichtkomponente an **jedem** Tag des erklärten Geltungsbereichs (`coverage` 2016-01-01 bis
2026-12-31); `missing_components()` zeigt Lücken für ein konkretes Datum (z. B. FINRA TAF ab 2027). Der Digest der Datei wird bei T-008 im Freeze-Beleg gebunden. **Die Engine rechnet noch mit
den Werten aus `config/default.toml`;** die Anbindung der Tabelle gehört zu T-008/T-011.

Jede Zeile ist genau einer Art zugeordnet:
- **Quelle**: offizieller, verlinkter Satz (Behörde/Broker).
- **Messwert**: im Projekt gemessen (Befehl, Stichprobe, Seed angegeben).
- **Annahme**: begründete Modellwahl ohne Beleg oder Messung.

| Komponente | Seite | Wert | Art | Grundlage |
|---|---|---|---|---|
| Kommission | beide | 0 | Quelle | Alpaca: provisionsfrei für US-Aktien/ETFs |
| SEC-Gebühr (§31) 2016 – 09/2025 | Verkauf | 0,278 bp | Annahme | höchster belegter Satz des Zeitraums ($27.80/Mio. ab 2024-05-22, SEC-Pressemitteilung 2024-47); einzelne Jahre nicht vollständig belegt, daher konservativ konstant |
| SEC-Gebühr 10/2025 – 2026-04-03 | Verkauf | 0 | Quelle | SEC Fee Rate Advisory FY2026 |
| SEC-Gebühr ab 2026-04-04 | Verkauf | 0,206 bp | Quelle | $20.60/Mio., SEC Fee Rate Advisory FY2026 |
| FINRA TAF 2016–2025 | Verkauf | $0.000166/Stück, max. $8.30 | Annahme / Quelle | Satz 2024/25 (frühere Jahre niedriger), FINRA-Gebührenplan |
| FINRA TAF 2026 | Verkauf | $0.000195/Stück, max. $9.79 | Quelle | FINRA-Gebührenplan; mögliche Aussetzung Q4/2026 nicht berücksichtigt |
| CAT-Gebühr | beide | $0.000046/Stück | Annahme | höchster bekannter Alpaca-Satz; aktuelle Sätze niedriger |
| Halber Spread SPY | beide | 0,29 bp | Messwert | 90. Perzentil aus 400 Stichproben 2016–2025 (Median 0,18 bp; Eröffnung 09:30 p90 0,58 bp) |
| Halber Spread QQQ | beide | 0,46 bp | Messwert | 90. Perzentil aus 400 Stichproben 2016–2025 (Median 0,28 bp; Eröffnung 09:30 p90 0,78 bp) |
| Slippage | beide | 1,0 bp | Annahme | keine eigenen Fill-Daten; zusätzlich zum Spread |
| EUR/USD-Umtausch | Ein-/Auszahlung | 100 bp | Annahme | Alpaca veröffentlicht keinen festen Aufschlag (Beispiele 45–150 bp); nur bei Ein-/Auszahlung |

## Spread-Messung

`python3 -m localtrader measure-spreads --symbol SPY --bars data/SPY_15m.csv` zieht mit festem Seed 400
Bar-Eröffnungen (dort führt die Engine aus) aus dem Forschungszeitraum. Die Obergrenze ist fest der
`[holdout] start`; `--before` darf sie nur vorverlegen, und das ganze Anfragefenster muss davor liegen –
der Holdout wird nie abgefragt. Je Zeitpunkt wird je Zeitpunkt die erste gültige SIP-Quote der folgenden 2 Sekunden (gekreuzte,
gesperrte oder leere Quotes werden übersprungen). Ergebnis je Jahr:

| Jahr | SPY Median / p90 (bp, halber Spread) | QQQ Median / p90 |
|---|---|---|
| 2016 | 0,24 / 0,26 | 0,45 / 0,49 |
| 2018 | 0,18 / 0,37 | 0,30 / 0,60 |
| 2020 | 0,16 / 0,36 | 0,24 / 0,51 |
| 2022 | 0,13 / 0,26 | 0,19 / 0,41 |
| 2024 | 0,10 / 0,19 | 0,20 / 0,31 |
| 2025 | 0,09 / 0,33 | 0,19 / 0,32 |

Grenzen: Quotes zeigen Spreads, keine tatsächlichen Fills; die Stichprobe je Jahr ist klein (25–51);
Eröffnungen um 09:30 New York (sommerzeitkorrekt klassifiziert) haben deutlich breitere Spreads, kommen aber
nur in rund 4 % der Stichproben vor (SPY 14, QQQ 16).

## Vergleich mit der bisherigen Konfiguration (Hin- und Rückweg)

| | Konfiguration | Tabelle SPY | Tabelle QQQ |
|---|---|---|---|
| Gebühren | 0,2 bp | ≈ 0,28 bp | ≈ 0,28 bp |
| Spread | 0,5 bp | 0,57 bp | 0,93 bp |
| Slippage | 2,0 bp | 2,0 bp | 2,0 bp |
| **Summe** | **2,7 bp** | **≈ 2,86 bp** | **≈ 3,20 bp** |

Die bisherige Konfiguration unterschätzt die Kosten um etwa 6 % (SPY) bzw. 19 % (QQQ). Die
Stressszenarien (doppelte Kosten, dreifacher Spread) decken das ab, die Basisläufe nicht.
