# Beispielreport: DAX 15 Minuten, Basisstrategien (2026-11-17)

Erzeugt mit `make intraday` und `make intraday-stress`. Rohdateien (summary.json, trades.csv, equity.csv,
decisions.jsonl, SVG) liegen lokal unter `reports/` und werden nicht versioniert.

**Daten:** `data/DAX3015.csv`, 2020-10-06 bis 2021-05-28 (165 Handelstage), Datenversion `2abcefe4e72c6ba5`.
Herkunft unbekannt, Zeitzone als Europe/Athens angenommen (siehe `docs/DATA_FORMAT.md`).
**Kosten (Annahme):** CFD, 1,0 bp Spread, 0,5 bp Slippage, keine Kommission, 5 % p. a. Finanzierung über Nacht.
**Kapital:** 10.000 EUR, 0,20 % Risiko je Trade, max. 20 % je Instrument, Mindestmenge 0,1 Kontrakt.

> Der Zeitraum ist kürzer als ein Jahr und umfasst ein Instrument in einer überwiegend steigenden Phase.
> Die Zahlen sind technische Ergebnisse, **kein Nachweis** einer Gewinn- oder Verlustwahrscheinlichkeit.

## Basislauf

| Kennzahl | Trendfolge | Mean Reversion | Buy-and-hold | Cash |
|---|---|---|---|---|
| Nettorendite | +0,24 % | −0,77 % | +17,19 % | 0 % |
| Geometrische Monatsrendite | +0,03 % | −0,10 % | – | 0 % |
| 90-%-Intervall Monatsrendite (Block-Bootstrap) | −0,13 % … +0,18 % | −0,24 % … +0,03 % | – | – |
| Max. Drawdown | 0,82 % | 1,29 % | – | 0 % |
| Längste Drawdown-Phase | 102 Tage | 126 Tage | – | – |
| Trades | 97 | 170 | 1 | 0 |
| Trefferquote | 32 % | 45 % | – | – |
| Ø Gewinn / Ø Verlust (EUR) | 9,69 / −3,99 | 4,39 / −4,31 | – | – |
| Ø Haltedauer | 16 h | 3,8 h | – | – |
| Kosten gesamt (EUR) | 40,33 | 52,56 | – | 0 |
| Ø Marktexposure | 3,7 % | 1,5 % | 100 % | 0 % |

## Robustheit (Nettorendite)

| Szenario | Trendfolge | Mean Reversion |
|---|---|---|
| Basis | +0,24 % | −0,77 % |
| Doppelte variable Kosten | −0,03 % | −1,25 % |
| Dreifacher Spread | −0,03 % | −1,25 % |
| Ausführung 1 Bar später | +0,35 % | −0,78 % |
| Parameter −20 % | +0,50 % | −0,59 % |
| Parameter +20 % | +1,67 % | −1,52 % |
| Phase 1 (Okt–Dez 2020) | −0,31 % | +0,38 % |
| Phase 2 (Dez 2020–Mär 2021) | +0,04 % | −0,98 % |
| Phase 3 (Mär–Mai 2021) | +0,29 % | −0,18 % |

## Einordnung

- **Mit den verfügbaren Daten wurde keine belastbare profitable Strategie gefunden.** Die Trendfolge
  liegt nahe null und wird bei doppelten Kosten negativ; Mean Reversion ist im Basislauf negativ.
- Beide Strategien liegen weit unter Buy-and-hold. Die Hauptursache ist die sehr geringe Kapitalbindung
  (Ø 1,5–3,7 %) durch 0,20 % Risiko je Trade und die Instrumentgrenze. Das ist gewollt konservativ.
  Ein Anheben des Risikos wäre ein bewusster, separat zu begründender Schritt und kein Mittel, Renditeziele zu erreichen.
- Die Parametervariante +20 % wirkt besser. Eine solche Auswahl im Nachhinein wäre Overfitting; sie
  ist ein Fall für die Walk-forward-Forschung (Etappe 8) und keine Empfehlung.
- Die 90-%-Intervalle beider Strategien schließen null ein.
