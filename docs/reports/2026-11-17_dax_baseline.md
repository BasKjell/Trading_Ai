# Beispielreport: DAX 15 Minuten, Basisstrategien (2026-11-17)

Erzeugt mit `make intraday` und `make intraday-stress`. Rohdateien (summary.json, trades.csv, equity.csv,
decisions.jsonl, SVG) liegen lokal unter `reports/` und werden nicht versioniert.

**Daten:** `data/DAX3015.csv`, 2020-10-06 bis 2021-05-28 (165 Handelstage), Datenversion `2abcefe4e72c6ba5`.
Herkunft unbekannt, Zeitzone als Europe/Athens angenommen (siehe `docs/DATA_FORMAT.md`).
**Kosten (Annahme):** CFD, 1,0 bp Spread, 0,5 bp Slippage, keine Kommission, 5 % p. a. Finanzierung über Nacht.
**Kapital:** 10.000 EUR, 0,20 % Risiko je Trade, max. 20 % je Instrument, Mindestmenge 0,1 Kontrakt.

> Der Zeitraum ist kürzer als ein Jahr und umfasst ein Instrument in einer überwiegend steigenden Phase.
> Die Zahlen sind technische Ergebnisse, **kein Nachweis** einer Gewinn- oder Verlustwahrscheinlichkeit.

Aktualisiert nach dem Review zu PR #17: Einstiege werden jetzt auf der Ausführungsbar erneut geprüft
(Datenlücke, Handelszeit, Signalalter). Dadurch entfallen 2 bzw. 5 Einstiege, die zuvor außerhalb der
Handelszeit ausgeführt wurden. Die Datei enthält keine verspätet verfügbaren Bars. Stop-Ausstiege werden
seit dem zweiten Review am Ende der Stop-Bar verbucht (Haltedauer dadurch geringfügig länger, Renditen unverändert).

## Basislauf

| Kennzahl | Trendfolge | Mean Reversion | Buy-and-hold | Cash |
|---|---|---|---|---|
| Nettorendite | +0,26 % | −0,68 % | +17,19 % | 0 % |
| Geometrische Monatsrendite | +0,03 % | −0,09 % | – | 0 % |
| 90-%-Intervall Monatsrendite (Block-Bootstrap) | −0,13 % … +0,19 % | −0,23 % … +0,03 % | – | – |
| Max. Drawdown | 0,82 % | 1,24 % | – | 0 % |
| Längste Drawdown-Phase | 102 Tage | 124 Tage | – | – |
| Trades | 96 | 165 | 1 | 0 |
| Trefferquote | 32 % | 45 % | – | – |
| Ø Gewinn / Ø Verlust (EUR) | 9,69 / −4,02 | 4,24 / −4,16 | – | – |
| Ø Haltedauer | 16 h | 3,2 h | – | – |
| Kosten gesamt (EUR) | 39,39 | 49,85 | – | 0 |
| Ø Marktexposure | 3,7 % | 1,3 % | 100 % | 0 % |

## Robustheit (Nettorendite)

| Szenario | Trendfolge | Mean Reversion |
|---|---|---|
| Basis | +0,26 % | −0,68 % |
| Doppelte variable Kosten | −0,00 % | −1,15 % |
| Dreifacher Spread | −0,00 % | −1,15 % |
| Ausführung 1 Bar später | +0,15 % | −0,65 % |
| Parameter −20 % | +0,54 % | −0,51 % |
| Parameter +20 % | +1,73 % | −1,36 % |
| Phase 1 (Okt–Dez 2020) | −0,31 % | +0,43 % |
| Phase 2 (Dez 2020–Mär 2021) | +0,04 % | −0,94 % |
| Phase 3 (Mär–Mai 2021) | +0,31 % | −0,18 % |

## Einordnung

- **Mit den verfügbaren Daten wurde keine belastbare profitable Strategie gefunden.** Die Trendfolge
  liegt nahe null und fällt bei doppelten Kosten auf null bzw. knapp darunter (−0,005 %); Mean Reversion ist im Basislauf negativ.
- Beide Strategien liegen weit unter Buy-and-hold. Die Hauptursache ist die sehr geringe Kapitalbindung
  (Ø 1,3–3,7 %) durch 0,20 % Risiko je Trade und die Instrumentgrenze. Das ist gewollt konservativ.
  Ein Anheben des Risikos wäre ein bewusster, separat zu begründender Schritt und kein Mittel, Renditeziele zu erreichen.
- Die Parametervariante +20 % wirkt besser. Eine solche Auswahl im Nachhinein wäre Overfitting; sie
  ist ein Fall für die Walk-forward-Forschung (Etappe 8) und keine Empfehlung.
- Die 90-%-Intervalle beider Strategien schließen null ein.
