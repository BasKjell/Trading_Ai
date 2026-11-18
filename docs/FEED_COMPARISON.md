# Datenfeed für den Paper-Betrieb: IEX gegen SIP (T-012)

**Entscheidung 2026-11-18 (Benutzer):** Option C, SIP mit 15 Minuten Verzögerung. Siehe [ADR 0006](decisions/0006-paper-feed-delayed-sip.md).

Befehl:

```bash
python3 -m localtrader compare-feeds --reference-dir data --candidate-dir data/iex --symbols SPY,QQQ
```

Der Befehl misst nur Dateneigenschaften und rechnet keine Strategie und keine Rendite. Deshalb erzeugt die Messung keinen zusätzlichen Versuch im T-008-Inventar. Die IEX-Dateien stammen aus `fetch-intraday --feed iex` mit derselben Holdout-Grenze wie die Forschungsdaten. IEX-Historie gibt es bei Alpaca erst ab 2020-07-27. Code: `localtrader/feed_compare.py`.

## Ergebnis (15-Minuten-Bars, reguläre Sitzung, 2020-07-27 bis 2025-10-31)

| Symbol | Abdeckung | Close-Abw. Median / p95 / p99 (bp) | High / Low p95 (bp) | IEX-Volumenanteil (Median) | gleiche Richtung | Ausbruch-Jaccard |
|---|---|---|---|---|---|---|
| SPY | 99,86 % | 0,43 / 2,37 / 4,26 | 2,16 / 2,33 | 1,75 % | 97,1 % | 0,91 |
| QQQ | 99,86 % | 0,78 / 4,51 / 7,99 | 4,20 / 4,57 | 1,16 % | 95,4 % | 0,90 |

- **Abdeckung:** Je Symbol fehlen in IEX rund 50 von 34.330 SIP-Bars.
- **Preise:** Schlusskurse weichen im Median um weniger als 1 bp ab. Das liegt unter dem modellierten halben Spread plus Slippage (1,5 bp). Einzelne Hochs und Tiefs weichen stark ab, bei SPY bis 283 bp, weil es auf IEX vereinzelt Ausreißer-Prints gibt.
- **Volumen:** IEX zeigt nur 1 bis 2 % des SIP-Volumens. Eine Volumengrenze auf IEX-Basis wäre 50- bis 90-mal zu streng. Mit IEX-Volumen muss die Liquiditätsgrenze deshalb auf SIP-Volumen umgestellt oder skaliert werden. Für ein Konto mit 10.000 EUR bindet sie auch dann praktisch nicht.
- **Signalnähe:** Die Bar-Richtung stimmt in 95 bis 97 % der Fälle überein. Ein generischer 16-Bar-Ausbruch („Close über dem höchsten Hoch der letzten 16 Bars“, keine Handelsregel) trifft auf IEX in rund 10 % der Ausbruchs-Bars anders zu (Jaccard 0,90). IEX zeigt 5 bis 7 % mehr Ausbrüche.
- Die Abweichungen werden über die Jahre kleiner. Der Close-Median bei QQQ fiel von 1,08 bp (2020) auf 0,57 bp (2025).

## Verzögerung von SIP im aktuellen Datenplan (gemessen 2026-11-18, nur Zeitstempel)

- Eine Anfrage mit Ende vor weniger als 15 Minuten scheitert mit `HTTP 403`.
- Ab 15 Minuten Abstand liefert die API SIP-Bars. Achtung: Die zuletzt gelieferte Bar kann dann noch laufen.
- Eine SIP-Bar, die um T endet, ist also vollständig ab T + 15 min verfügbar. Ein Feed darf nur Bars mit `Ende ≤ jetzt − 15 min` übernehmen.
- Entscheidungen fallen dadurch später als in der Forschung. Ob der Paper-Betrieb damit einem T-008-Szenario entspricht, ist **nicht belegt**. `delay1` verschiebt die Ausführung um genau ein zulässiges Open. Der Paper-Dienst arbeitet dagegen mit dem tatsächlichen Tick-Zeitpunkt: Eine Bar, die um T endet, ist frühestens um T + 15 min lesbar, und dann liegt das Open um T + 15 min schon in der Vergangenheit. Innerhalb der Sitzung kommt daher eher das Open um T + 30 min infrage, am Sitzungswechsel dagegen dasselbe Open wie im Basisszenario. Eine Gleichsetzung braucht einen Vergleich der tatsächlichen Ankunfts- und Ausführungszeitpunkte, einschließlich Sitzungswechsel (offen).

## Bewertung der Optionen

| Option | Datenbasis wie Forschung | Zeitpunkt | Kosten | Bewertung |
|---|---|---|---|---|
| **A: IEX in Echtzeit** | nein (rund 10 % abweichende Ausbruchs-Bars, Volumen nicht vergleichbar) | wie Forschung | kostenlos | gut für Betriebstests. Die Signale sind nicht 1:1 mit der Forschung vergleichbar |
| **B: SIP in Echtzeit** | ja | wie Forschung | Abo, nur mit Freigabe | nur nötig, wenn die Verzögerung von C die Strategie nachweislich beeinträchtigt |
| **C: SIP verzögert** | ja | später als die Forschung (Ausmaß siehe oben, Entsprechung zu `delay1` nicht belegt) | kostenlos | **empfohlen**: gleiche Daten wie die Forschung |

**Empfehlung:**
- **C** für Shadow und Paper: gleiche Datenquelle wie die Forschung, ohne Kosten und ohne Feed-Bruch. Die zeitliche Verschiebung gegenüber der Forschung ist zu belegen, bevor Paper-Ergebnisse mit einem Forschungsszenario verglichen werden.
- **A** nur ergänzend zur Überwachung der Datenfrische oder für reine Betriebstests.
- **B** erst nach einem bestandenen Forschungsurteil, wenn die Verzögerung von C nachweislich schadet. Die Entscheidung liegt beim Benutzer (Datenkauf nur nach Freigabe).
