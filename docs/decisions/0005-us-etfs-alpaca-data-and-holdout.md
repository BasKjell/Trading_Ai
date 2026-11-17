# ADR 0005: US-Index-ETFs mit Alpaca-Daten statt DAX; fester Holdout

Datum: 2026-11-17 · Status: angenommen (Benutzerentscheidung)

## Kontext

Die einzigen 15-Minuten-Daten waren rund 8 Monate DAX unbekannter Herkunft (T-018). Der in der Spec
genannte Broker Alpaca handelt keinen DAX (T-012). Der Benutzer hat am 2026-11-17 entschieden: Das
Instrument muss nicht der DAX sein; es soll ein geeignetes Instrument sein, für das gute Daten verfügbar
sind, und es sollen mehr Daten ergänzt werden.

## Entscheidung

- Erste Instrumente sind **SPY** und **QQQ**: sehr liquide US-Index-ETFs, bei Alpaca handelbar
  (provisionsfrei, Bruchstücke möglich) und mit 15-Minuten-Historie ab 2016.
- Datenquelle: Alpaca Market Data v2 `/stocks/bars`, Feed **SIP** (konsolidiertes Tape aller US-Börsen),
  `adjustment=all` (split- und dividendenbereinigt), abgerufen mit dem vorhandenen Paper-Key. Im
  Basic-Plan kostenlos; Einschränkung nur für die letzten 15 Minuten. **Kein Datenkauf.**
- Nur Bars der regulären Handelszeit (09:30–16:00 America/New_York). Außerbörsliche Bars sind dünn
  gehandelt und bei Alpaca nur mit Limitorders handelbar.
- **Holdout:** Alle Bars ab `2025-11-01T00:00:00Z` sind der finale unabhängige Testzeitraum
  (`[holdout]` in `config/default.toml`). Festgelegt und committet, bevor irgendein Ergebnis auf diesen
  Daten berechnet wurde. Forschungsläufe schließen ihn automatisch aus; `--use-holdout` ist der zentralen
  Holdout-Auswertung vorbehalten und wird im Report protokolliert.
- Der DAX bleibt als zweiter, kleiner Datensatz erhalten.

## Alternativen

- DAX mit anderem Broker: keine frei verfügbaren, langen 15-Minuten-Daten bekannter Herkunft.
- BTC/ETH über Alpaca: 24/7-Handel und andere Kostenstruktur; später als eigenes Instrument möglich.
- IEX-Feed: nur ein Handelsplatz, deutlich weniger Volumen in den Bars; SIP ist repräsentativer.

## Konsequenzen

- Preise sind in USD, das Konto rechnet in EUR. Bis T-011 (Währungen) werden USD 1:1 als
  Kontowährung behandelt. Renditen sind davon nicht betroffen, absolute Beträge schon.
- Bereinigte Preise (`adjustment=all`) enthalten Dividenden als Kursanpassung; frühere Kurse liegen
  unter den damals gehandelten. Für Renditen unkritisch, für absolute Preisniveaus nicht.
- Kein Feiertags-/Halbtagskalender: Halbtage enden früher; das erkennt der Import nicht als Lücke.
- SPY und QQQ sind stark korreliert (gleiche Korrelationsgruppe in der Konfiguration).
- T-012 ist damit in der Instrumentenfrage entschieden; der Broker-Preflight bleibt offen.
