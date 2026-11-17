## 2026-11-18 – T-008 Bausteine: Protokollprüfung, Versuchsjournal, Modellregistry

**Anlass/Ziel:** Aus dem Auftrag in PR #22 die klar abgegrenzten Bausteine umsetzen (Benutzerauftrag,
Denkstufe medium). Die kritische Validierungslogik (Datums-/Verfügbarkeitssplits, Purge/Embargo,
Holdout-Zugriff, Auswahl- und Bootstrap-Statistik) ist **nicht** enthalten und bleibt für High geplant.

**Änderungen**
- `localtrader/research_protocol.py`: Struktur- und Text/JSON-Konsistenzprüfung des T-008-Protokolls
  (Falten, Holdout-Grenze, Kandidaten gegen die Strategiefactory, Nachbarschaften, Budgetarithmetik,
  Zeitstopps, Bootstrap-Angaben); `assert_startable` sperrt den Start bei jeder Abweichung, solange der
  Zustand nicht `frozen` ist oder kein Freeze-Beleg existiert; SHA-256-Belege über exakte Dateibytes
  ohne Selbstbezug.
- `localtrader/attempt_journal.py`: append-only Versuchsjournal mit Hash-Kette, exklusiver Reservierung
  je Versuchs-ID, erlaubten Statusübergängen, Gründen für Fehler/Abbruch, Budgets aus dem Protokoll
  (39/195/1) und getrennter Zählung von Forschung und zentraler Auswertung. Reservierung ist atomar nur
  innerhalb eines Dateisystems (keine globale Sperre).
- `localtrader/model_registry.py`: unveränderliche Modellversionen mit Pflichtfeldern, Cash-Champion
  `CASH-EUR-v1`, Aktivierung nur in Shadow und nur nach bestandenen Gates, Rücknahme nur nach bestätigtem
  kontrolliertem Abbau, atomarer Zeigerwechsel, Audit-Log. Paper/Live sind hier nicht aktivierbar.

- Nach Review (`2c72d7f`): Freeze-Beleg wird geladen und gegen Protokoll-/Planbytes, Pflichtbindungen
  und Laufinputs geprüft; Journal erkennt ein abgeschnittenes Ende (Head + einmalige Statusmarker) und
  verhindert Neustarts; Registry-Zeigerwechsel als Intent/Commit-Transaktion mit `recover()`.

**Tests:** `tests/test_t008_infrastructure.py` (19 Tests): echtes Protokoll konsistent und nicht startbar;
Abweichungen in Faltendaten, Zeitstopp, Seed, Blocklänge und Kandidaten-ID sperren; Strukturfehler;
Holdout-Abgleich; Belege; Journal-Lebenszyklus, Budget, Eindeutigkeit, Manipulation; Registry.
