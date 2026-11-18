# Basisreport: SPY und QQQ, 15 Minuten, Basisstrategien (2026-11-17, korrigiert 2026-11-18)

Erzeugt mit `intraday-backtest` und `intraday-stress` (`--symbol SPY|QQQ`) auf dem Stand nach dem
Codex-Review zu PR #21. Rohdateien liegen lokal unter `reports/` und werden nicht versioniert.
Experiment: **EXP-T018-003** (ersetzt EXP-T018-001/-002; -001 nutzte bereinigte Kurse zur Ausführung und
enthielt Nachbörsen-Bars an Halbtagen, -002 buchte Dividenden am Ex-Tag als Cash).

**Daten:** Alpaca SIP, **handelbare Rohkurse** für Signale, Ausführung, Stops und Bewertung; Dividenden
aus Alpaca-Corporate-Actions als Forderung am Ex-Tag und Cash am Zahltag, abzüglich 15 % Quellensteuer; reguläre Handelszeit je Tag inkl. NYSE-Halbtagen
(ADR 0005). Forschungszeitraum 2016-01-04 bis 2025-10-31 (2.473 Handelstage). **Holdout ab 2025-11-01
beim Import verworfen** (6.788 Zeilen je Symbol, nur Anzahl gemeldet). SPY fehlen bei Alpaca zwei
Dividenden (März 2016, Juni 2018); Trend- und Buy-and-hold-Ergebnisse von SPY sind dadurch minimal zu niedrig.
**Kosten (Annahme):** keine Kommission, 0,1 bp Gebühren, 0,5 bp Spread, 1,0 bp Slippage, keine Finanzierung.
**Kapital/Risiko:** 10.000 (USD 1:1 als Kontowährung, T-011 offen), 0,20 % Risiko je Trade, max. 20 % je Instrument.
**Parameter:** unverändert aus der Konfiguration (keine Optimierung auf diesen Daten).

> Ein Instrument je Lauf; SPY und QQQ sind stark korreliert und keine unabhängigen Belege.
> Die Zahlen sind technische Ergebnisse, **kein Nachweis** einer Gewinnwahrscheinlichkeit.

## Basislauf

| Kennzahl | Trend SPY | Trend QQQ | MeanRev SPY | MeanRev QQQ |
|---|---|---|---|---|
| Nettorendite (9,8 Jahre) | +6,30 % | +12,29 % | −7,94 % | −7,91 % |
| Jahresrendite | +0,62 % | +1,19 % | −0,84 % | −0,84 % |
| Geometrische Monatsrendite | +0,05 % | +0,10 % | −0,07 % | −0,07 % |
| 90-%-Intervall Monatsrendite (Block-Bootstrap) | −0,005 % … +0,11 % | +0,03 % … +0,17 % | −0,11 % … −0,03 % | −0,11 % … −0,03 % |
| Sharpe (jährlich) | 0,49 | 0,72 | −0,90 | −0,99 |
| Max. Drawdown | 1,63 % | 1,95 % | 8,01 % | 8,03 % |
| Längste Drawdown-Phase | 492 Tage | 296 Tage | bis Ende | bis Ende |
| Trades | 972 | 946 | 856 | 690 |
| Trefferquote | 38 % | 41 % | 48 % | 46 % |
| Profit-Faktor | 1,14 | 1,23 | 0,76 | 0,75 |
| Ø Haltedauer | 27 h | 27 h | 7,1 h | 6,7 h |
| Kosten gesamt | 530 | 522 | 434 | 329 |
| Dividendenansprüche (netto) | 67 | 23 | 24 | 8 |
| Ø Exposure / Zeit im Markt | 6,1 % / 31 % | 6,1 % / 31 % | 1,7 % / 9 % | 1,2 % / 7 % |
| Buy-and-hold gleicher Zeitraum (mit Nettodividenden) | +263 % | +489 % | +263 % | +489 % |

Mean Reversion erreicht Ende 2021 die 8-%-Drawdown-Grenze; danach lehnt die Risiko-Engine alle neuen
Einstiege ab. Ergebnisse ab 2022 enthalten deshalb keine Trades mehr. Am Sitzungsende entstandene Signale
werden verworfen statt am Folgetag ausgeführt.

## Robustheit (Nettorendite über den Forschungszeitraum)

| Szenario | Trend SPY | Trend QQQ | MeanRev SPY | MeanRev QQQ |
|---|---|---|---|---|
| Basis | +6,30 % | +12,29 % | −7,94 % | −7,91 % |
| Doppelte variable Kosten | +0,92 % | +6,83 % | −8,01 % | −7,90 % |
| Dreifacher Spread | +4,27 % | +10,23 % | −7,92 % | −7,94 % |
| Ausführung 1 Bar später | +5,07 % | +12,73 % | −7,94 % | −7,92 % |
| Parameter −20 % | +2,86 % | +8,40 % | −7,93 % | −7,98 % |
| Parameter +20 % | +6,72 % | +15,34 % | −3,79 % | −7,26 % |
| Phase 1 (2016-01 – 2019-04) | +1,60 % | +1,89 % | −3,65 % | −4,97 % |
| Phase 2 (2019-04 – 2022-07) | +0,97 % | +4,90 % | −4,95 % | −7,73 % |
| Phase 3 (2022-07 – 2025-10) | +3,12 % | +4,28 % | −0,29 % | −0,61 % |

## Einordnung

- **Trendfolge** ist in allen drei Phasen und unter allen Stressszenarien positiv, bei SPY aber nur knapp
  (doppelte Kosten: +0,9 % in fast 10 Jahren; das Intervall schließt null ein). Bei QQQ schließt das
  90-%-Intervall null aus. Das ist ein Hinweis, **kein Nachweis**: ein einziger, nicht optimierter Lauf
  auf einem langen Aufwärtsmarkt, nur long, mit Ø 6 % Kapitalbindung weit unter Buy-and-hold.
- **Mean Reversion** ist durchgehend negativ (Profit-Faktor < 1, Intervall vollständig unter null) und
  wird verworfen.
- Die Korrektur (Rohkurse, Dividenden, Halbtage) ändert die Strategieergebnisse kaum; der
  Buy-and-hold-Vergleich sinkt, weil bereinigte Kurse Dividenden brutto reinvestiert hatten. Die Buchung
  am Zahltag statt am Ex-Tag (EXP-T018-003) verändert die Ergebnisse praktisch nicht.
- Nächster Schritt ist die Walk-forward-Validierung nach dem Protokoll in T-008 (#22), nicht weitere
  Parametervarianten auf diesem Zeitraum. Der Holdout bleibt unberührt.
