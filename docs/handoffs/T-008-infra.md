# Übergabe T-008 (Teil) – Protokollprüfung, Versuchsjournal, Modellregistry

- **Task-ID / Issue:** T-008 / #4 (nur diese Bausteine; Claim für T-008 liegt bei Codex)
- **Agent / Modell / Denkstufe:** Claude Code / claude-opus-5-5 (konfiguriert) / medium
- **Branch:** `agent/T-008-journal-registry`, Basis `agent/T-008-research-design` (#22)
- **Stand:** review (PR #23)

## Erledigt
- `research_protocol.py`: Struktur-/Textprüfung; Start nur mit `state=frozen` und einem existierenden
  Freeze-Beleg, der die exakten Protokoll-/Planbytes, alle `freeze.required`-Bindungen (Hex-Digests) und
  die beim Start vorgelegten Laufinputs bindet (`make_receipt`, `check_receipt`, `assert_startable`).
- `attempt_journal.py`: Hash-Kette, `head.json` und einmalige Statusmarker; abgeschnittenes Ende oder
  Marker ohne Ereignis blockieren (fail closed); kein Neustart abgeschlossener/fehlgeschlagener Versuche.
- `model_registry.py`: Zeigerwechsel als Transaktion (Intent → Zeiger → Commit); unbestätigter Zeiger
  wird von `active()` verweigert, `recover()` stellt den vorherigen Zeiger her.
- Review 1 (`2c72d7f`): alle drei Befunde mit Regressionen behoben.
- Review 2 (`0ba43f6`): `recover()` stellt den letzten bestätigten Zeiger aus dem Audit-Log mit seiner
  ursprünglichen Transaktions-ID wieder her (nie einen Intent-`previous`); idempotent auch bei
  wiederholten Abstürzen während der Wiederherstellung.

## Grenzen (bewusst, dokumentiert)
- Kein externer Anker: Wer Journal, Marker und Head konsistent umschreibt, wird nicht erkannt.
- Reservierung und Statusmarker sind nur innerhalb eines Dateisystems atomar; der zentrale
  Holdout-Dienst braucht eine eigene transaktionale Reservierung (High).
- Der Freeze-Beleg selbst wird noch nicht unabhängig signiert; sein Ablageort/Commit wird im
  Preregistrierungscommit festgelegt (T-008, High).

## Verbleibende Schritte (High, T-008)
Datums-/Verfügbarkeitssplits, Purge/Embargo, Holdout-Zugriffsdienst, Auswahl- und Bootstrap-Statistik,
Ledger/FX; Anbindung dieser Bausteine an den Lauf.

## Prüfungen
- `make check` lokal bestanden.
