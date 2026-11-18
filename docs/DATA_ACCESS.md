# Externe Datenzugänge

Offline-Fixtures benötigen keinen API-Key. Der vorhandene Alpaca-Paper-Key kann außerdem historische Marktdaten abrufen und wird nur aus der lokalen `.env` gelesen. Secrets werden weder an Ollama übergeben noch protokolliert.

Der lokale Ollama-Dienst benötigt ebenfalls keinen API-Key. Die Anwendung akzeptiert dafür ausschließlich Loopback-Adressen (`127.0.0.1`, `localhost`, `::1`).

Für belastbare Forschung werden später folgende Zugänge empfohlen. Zugangsdaten gehören ausschließlich in eine lokale, ignorierte `.env` oder in Umgebungsvariablen und dürfen nie im Chat, Repository oder Log erscheinen.

## Aktuell angebunden: Alpaca

- Aktien-/ETF-Historie über IEX oder SIP; beim Basic-Plan sind aktuelle SIP-Daten eingeschränkt, deshalb endet jede automatische Historienanfrage mindestens 15 Minuten in der Vergangenheit.
- Kryptohistorie für BTC/USD und ETH/USD.
- Sicherheitslimit 180 API-Anfragen pro Minute, Pagination, Retry mit Provider-Cooldown und persistente Wiederaufnahme.
- Der aktuelle Katalog ist bewusst allowlisted; beliebige vom LLM erfundene Symbole oder URLs werden abgelehnt.

- 15-Minuten-ETF-Historie für die Intraday-Engine: `fetch-intraday` (T-018, ADR 0005, `docs/DATA_FORMAT.md`).

## Noch sinnvoll für belastbare Forschung

1. **Point-in-time US-Aktien- und ETF-Daten**
   - Erforderlich: OHLCV, Splits, Dividenden, Delistings, Symbolhistorie, Börsenkalender und möglichst historisches Index-/Universums-Membership.
   - Geeignete Anbieterklasse: Polygon, Alpaca Market Data, Nasdaq Data Link oder ein institutioneller Point-in-time-Anbieter.
   - Benötigte Variable, anbieterspezifisch: beispielsweise `MARKET_DATA_API_KEY`.

2. **BTC-/ETH-Spotdaten**
   - Erforderlich: OHLCV einer klar benannten Spotbörse, Gebührenhistorie beziehungsweise konservative Kostenannahmen und 24/7-Zeitstempel.
   - Öffentliche historische Endpunkte vieler Börsen benötigen für reine Bars keinen Schlüssel. Für höhere Limits kann beispielsweise `CRYPTO_DATA_API_KEY` erforderlich sein.

## Optional: externe Paper-Broker-Sandbox

- Nur nach manueller Freigabe und ausschließlich Paper/Sandbox.
- Erforderlich: Paper-API-Key, Paper-Secret, explizite Paper-Basis-URL und eine verifizierte Paper-Konto-ID.
- Der MVP verwaltet keine Zugangsdaten und enthält noch keinen Broker-Transport.
- Live-Hosts werden vom Sicherheitswächter abgelehnt.

## Später P2

- lizenzierte News-API mit präzisen Veröffentlichungs- und Empfangszeitpunkten;
- regulatorische Primärquellen wie SEC EDGAR (in der Regel ohne API-Key, aber mit konformem User-Agent);
- Point-in-time-Fundamentaldaten und Sektorhistorie;
- Shortseller-/Eventquellen nur im Research- und Shadow-Modus.

Vor Auswahl eines kostenpflichtigen Anbieters sollte zuerst Datenabdeckung, Lizenz, Point-in-time-Eignung, Rate Limits und Exportmöglichkeit geprüft werden. Ein kostenloser End-of-day-Feed reicht für einen Funktions-Smoke-Test, aber nicht automatisch für survivorship-bias-freie Forschung.
