# Basisreport: SPY und QQQ, 15 Minuten, Basisstrategien (2026-11-17)

Erzeugt mit `intraday-backtest` und `intraday-stress` (`--symbol SPY|QQQ`), Code-Stand T-018 auf
`1dfb421`. Rohdateien liegen lokal unter `reports/` und werden nicht versioniert. Experiment: EXP-T018-001.

**Daten:** Alpaca SIP, bereinigt, nur reguläre Handelszeit (ADR 0005). Forschungszeitraum
2016-01-04 bis 2025-10-31 (2.473 Handelstage). **Holdout ab 2025-11-01 nicht verwendet** (6.805 Bars je
Symbol ausgeschlossen). Datenversionen SPY `521f66df08a5c5d8`, QQQ `95c1721523a14210`.
**Kosten (Annahme):** keine Kommission, 0,1 bp Gebühren, 0,5 bp Spread, 1,0 bp Slippage, keine Finanzierung.
**Kapital/Risiko:** 10.000 (USD 1:1 als Kontowährung, T-011 offen), 0,20 % Risiko je Trade, max. 20 % je Instrument.
**Parameter:** unverändert aus der Konfiguration (keine Optimierung auf diesen Daten).

> Ein Instrument je Lauf; SPY und QQQ sind stark korreliert und keine unabhängigen Belege.
> Die Zahlen sind technische Ergebnisse, **kein Nachweis** einer Gewinnwahrscheinlichkeit.

## Basislauf

| Kennzahl | Trend SPY | Trend QQQ | MeanRev SPY | MeanRev QQQ |
|---|---|---|---|---|
| Nettorendite (9,8 Jahre) | +6,32 % | +12,93 % | −7,93 % | −7,91 % |
| Jahresrendite | +0,63 % | +1,25 % | −0,84 % | −0,84 % |
| Geometrische Monatsrendite | +0,05 % | +0,10 % | −0,07 % | −0,07 % |
| 90-%-Intervall Monatsrendite (Block-Bootstrap) | −0,005 % … +0,11 % | +0,04 % … +0,18 % | −0,11 % … −0,03 % | −0,11 % … −0,03 % |
| Sharpe (jährlich) | 0,49 | 0,76 | −0,93 | −0,93 |
| Max. Drawdown | 1,64 % | 1,98 % | 8,03 % | 8,03 % |
| Längste Drawdown-Phase | 469 Tage | 290 Tage | bis Ende | bis Ende |
| Trades | 981 | 947 | 847 | 751 |
| Trefferquote | 39 % | 41 % | 48 % | 47 % |
| Profit-Faktor | 1,15 | 1,25 | 0,76 | 0,77 |
| Ø Haltedauer | 27 h | 27 h | 7,2 h | 6,7 h |
| Kosten gesamt | 536 | 525 | 429 | 360 |
| Ø Exposure / Zeit im Markt | 6,2 % / 31 % | 6,2 % / 32 % | 1,7 % / 9 % | 1,3 % / 8 % |
| Buy-and-hold gleicher Zeitraum | +296 % | +519 % | +296 % | +519 % |

Mean Reversion erreicht im Dezember 2021 die 8-%-Drawdown-Grenze; danach lehnt die Risiko-Engine alle
neuen Einstiege ab (965 bzw. 1.212 Ablehnungen `drawdown_block_new`). Die Ergebnisse ab 2022 enthalten
deshalb keine Trades mehr. 137 bzw. 115 Einstiege wurden verworfen, weil das Signal am Sitzungsende
entstand und erst am Folgetag hätte ausgeführt werden können.

## Robustheit (Nettorendite über den Forschungszeitraum)

| Szenario | Trend SPY | Trend QQQ | MeanRev SPY | MeanRev QQQ |
|---|---|---|---|---|
| Basis | +6,32 % | +12,93 % | −7,93 % | −7,91 % |
| Doppelte variable Kosten | +0,89 % | +7,43 % | −7,98 % | −8,09 % |
| Dreifacher Spread | +4,27 % | +10,86 % | −7,97 % | −7,95 % |
| Ausführung 1 Bar später | +4,89 % | +13,30 % | −7,93 % | −7,90 % |
| Parameter −20 % | +3,40 % | +8,15 % | −7,96 % | −7,93 % |
| Parameter +20 % | +7,39 % | +15,32 % | −3,88 % | −7,36 % |
| Phase 1 (2016-01 – 2019-04) | +1,75 % | +2,38 % | −3,87 % | −4,74 % |
| Phase 2 (2019-04 – 2022-07) | +1,09 % | +4,79 % | −4,70 % | −7,72 % |
| Phase 3 (2022-07 – 2025-10) | +2,80 % | +4,43 % | +0,13 % | −0,22 % |

## Einordnung

- **Trendfolge** ist in allen drei Phasen und unter allen Stressszenarien positiv, bei SPY aber nur knapp
  (doppelte Kosten: +0,9 % in fast 10 Jahren; das Intervall schließt null ein). Bei QQQ schließt das
  90-%-Intervall null aus. Das ist ein Hinweis, **kein Nachweis**: Es ist ein einziger, nicht
  optimierter Lauf auf einem langen Aufwärtsmarkt; die Strategie ist nur long und profitiert vom Trend
  des Basiswerts. Mit Ø 6 % Kapitalbindung liegt sie weit unter Buy-and-hold.
- **Mean Reversion** ist durchgehend negativ (Profit-Faktor < 1, Intervall vollständig unter null) und
  wird verworfen. Phase 3 liegt nahe null; eine Umstellung der Parameter wäre Auswahl im Nachhinein.
- Die Kosten (rund 5 % des Anfangskapitals) entsprechen bei der Trendfolge etwa der Hälfte des
  Bruttoergebnisses; der Kostenstress ist das wichtigste Risiko.
- Nächster sinnvoller Schritt ist Walk-forward mit Champion-Challenger (T-008), nicht weitere
  Parametervarianten auf diesem Zeitraum. Der Holdout bleibt bis dahin unberührt.
