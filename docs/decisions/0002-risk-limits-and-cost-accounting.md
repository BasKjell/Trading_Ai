# ADR 0002: Risikolimits gemäß Vorgaben, einfache Kostenbuchung

Datum: 2026-11-17 · Status: angenommen

## Kontext

Der MVP nutzte 0,25 % Risiko pro Trade, eine Drawdown-Leiter von 8/12/15 % und 100.000 Startkapital.
Die Projektvorgaben verlangen 0,20 % pro Trade, höchstens 1 % gesamtes Stop-Risiko, 20 % je Instrument,
100 % Bruttoexposition, Drawdown-Stufen 5/8/10 % und 10.000 EUR. Außerdem wurden Spread und Slippage
doppelt belastet: einmal im Ausführungspreis und ein zweites Mal vom Cash.

## Entscheidung

- Spread und Slippage stecken ausschließlich im Ausführungspreis; vom Cash wird nur die Gebühr
  (inkl. Mindestgebühr) abgezogen. `realized_costs` und Berichte weisen alle drei Komponenten aus.
- Positionsgrößen berücksichtigen Stop-Distanz plus geschätzte Round-Trip-Kosten, das offene
  Stop-Risiko-Budget, Instrument-, Gruppen- und Bruttoexposition, Cash, Volumenanteil der Ausführungsbar
  und eine Mindestordergröße. Die Entscheidung nennt das bindende Limit.
- Offenes Stop-Risiko wird vom aktuellen Kurs zum Stop gemessen; Positionen ohne Stop zählen mit ihrem
  vollen Marktwert.
- High-Water-Mark: höchste markierte Equity (inkl. unrealisierter Ergebnisse). Ein- und Auszahlungen
  verschieben Cash und HWM gleichermaßen.
- Drawdown ≥ 5 %: Risikobudget halbiert; ≥ 8 %: keine neuen Positionen; ≥ 10 %: Kill-Switch und
  `deleveraging_required` werden gesetzt und persistiert. Nur ein manuelles `kill-switch off` gibt den
  Handel wieder frei; ein Neustart nicht.
- Schwellen sind inklusiv mit Toleranz gegen Gleitkomma-Artefakte.
- `HARD_LIMITS` in `config.py` begrenzen jede Konfiguration (z. B. ≤ 0,5 % pro Trade, keine Hebelung).

## Konsequenzen

- Bisherige Backtest-Ergebnisse sind nicht mit neuen vergleichbar (Kapital, Limits, Kostenbuchung).
- Der kontrollierte Positionsabbau nach dem Kill-Switch wird im neuen Intraday-Engine umgesetzt;
  die älteren Tages-Engines sperren nur neue Einstiege.
