# T-008 ausführen

Der Forschungsaufruf und der Verwahreraufruf sind getrennte Programme. Der integrierte Methodenentwurf sperrt beide bereits vor dem Lesen einer Deployment-Datei. Die folgenden Schritte beschreiben den Betrieb nach unabhängigem Klasse-K-Review, Integration und separatem Freeze. Die Offline-Tests erzeugen ihre Eingaben selbst.

## Kosten und Konto

Jedes simulierte Portfolio startet mit 10.000 EUR. Das Budget beträgt 1.200 EUR/Jahr für zeitnahe konsolidierte Kursdaten und 120 EUR/Jahr für Betrieb. Die historischen Abrufkosten und die zusätzlichen jährlichen Strategiekosten betragen je 0 EUR. Die Annahmen und Quellen stehen in `config/t008_economics.json`. Die laufenden Kosten gelten für jedes Portfolio, einschließlich Cash, anteilig pro Kalendertag und mit tatsächlicher Jahreslänge. Auch zwischen Testfalten trägt das fortgeführte Kapital diese Kosten.

Der Simulator kauft bei einem USD-Fehlbetrag die fehlende Währung, behält USD-Verkaufserlöse und tauscht am Ende eines Bewertungsfensters liquide USD zurück. Ausschüttungen zählen ab dem Ex-Tag als Nettoforderung, Geld wird erst am Zahltag verfügbar. Der am Ereigniszeitpunkt verfügbare EZB-Kurs bewertet das Portfolio. Der Gebührenposten für Währungsumtausch fällt je tatsächlicher Konversion an. Gebühren-, Spread- und Slippageparameter kommen aus der datierten Kostentabelle.

## Freeze vorbereiten

1. Der Verwahrer klärt die im Versuchsplan genannten historischen Expositionen, die SPY-Lücken, die fehlenden Zahltage und das QQQ-Duplikat anhand eigener Belege. Er stellt in der Forschungsumgebung physisch getrennte, beschnittene Dateien bereit. Die Finanzierungsannahmen und das Inventar früherer Versuche gehören in die Prüfung vor dem Freeze.
2. Forschung und Verwahrer erhalten getrennte, dauerhafte Journalverzeichnisse. Je Autorität einmalig `AttemptJournal(root, experiment_id, Budget.from_protocol(protocol))` zur Provisionierung verwenden. Für den Verwahrer gehören Experimentverzeichnis, `attempts/` und `budget/` dem Verwahrerkonto und haben Modus 0700. Forschung hat keinen Zugriff auf Verwahrerjournal, privaten Schlüssel, Providerzugang oder ungekürzte Daten.
3. Die geprüfte Protokollfassung trägt den Zustand `frozen` und den Pfad zu einem externen Freeze-Beleg unter `freeze.binding_receipt`. Erst nach Integration dieses Protokolls erzeugt der Verwahrer mit `research_protocol.make_receipt` den Beleg über die endgültigen Plan- und Protokollbytes und alle benannten Bindungen. Er verwahrt das Original und dessen Digest außerhalb des Forschungszugriffs. Der Code-SHA ist der tatsächlich integrierte Commit. Diese Reihenfolge vermeidet einen selbstreferenziellen Commit.
4. Beide Programme aus dem Stamm eines sauberen Checkouts dieses Commits starten. Anbieterzugänge, Freigaben, Journale und wirtschaftliche Berichte bleiben außerhalb von Git.

### Forschungsexporte vorab prüfen

Vom Verwahrer bereitgestellten Forschungsordner als Eingabe verwenden:

```bash
python -m localtrader.t008_preflight /absoluter/pfad/forschung
```

Der Vorabcheck liest die fünf unten aufgeführten CSV-Dateien, prüft das Exportformat, die
chronologische Reihenfolge, den Holdout-Stichtag und die Veröffentlichungszeit der EZB-Kurse.
Er gibt Zeilenzahl, Zeitspanne und Prüfsummen der geprüften Dateibytes sowie die beiden zugehörigen
Datenbindungen aus. Er schreibt keine Kopien, startet keinen Versuch und fragt keinen Provider ab.
Die Ausgabe kann außerhalb von Git als Belegentwurf abgelegt werden. Vor dem Freeze sind zusätzlich
die vollständige Daten- und Kalenderabdeckung, die Provider-Ausnahmen und die Verwahrerattestierung
zu prüfen. Der Vorabcheck bestätigt diese Punkte nicht.

## Forschung

`python -m localtrader.t008 DEPLOYMENT.json`

| Feld | Inhalt |
| --- | --- |
| `bindings` | Bindungen des geprüften externen Belegs |
| `files` | Abbildung von `QQQ_15m.csv`, `SPY_15m.csv`, beiden `<Symbol>_dividends.csv` und `EURUSD_ecb.csv` auf kanonische absolute Pfade der Forschungsexporte |
| `journal_root` | Elternverzeichnis des vorprovisionierten Experimentjournals |
| `prior_inventory` | Geprüfte JSON-Datei mit eindeutigen `unique_same_family_strategy_slot_ids` und Begründung der Ausschlüsse |
| `custody_attestation` | Verwahrerdokument mit identischer Dateizuordnung, `physical_research_only`, `historical_exposure_resolved`, `material_exceptions_resolved`, konkreten Belegen in `provider_exceptions_evidence` und dem Ergebnis von `t008.journal_identity(root, experiment_id)` unter `research_journal_identity` |

Der zusätzliche Belegeintrag `research_journal_path_digest` ist SHA-256 über den UTF-8-kodierten aufgelösten Journalwurzelpfad. Einzeldateien werden über ihre Bytes gebunden, die Zusammenfassung mehrerer Digests verwendet `t008.digest_map`. Die konkreten Bindungsberechnungen sind in `run_research` implementiert und im synthetischen Operatortest nachgebildet.

Das Programm erzeugt unter `<journal_root>/<experiment_id>/research-output/` exklusive Dateien für Start, einzelne Slots, die Auswahl vor Öffnung der Testfalten und das Schlussurteil. Das Experimentregister liegt im Unterordner `experiments/`. Fehlgeschlagene Versuche zählen im Journal. Ein unvollständiger Lauf ist nicht entscheidbar. Nach einem Fehler das Journal und die bestehende Auswertung prüfen. Ein erneuter Aufruf, ein neues Journal oder ein neuer Seed sind kein genehmigter Ersatzversuch.

## Zentraler Einmalversuch

Eine bestandene Forschungsauswertung ist Voraussetzung für die separate Freigabe durch den Benutzer. Vor dem Freeze bindet der Verwahrer die **Bytes** der privaten JSON-Policy an `central_single_journal_and_release_procedure_digest`. Sie enthält `custodian_uid`, `public_key_pem`, `journal_root`, `journal_identity` (Verzeichnisidentitäten wie oben), `canonical_slot`, `research_verdict` (Pfad), `registry_root` und `credentials` (Pfad zur privaten Provider-Konfiguration). Der externe Beleg und sein Verzeichnis müssen vor fremden Schreibzugriffen geschützt sein.

Nach dem Forschungsurteil prüft der Benutzer den Kandidaten und die F3-Trainingsgewichte und signiert die exakten Bytes der separaten Freigabe mit seinem privaten Schlüssel (OpenSSL DGST, SHA-256). Die JSON-Freigabe nennt `approved`, `receipt_digest`, `integrated_sha`, `research_verdict_sha256`, `candidate`, `benchmark_weights`, `period` (beide UTC-Grenzen aus dem Protokoll) und `slot`.

`python -m localtrader.t008_central POLICY.json RELEASE.json SIGNATURE.bin`

Der Abruf beginnt nach der irreversiblen Reservierung. Fehler oder Absturz verbrauchen den Versuch. Der Verwahrer bewahrt die vorab benannten Aggregate und Eingangs-Digests im privaten Journal auf. Die Modellregistry dokumentiert die Entscheidung. Ein bestandener Lauf qualifiziert höchstens für Shadow. Bei einem Schreibfehler nach dem zentralen Urteil sind Urteil, Journal, Registry-Audit und vorhandene Registrierungsdatei beim Verwahrer abzugleichen. Die Wiederherstellung verwendet diese Belege ohne erneuten Providerabruf.
