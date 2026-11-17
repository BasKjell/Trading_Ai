# ADR 0003: Ereignisbasierter 15-Minuten-Engine und DAX-CFD als erstes Instrument

Datum: 2026-11-17 · Status: angenommen

## Kontext

Der MVP-Backtest arbeitete auf Tagesbars mit fest verdrahteten Strategien. Die Vorgaben verlangen
15-Minuten-Signale, Vergleichsintervalle, Kostenstress und eine gemeinsame Logik für Backtest und Paper.

## Entscheidung

- Neuer Engine `localtrader/intraday_engine.py`, der dieselbe Risiko-Engine, dasselbe Fill-Modell und
  dasselbe Portfolio wie der Paper-Betrieb verwendet. Strategien sind reine Funktionen über
  `StrategyContext` (abgeschlossene 15-Minuten-Bars, verfügbare Stundenbars).
- Ausführungsregeln: nächste Eröffnung nach dem Signal; Stops `min(Open, Stop)`; Stop in der
  Einstiegsbar gilt als ausgelöst; Liquiditätsgrenze aus der Vorbar; Teilausführungen bei Exits;
  Übernachtfinanzierung; kontrollierter Abbau nach dem Drawdown-Kill-Switch; Abschlussliquidation.
- Nachtrag nach Review zu PR #17: Informationen werden nach Verfügbarkeit (`received_at`) verarbeitet,
  nicht nach `bar.start`; ein Strategiekontext enthält nur Bars mit `received_at <= now`. Eine verspätet
  eintreffende ältere Bar löst keine eigene Entscheidung aus. `Signal.earliest_execution` ist bindend.
  Neue Einstiege werden auf der Ausführungsbar erneut geprüft (Bar nach Datenlücke, außerhalb der
  Handelszeit, mehr als `1 + extra_delay_bars` Bars nach `earliest_execution`) und sonst verworfen.
  Ausstiege werden dadurch nie blockiert.
- Erstes Instrument ist der DAX (CFD-Kostenmodell), weil nur dafür 15-Minuten-Daten vorliegen.
  Die Position wird voll mit Cash gedeckt (keine Hebelnutzung trotz CFD).
- Die bisherigen Tages-Engines bleiben für das Research-Lab bestehen.

## Alternativen

- DAX-ETF über Xetra: echte Börsengebühren (Mindestgebühr) wären bei 10.000 EUR und kleinen Positionen
  dominierend; ohne passende 15-Minuten-Daten nicht prüfbar. Über `[instruments.*]` konfigurierbar.
- Ein Engine für alle Intervalle: später möglich; zunächst getrennt, um den bewährten Tages-Code nicht zu brechen.

## Konsequenzen

- Broker- und Datenanbieter sind noch nicht festgelegt; die Kostenparameter sind Annahmen und im
  Report als solche gekennzeichnet.
- CFD-spezifische Risiken (Emittentenrisiko, Finanzierung, Kursstellung des Anbieters) müssen vor
  einer Liveentscheidung gesondert geprüft werden.
