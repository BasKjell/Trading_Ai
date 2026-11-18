# ADR 0006: Paper- und Shadow-Betrieb mit verzögertem SIP-Feed

Datum: 2026-11-18 · Status: angenommen (Benutzerentscheidung)

## Kontext

Der Preflight (T-012) hat ergeben: Der vorhandene Alpaca-Datenplan liefert SIP nur mit 15 Minuten Verzögerung, in Echtzeit nur IEX. Die Forschung (T-018, T-008) beruht auf SIP. Die Messung in `docs/FEED_COMPARISON.md` zeigt:
- IEX-Preise liegen nahe an SIP.
- Das IEX-Volumen beträgt aber nur 1 bis 2 % des SIP-Volumens.
- Rund 10 % der Ausbruchs-Bars fallen auf IEX anders aus.

## Entscheidung

Option **C**: Paper und Shadow verwenden **SIP-Bars mit 15 Minuten Verzögerung**. Damit entsteht kein Datenkauf.

- Ein Feed übernimmt nur vollständige Bars mit `Ende ≤ jetzt − 15 min`. Die zuletzt gelieferte Bar kann noch laufen und wird verworfen.
- Eine Entscheidung fällt dadurch eine Bar später als in der Forschung, die Ausführung erfolgt ein zulässiges Open später. Das entspricht dem T-008-Stressszenario `delay1`, das ein Kandidat ohnehin bestehen muss.
- Für die Prüfung auf veraltete Daten ist eine Bar normal, wenn sie zwischen 15 und 30 Minuten alt ist. Der Paper-Dienst (T-006) verwendet dafür eine Grenze von 35 Minuten.
- IEX in Echtzeit ist nur zur Überwachung der Datenfrische oder für reine Betriebstests erlaubt. IEX-Kerzen fließen nicht in Signale ein.

## Folgen

- Paper-Ergebnisse sind mit dem `delay1`-Szenario der Forschung vergleichbar, nicht mit dem Basisszenario.
- Ein SIP-Echtzeitabo (Option B) wird nur erwogen, wenn ein Kandidat das Basisszenario besteht, `delay1` aber nur knapp. Ein Datenkauf braucht die ausdrückliche Freigabe des Benutzers (AGENTS.md §5).
