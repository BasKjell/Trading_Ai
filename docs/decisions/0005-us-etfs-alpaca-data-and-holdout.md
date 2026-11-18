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
  abgerufen mit dem vorhandenen Paper-Key. Im Basic-Plan kostenlos; Einschränkung nur für die letzten
  15 Minuten. **Kein Datenkauf.**
- **Handelbare und bereinigte Preise getrennt** (Spec §5): Die Engine nutzt ausschließlich Rohkurse
  (`adjustment=raw`, wie gehandelt) für Signale, Ausführung, Stops und Bewertung. Die bereinigte Serie
  (`adjustment=all`) wird getrennt abgelegt und nur für Analysen verwendet.
- **Dividenden:** Quelle ist Alpacas Corporate-Actions-Endpunkt (Ex-Tag, Zahltag, Betrag je Anteil). Als
  unabhängige Gegenprobe werden die Beträge zusätzlich aus Roh- und bereinigter Serie abgeleitet; eine
  Abweichung über 5 % bricht den Abruf ab. Am Ex-Tag entsteht für eine gehaltene Position eine
  **Forderung** abzüglich 15 % US-Quellensteuer (Annahme W-8BEN): Sie zählt zum Kontowert, ist aber kein
  verfügbares Cash. Erst am Zahltag wird sie zu Cash, auch wenn die Position inzwischen geschlossen ist.
  Am Datenende offene Forderungen werden ausgewiesen. Die tatsächlich verwendeten Dividenden fließen in
  die Datenversion des Laufs ein. Buy-and-hold rechnet mit derselben Anspruchslogik. Splits werden noch
  nicht unterstützt: Ein Split im Abrufzeitraum (aus einer der beiden Quellen) bricht den Abruf ab
  (SPY/QQQ hatten 2016–2026 keinen).
- **Lücken bei Alpaca:** Vor 2020 fehlen alle Zahltage; Annahme (gekennzeichnet): Ex-Tag + längste
  beobachtete Verzögerung desselben ETFs (Cash eher später). Ein doppelt gelisteter Eintrag mit gleichem
  Betrag (QQQ 2022-09-19) verwendet den späteren Zahltag; Alpacas bereinigte Serie rechnet ihn doppelt
  ein, die Gegenprobe erkennt das. Abweichende Beträge brechen den Abruf ab.
- Bekannte Datenlücke: Alpaca fehlen zwei SPY-Dividenden (Ex-Tage im März 2016 und Juni 2018), sowohl in den
  bereinigten Bars als auch im Corporate-Actions-Endpunkt. Der Abruf meldet solche Lücken im Manifest
  (`dividend_gaps`) und die Gegenprobe beider Quellen (`dividend_crosscheck`).
- Nur Bars der **regulären Handelszeit des jeweiligen Tages**: 09:30–16:00 America/New_York, an
  NYSE-Halbtagen bis 13:00 (`localtrader/calendars.py`, für 2016–2025 gegen das Handelsvolumen in den
  Daten geprüft). Tage nach dem bekannten Kalenderende (2026-12-31) gelten als nicht handelbar.
  Außerbörsliche Bars sind dünn gehandelt und bei Alpaca nur mit Limitorders handelbar.
- **Holdout:** Der finale unabhängige Testzeitraum beginnt `2025-11-01T00:00:00Z` (`[holdout]` in
  `config/default.toml`), festgelegt und committet, bevor irgendein Ergebnis auf diesen Daten berechnet
  wurde. Beim Import werden alle Zeilen, die nicht vollständig vor dieser Grenze verfügbar sind
  (`received_at`), vor jeder Qualitätsprüfung verworfen; im Manifest steht nur ihre Anzahl, nie ein Preis.
  Dividenden ab dem Holdout-Tag werden ebenfalls nicht geladen. `--use-holdout` ist derzeit eine im
  Report protokollierte **Konvention, keine technische Zugriffskontrolle**; eine zentrale Kontrolle mit
  Nutzungszähler ist für T-008 geplant.
- **Verwahrung (Benutzerentscheidung 2026-11-18, Methodenreview zu #22):** Forschungsdateien enthalten
  physisch keine Holdout-Daten. `fetch-intraday` lädt Kurse, bereinigte Kurse und Corporate Actions
  höchstens bis vor den `[holdout] start` und lehnt ein späteres Ende ab; die früheren Vollkopien wurden
  gelöscht. Der Holdout wird erst im zentralen T-008-Lauf frisch geladen, nachdem der eine Versuch im
  Versuchsjournal reserviert ist; der Benutzer gibt diesen Lauf als Verwahrer frei.
- Der DAX bleibt als zweiter, kleiner Datensatz erhalten.

## Alternativen

- DAX mit anderem Broker: keine frei verfügbaren, langen 15-Minuten-Daten bekannter Herkunft.
- BTC/ETH über Alpaca: 24/7-Handel und andere Kostenstruktur; später als eigenes Instrument möglich.
- IEX-Feed: nur ein Handelsplatz, deutlich weniger Volumen in den Bars; SIP ist repräsentativer.

## Konsequenzen

- Preise sind in USD, das Konto rechnet in EUR. Bis T-011 (Währungen) werden USD 1:1 als
  Kontowährung behandelt. Renditen sind davon nicht betroffen, absolute Beträge schon.
- Strategien sehen Rohkurse; am Ex-Tag erscheint die Dividende als kleine Eröffnungslücke (etwa 0,3–0,6 %).
  Das entspricht dem, was der Livebetrieb sieht. Signale auf bereinigten Kursen bei Ausführung auf
  Rohkursen wären eine spätere, eigene Entscheidung.
- Der Halbtagskalender muss jährlich ergänzt werden; ohne Ergänzung wird ab 2027 nicht gehandelt.
- SPY und QQQ sind stark korreliert (gleiche Korrelationsgruppe in der Konfiguration).
- T-012 ist damit in der Instrumentenfrage entschieden; der Broker-Preflight bleibt offen.
