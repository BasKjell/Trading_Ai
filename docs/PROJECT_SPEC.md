# Projektspezifikation

Verbindliche Anforderungen des Benutzers (Stand 2026-11-17, übernommen aus den Projektanweisungen).
Änderungen an dieser Datei nur auf ausdrückliche Benutzerentscheidung; Datum und Anlass im CHANGELOG
vermerken. Die frühere Codex-Spezifikation liegt nur noch als Archiv unter
`docs/archive/CODEX_MASTER_PROMPT.md` und ist **nicht** mehr verbindlich (ADR 0004).

Umsetzungsstand: [`docs/STATUS.md`](STATUS.md). Arbeitsregeln: [`AGENTS.md`](../AGENTS.md).

---

Du bist verantwortlich für die selbstständige Entwicklung eines Trading-Systems mit kontrollierter, datenbasierter Weiterentwicklung. Du arbeitest gemeinsam mit anderen Entwicklern.

Arbeite als erfahrener Python-Entwickler, Quant Research Engineer und Test Engineer. Implementiere das System vollständig, statt nur Architekturvorschläge oder Codegerüste zu liefern.

## 1. Ziel und Grenzen

Entwickle einen Trading-Bot, der:

- standardmäßig alle 15 Minuten abgeschlossene Marktdaten auswertet,
- nur bei einem ausreichend belastbaren Signal handelt,
- Positionen je nach Strategie über Stunden bis Tage halten kann,
- Strategien historisch prüft und im Paper Trading ausführt,
- neue Modellvarianten automatisch trainiert und bewertet,
- seine Leistung und sämtliche Entscheidungen nachvollziehbar dokumentiert.

Forschungsziele:

- durchschnittlich 1–2 % monatliche geometrische Rendite nach sämtlichen Handels- und Betriebskosten, vor persönlichen Steuern,
- möglichst höchstens 15 % Drawdown.

Diese Ziele sind keine Zusagen und keine zwingenden Abnahmekriterien. Ein legitimes Ergebnis lautet: „Mit den verfügbaren Daten wurde keine belastbare profitable Strategie gefunden.“

Erzwinge die Renditeziele niemals durch höheren Hebel, veränderte Risikolimits, geschönte Ausführungen oder selektive Berichterstattung.

Ein Drawdown-Limit ist ein Kontrollziel, keine garantierte Verlustobergrenze.

## 2. Selbstständige Arbeitsweise

1. Untersuche zuerst das vorhandene Repository und seine Anweisungen.
2. Nutze vorhandene geeignete Komponenten.
3. Erstelle einen kurzen Implementierungsplan und setze ihn anschließend um.
4. Stelle nur Rückfragen, wenn eine Entscheidung tatsächlich blockiert.
5. Triff ansonsten konservative, dokumentierte und konfigurierbare Annahmen.
6. Führe Tests aus und behebe gefundene Fehler.
7. Liefere eine lokal startbare Anwendung, nicht nur Pseudocode.
8. Dokumentiere verbleibende Grenzen offen.
9. Kaufe keine Daten, buche keine Dienste und führe keine echten Orders ohne ausdrückliche Freigabe aus.
10. Verändere keine unbeteiligten Projekte oder Dateien.

Fehlende Zugangsdaten dürfen die Entwicklung nicht blockieren. Implementiere Adapter, lokale Datenimporte, Testdaten und einen Offline-Demomodus. Kennzeichne synthetische Daten klar und verwende sie niemals als Nachweis wirtschaftlicher Profitabilität.

## 3. Konfigurierbare Ausgangsannahmen

Verwende zunächst:

- Python und Docker Compose,
- simuliertes Startkapital: 10.000 EUR,
- Handelsmodus: Paper Trading,
- primäres Signalintervall: 15 Minuten,
- Vergleichsintervalle: 1 Stunde und 1 Tag,
- Long-only und ungehebelt,
- wenige liquide Instrumente,
- keine Optionen, Futures, Leerverkäufe oder Kreditfinanzierung im MVP.

Lege das Instrumentenuniversum über Konfiguration fest.

Implementiere den Kern unabhängig von Broker und Anlageklasse. Wähle für den MVP einen konkreten Datenadapter und einen Brokeradapter mit dokumentierter API und begründe die Auswahl anhand von Verfügbarkeit, Gebühren, Datenqualität und Eignung.

Prüfe aktuelle offizielle API-Dokumentation und Lizenzen der verwendeten Komponenten.

Falls kein ausreichend geeigneter kostenloser historischer Datenzugang existiert, implementiere einen klar definierten CSV-/Parquet-Import. Behaupte keine ausreichende Datenbasis, wenn sie nicht vorhanden ist.

## 4. Architektur

Trenne mindestens:

- Konfiguration und Validierung,
- Marktdatenaufnahme,
- Datenqualitätsprüfung,
- Feature-Berechnung,
- Strategien und Signalbildung,
- Portfolio- und Risikosteuerung,
- Orderverwaltung und Brokeradapter,
- Backtesting,
- Paper Trading,
- Forschung und Modelltraining,
- Modellregistrierung und Versionsverwaltung,
- Reporting und Monitoring.

Nutze gemeinsame Strategie-, Risiko- und Orderlogik für Backtesting und Paper Trading, damit sich die Systeme nicht fachlich auseinanderentwickeln.

Halte die Architektur überschaubar. Ein modularer Dienst ist für den MVP ausreichend; keine unnötige Microservice-Landschaft.

Geeigneter Ausgangspunkt:

- Parquet für historische Marktdaten,
- PostgreSQL für Orders, Trades, Zustände und Experimente,
- eine einfache lokale Weboberfläche,
- strukturierte Logs und Healthchecks.

Wähle konkrete Bibliotheken nach Eignung und Wartbarkeit. Fixiere Abhängigkeiten reproduzierbar.

## 5. Datenqualität und zeitliche Korrektheit

Speichere mindestens:

- Instrument und Handelsplatz,
- Ereigniszeit und Verfügbarkeitszeit, soweit vorhanden,
- OHLCV,
- Datenquelle,
- Zeitzone,
- Datenversion.

Anforderungen:

- intern UTC, zusätzlich korrekte Handelskalender und lokale Börsenzeiten,
- ausschließlich abgeschlossene Kerzen zur Signalberechnung,
- Erkennung von Duplikaten, Lücken und unplausiblen Preisen,
- keine stillschweigende Auffüllung fehlender handelbarer Preise,
- dokumentierter Umgang mit Splits, Dividenden und Symboländerungen,
- Trennung von für Analyse adjustierten Preisen und tatsächlich handelbaren Preisen,
- historische Universumszusammensetzung berücksichtigen, falls verfügbar.

Wenn Survivorship Bias oder andere Datenprobleme nicht ausgeschlossen werden können, kennzeichne Ergebnisse entsprechend und begrenze die daraus abgeleiteten Aussagen.

Alle Features dürfen ausschließlich Informationen verwenden, die zum Entscheidungszeitpunkt verfügbar waren.

## 6. Erste Strategien

Implementiere zunächst zwei einfache, nachvollziehbare Strategiehypothesen.

### A. Trendfolge

Beispielhafte Komponenten:

- Trendfilter aus einem höheren Zeitintervall,
- Einstieg nach Ausbruch oder Rücksetzer,
- volatilitätsabhängige Positionsgröße und Stop-Distanz,
- klar definierter Ausstieg,
- optionaler Zeitstopp.

### B. Mean Reversion

Beispielhafte Komponenten:

- messbare Abweichung von einem gleitenden Referenzwert,
- Filter zur Vermeidung ausgeprägter Trendphasen,
- Rückkehr zum Referenzwert als möglicher Ausstieg,
- Verlustbegrenzung und maximale Haltedauer.

Halte Parameterzahl und Suchräume klein. Begründe die wirtschaftliche Hypothese jeder Strategie.

Implementiere zusätzlich:

- Cash als Referenz,
- Buy-and-hold des jeweiligen Instruments beziehungsweise einen geeigneten Portfoliovergleich,
- Auswertung von Rendite, Risiko, Marktexposure und Kapitalbindung.

Alle 15 Minuten auswerten bedeutet nicht, alle 15 Minuten handeln zu müssen. Nicht zu handeln ist eine zulässige Entscheidung.

## 7. Backtesting und Kostenmodell

Implementiere eine ereignisbasierte oder vergleichbar zeitlich korrekte Simulation.

Berücksichtige:

- Brokergebühren und Mindestgebühren,
- Börsen- und regulatorische Gebühren, soweit relevant,
- Bid-Ask-Spread,
- Slippage,
- Währungsumrechnung,
- Teilfüllungen und nicht ausgeführte Orders,
- verfügbare Liquidität und Volumenbegrenzungen,
- laufende Daten- und Betriebskosten im Gesamtreport.

Verhindere insbesondere:

- Ausführung zum Schlusskurs derselben Kerze, wenn das Signal erst mit diesem Schlusskurs entsteht,
- automatische Limitorder-Füllungen nur aufgrund eines berührten Kerzenpreises,
- unrealistische Stop-Ausführungen bei Kurslücken,
- Nutzung zukünftiger Hochs, Tiefs oder Volumina.

Wenn innerhalb einer Kerze sowohl Stop als auch Gewinnziel erreicht werden könnten:

- verwende feinere Daten oder
- eine dokumentierte konservative Annahme.

Unterscheide modellierte Ausführungskosten von tatsächlich gemessenen Kosten.

Prüfe mindestens:

- Basiskosten,
- doppelte variable Handelskosten,
- verzögerte Ausführung,
- vergrößerte Spreads,
- Parameterabweichungen,
- unterschiedliche Marktphasen.

## 8. Validierung und Schutz vor Overfitting

Nutze chronologische Walk-forward-Validierung:

1. Training auf vergangenen Daten.
2. Parameterwahl auf nachfolgenden Validierungsdaten.
3. Bewertung auf anschließendem Testabschnitt.
4. Wiederholung über mehrere Zeitfenster.

Keine zufällige Aufteilung von Zeitreihen.

Passe Normalisierung, Feature-Auswahl und Modelle ausschließlich auf Trainingsdaten an.

Berücksichtige bei überlappenden Ziel- oder Haltedauern geeignete zeitliche Abstände zwischen Datensätzen, damit keine Information über die Grenzen gelangt.

Reserviere einen abschließenden unangetasteten Testzeitraum:

- nicht zur wiederholten Strategieentwicklung verwenden,
- jede Verwendung protokollieren,
- nach Einsicht nicht weiter als unabhängig bezeichnen.

Protokolliere sämtliche getesteten Varianten, einschließlich verworfener Ergebnisse. Bewerte den Einfluss mehrfacher Strategietests.

Zeige Unsicherheit der Kennzahlen, beispielsweise mit geeignetem Block-Bootstrapping. Behandle korrelierte Trades nicht als unabhängige Beobachtungen.

Ein positiver Backtest allein reicht nicht zur Freigabe.

## 9. Kontrollierte Selbstverbesserung

Implementiere einen separaten Forschungsprozess.

Dieser darf:

- vorab erlaubte Parameter untersuchen,
- Modelle auf neuen Daten nachtrainieren,
- einfache ML-Modelle gegen Regelstrategien vergleichen,
- Marktphasenfilter testen,
- Strategiegewichtungen innerhalb fester Grenzen vorschlagen.

Beginne mit einfachen Modellen. Reinforcement Learning und große neuronale Modelle sind keine MVP-Anforderung.

Verwende ein Champion-Challenger-Verfahren:

- Champion: aktuell freigegebene Version.
- Challenger: neue Kandidatenversion.
- Beide werden auf identischen Daten und mit denselben Kostenannahmen verglichen.
- Ein Challenger läuft anschließend im Shadow-Modus ohne eigene echte Orders.

Definiere vor dem jeweiligen Experiment verbindliche Bewertungskriterien:

- Rendite nach Kosten,
- Drawdown,
- Stabilität über mehrere Zeitfenster,
- Handelsaktivität und Gebührenbelastung,
- Sensitivität gegenüber Annahmen,
- Umfang und Aussagekraft der verfügbaren Daten.

Speichere diese Kriterien versioniert. Passe sie nicht nachträglich an, um einen Kandidaten bestehen zu lassen.

Eine neue Version darf automatisch im Paper Trading aktiviert werden, wenn die vorher definierten Kriterien erfüllt sind. Für Liveversionen ist eine ausdrückliche menschliche Freigabe erforderlich.

Erlaube als reguläres Ergebnis:

„Kein Kandidat ist ausreichend besser; bestehende Version bleibt aktiv.“

Der Forschungsprozess darf niemals:

- Risikolimits erhöhen,
- Live-Trading aktivieren,
- Brokerberechtigungen verändern,
- Gebühren aus der Bewertung entfernen,
- Testdaten in Trainingsdaten umdeklarieren,
- Produktionscode selbstständig im laufenden Livebetrieb überschreiben.

Speichere je Version:

- Code-Commit,
- Datenversion,
- Features und Parameter,
- Trainingszeitraum,
- Validierungsberichte,
- Abhängigkeiten,
- Zufallsseeds,
- Aktivierungsentscheidung und Begründung.

Unterstütze Rollback auf eine zuvor freigegebene Version. Ein Rückwechsel darf laufende Positionen nicht unkontrolliert verändern.

## 10. Unabhängige Risikosteuerung

Setze sämtliche Orders durch eine eigenständige Risikokomponente durch.

Vorläufige, konfigurierbare Paper-Trading-Startwerte:

- geplantes Risiko pro Trade: höchstens 0,20 % des aktuellen Kontowerts,
- gesamtes geplantes Stop-Risiko offener Positionen: höchstens 1 %,
- Positionswert je Instrument: höchstens 20 %,
- gesamte Bruttoexposition: höchstens 100 %,
- kein geliehenes Kapital.

Positionsgrößen müssen gleichzeitig Stop-Distanz, Gebühren, verfügbare Liquidität, Mindestordergrößen und Expositionsgrenzen berücksichtigen.

Geplantes Stop-Risiko ist keine Garantie für den tatsächlichen Verlust.

Beispielhafte Drawdown-Regeln:

- ab 5 %: Risikobudget halbieren,
- ab 8 %: keine neuen Positionen,
- ab 10 %: kontrollierten Abbau auslösen und manuelle Prüfung verlangen.

Implementiere diese Werte als vorläufige Schutzparameter, nicht als nachgewiesen optimale Einstellungen.

Definiere eindeutig:

- High-Water-Mark des Kontowerts,
- Behandlung offener Gewinne und Verluste,
- Bereinigung um Ein- und Auszahlungen,
- Bedingungen für erneute Handelsfreigabe.

Zusätzlich:

- Tagesverlustgrenze,
- Begrenzung korrelierter Positionen,
- keine Martingale- oder Verlustverdopplungslogik,
- kein Hochsetzen des Risikos zur Erreichung einer Monatsrendite,
- kein automatischer Neustart nach einer Risikosperre.

Bei Daten- oder Verbindungsproblemen neue Orders sperren. Bestehende Schutzorders nicht blind löschen. Positionsabbau und Fehlerbehandlung müssen den tatsächlichen Brokerzustand berücksichtigen.

## 11. Zuverlässige Orderverwaltung

Implementiere einen persistenten Orderzustandsautomaten.

Berücksichtige:

- eindeutige Client-Order-IDs,
- Schutz gegen doppelte Orders,
- Teilfüllungen,
- Ablehnungen,
- Stornierungen,
- Netzwerkabbrüche,
- Timeouts mit unbekanntem Ausführungsstatus,
- Neustarts mit offenen Orders und Positionen.

Nach einem Timeout darf eine Order nicht blind erneut gesendet werden. Zuerst Brokerzustand abgleichen.

Führe beim Start und regelmäßig einen Abgleich durch zwischen:

- internem Positionsbestand,
- Brokerpositionen,
- offenen Orders,
- verfügbarem Cash.

Bei ungeklärten Abweichungen Handel sperren und Alarm auslösen.

## 12. Betriebsmodi und Sicherheit

Implementiere klar getrennt:

- backtest,
- paper,
- shadow,
- live.

Standard ist paper.

Live-Trading bleibt technisch gesperrt, bis:

- eine explizite Livekonfiguration vorliegt,
- geeignete Zugangsdaten vorhanden sind,
- eine dokumentierte menschliche Freigabe vorliegt.

Ein einfacher Neustart oder ein trainiertes Modell darf diese Sperre nicht aufheben.

Speichere Geheimnisse ausschließlich über geeignete Umgebungsvariablen oder Secret-Dateien. Keine Secrets im Repository oder in Logs.

Brokerberechtigungen auf das notwendige Minimum beschränken; keine Auszahlungsberechtigungen.

Dashboard standardmäßig nur lokal erreichbar machen.

## 13. Dashboard und Reporting

Zeige mindestens:

- aktuellen Modus,
- aktive Strategie- und Modellversion,
- Datenfrische,
- Verbindungsstatus,
- Kontowert und Cash,
- offene Positionen und Orders,
- realisierte und unrealisierte Ergebnisse,
- Gebühren und geschätzte Slippage,
- aktuellen und maximalen Drawdown,
- aktive Risikosperren,
- letzte Signale mit Begründung,
- Forschungsversuche und Modellvergleiche.

Historische Reports sollen enthalten:

- Nettorendite,
- geometrische Monatsrendite,
- Monatsrenditen als Zeitreihe,
- maximale Verlustphase und Drawdown-Dauer,
- Volatilität sowie risikoadjustierte Kennzahlen,
- Anzahl der Trades und Haltedauer,
- Trefferquote und durchschnittlichen Gewinn/Verlust,
- Turnover und Kostenbelastung,
- Vergleich mit Benchmarks,
- Ergebnisse je Testfenster,
- Sensitivitätsanalysen,
- bekannte Daten- und Simulationsgrenzen.

Kennzeichne annualisierte Kennzahlen aus kurzen Zeiträumen deutlich.

## 14. Tests und Abnahmekriterien

Schreibe aussagekräftige automatisierte Tests für:

- zeitliche Datenverfügbarkeit und Look-ahead-Schutz,
- Kerzenabschluss und Handelskalender,
- Positionsgrößen und Risikolimits,
- Gebühren und Portfolio-Buchhaltung,
- Stop-Ausführungen bei Kurslücken,
- Teilfüllungen,
- doppelte und unbekannte Orderzustände,
- Neustart und Brokerabgleich,
- Trennung der Betriebsmodi,
- Sperre gegen unbeabsichtigtes Live-Trading,
- Modellaktivierung und Rollback.

Erstelle einen deterministischen Integrationstest:

Datenimport → Feature-Berechnung → Signal → Risikoprüfung →
simulierte Order → Ausführung → Portfolioaktualisierung → Report.

Prüfe zusätzlich einen Prozessabsturz mit anschließendem Wiederanlauf und offenen Orders.

Die technische Abnahme verlangt:

- reproduzierbaren lokalen Start,
- erfolgreiche relevante Tests,
- funktionierenden Offline-Demomodus,
- Backtest beider Basisstrategien,
- persistenten Paper-Trading-Betrieb,
- nachvollziehbare Risikosperren,
- mindestens einen vollständigen Champion-Challenger-Durchlauf,
- verständliches Dashboard und Dokumentation.

Eine profitable Strategie ist keine Voraussetzung für die technische Abnahme. Technische Funktionsfähigkeit darf nicht als Nachweis wirtschaftlicher Profitabilität dargestellt werden.

## 15. Lieferumfang

Liefere:

- vollständigen Quellcode,
- Docker-Compose-Konfiguration,
- reproduzierbare Abhängigkeiten,
- Beispielkonfiguration ohne Geheimnisse,
- Datenbankschema und Migrationen,
- dokumentiertes CSV-/Parquet-Importformat,
- automatisierte Tests,
- Offline-Demodaten,
- Backtesting- und Paper-Trading-Kommandos,
- Dashboard,
- Beispielreports,
- README mit Installation, Betrieb und Fehlerbehebung,
- kurze Architektur- und Risikoentscheidungen,
- Beschreibung des Freigabeprozesses für spätere Live-Nutzung.

Dokumentiere fehlende echte Zugangsdaten, Datenlizenzen oder andere externe Voraussetzungen konkret.

## 16. Umsetzung in Etappen

Arbeite in dieser Reihenfolge und fahre nach erfolgreichen Prüfungen selbstständig fort:

1. Repository prüfen und Plan erstellen.
2. Konfiguration, Datenmodelle und Offline-Demomodus.
3. Datenaufnahme und Qualitätsprüfungen.
4. Backtesting, Kostenmodell und Basisstrategien.
5. Risiko- und Orderverwaltung.
6. Paper Trading und Wiederanlauf.
7. Dashboard und Reporting.
8. Walk-forward-Forschung und Champion-Challenger-Prozess.
9. Integrationstests und Dokumentation.

Beginne mit einem durchgängigen, kleinen System und erweitere es anschließend. Vermeide eine große Sammlung unfertiger Komponenten.

Berichte zum Abschluss:

- was tatsächlich implementiert ist,
- welche Prüfungen ausgeführt wurden und bestanden sind,
- wie das System gestartet wird,
- welche Daten echt beziehungsweise synthetisch sind,
- welche Ergebnisse belastbar sind und welche nicht,
- welche externen Voraussetzungen noch fehlen.

## 17. Verbindliche Dokumentation, Tests und automatische GitHub-Synchronisation

Diese Regeln gelten für sämtliche Arbeiten im Repository https://github.com/BasKjell/Trading_Ai.git.

### Änderungen dokumentieren

Dokumentiere jede fachlich zusammenhängende Änderung in CHANGELOG.md:

- Datum,
- Anlass und Ziel,
- verändertes Verhalten und betroffene Komponenten,
- hinzugefügte oder angepasste Tests,
- tatsächlich ausgeführte Prüfungen und deren Ergebnisse,
- bekannte Einschränkungen und gegebenenfalls Rücknahmehinweise.

Aktualisiere bei Bedarf zusätzlich README, Architekturübersicht, Konfigurationsbeschreibung und Betriebsanleitung.

Dokumentiere wesentliche Architekturentscheidungen unter docs/decisions/. Beschreibe insbesondere Gründe, Alternativen und Konsequenzen.

Schreibe keine Geheimnisse, API-Schlüssel, Kontodaten oder vollständigen sensiblen API-Antworten in Dokumentation, Logs oder Commit-Nachrichten.

### Jede Änderung absichern

Erstelle für jede neue oder geänderte ausführbare Funktion aussagekräftige automatisierte Tests. Decke dabei ab:

- erwartetes Verhalten,
- relevante Grenzfälle,
- fehlerhafte Eingaben,
- Ausfälle externer Abhängigkeiten,
- Auswirkungen auf Daten, Risiko- und Orderzustände.

Ergänze bei jeder Fehlerbehebung einen Regressionstest, der den Fehler vor der Korrektur reproduziert.

Verwende Unit-Tests für isolierte Logik, Integrationstests für das Zusammenspiel von Komponenten und End-to-End-Tests für zentrale Abläufe.

Für reine Dokumentations- oder Konfigurationsänderungen verwende passende Prüfungen, beispielsweise Link-, Format-, Schema- und Startprüfungen. Erzeuge keine bedeutungslosen Tests nur zur Erhöhung der Testanzahl.

Tests müssen fachliches Verhalten prüfen und dürfen nicht lediglich die Implementierung nachbauen. Ändere erwartete Testergebnisse nicht, nur damit eine fehlerhafte Implementierung besteht.

### Prüfungen bei jeder Änderung erneut ausführen

Nach jeder abgeschlossenen zusammenhängenden Änderung und vor jedem Commit:

1. Formatierung prüfen.
2. Linter ausführen.
3. Typprüfung ausführen.
4. Die vollständige lokal ausführbare automatisierte Testsuite ausführen.
5. Relevante Build-, Konfigurations- und Startprüfungen ausführen.
6. Den endgültigen Diff auf unbeabsichtigte Änderungen und Geheimnisse prüfen.

Stelle dafür einen einheitlichen Befehl bereit, beispielsweise `make check`. Dieser muss bei jedem Fehler mit einem von null verschiedenen Exit-Code enden.

Nach jeder weiteren Codekorrektur sind diese Prüfungen erneut erforderlich. Berichte ausschließlich tatsächlich ausgeführte Ergebnisse.

Halte die Standardsuite deterministisch, ohne Brokerzugang, Internet oder echte Zugangsdaten ausführbar. Verwende kontrollierte Testadapter und synthetische Testdaten.

Separate externe Integrationstests dürfen nur lesende API-Abfragen oder ausdrücklich dafür konfigurierte Paper-Umgebungen verwenden. Tests dürfen niemals echte Orders auslösen.

Kennzeichne übersprungene oder wegen fehlender Infrastruktur nicht ausführbare Tests ausdrücklich. Stelle sie niemals als bestanden dar.

### Automatisch committen und pushen

Du bist ausdrücklich autorisiert, abgeschlossene und erfolgreich geprüfte Änderungen selbstständig zu committen und nach GitHub zu pushen. Frage dafür nicht bei jedem Commit erneut nach.

Eine Änderung bedeutet eine kleine, fachlich zusammenhängende und funktionsfähige Arbeitseinheit, nicht jeden einzelnen Speichervorgang.

Vorgehen:

1. Prüfe das Git-Remote und den aktuellen Branch.
2. Arbeite auf einem dedizierten Arbeitsbranch.
3. Prüfe den Diff und füge nur die zur Änderung gehörenden Dateien hinzu.
4. Committe mit einer präzisen Nachricht zu Zweck und Verhalten.
5. Pushe unmittelbar anschließend auf den zugehörigen Remote-Branch.
6. Prüfe, ob der Push erfolgreich war und die GitHub-Prüfungen bestanden sind.

Nutze beispielsweise Commit-Präfixe wie feat, fix, test, docs oder refactor.

Kein automatisches Merge nach main, kein Force-Push und kein Umschreiben bestehender Historie. Branch-Schutzregeln müssen eingehalten werden. *(Präzisiert durch §21: Merge durch einen Integrationsagenten nach unabhängigem Review ist erlaubt.)*

Fremde oder bereits vorhandene uncommittete Änderungen dürfen nicht versehentlich mitcommittet oder verworfen werden.

Wenn Tests fehlschlagen, korrigiere die Ursache und führe die Prüfungen erneut aus, bevor du commitest und pushst. Umgehe keine Hooks oder Checks.

Bei fehlendem GitHub-Zugang oder fehlgeschlagenem Push bewahre die Arbeit lokal und berichte die konkrete Blockade. Behaupte keine erfolgreiche Synchronisation.

### GitHub Actions verpflichtend

Richte einen Workflow ein, der bei jedem Push und jedem Pull Request dieselben verbindlichen Prüfungen ausführt.

- Nutze reproduzierbar festgelegte Abhängigkeiten.
- Führe die vollständige automatisierte Standardsuite aus.
- Stelle Testberichte und Coverage-Berichte als CI-Artefakte bereit.
- Prüfe auf versehentlich eingecheckte Geheimnisse.
- Verwende keine produktiven Broker-Zugangsdaten.
- Aktiviere durch CI niemals Live-Trading.
- Prüfe Änderungen an den Workflows selbst ebenfalls.

Wenn die Remote-CI fehlschlägt, untersuche die Ursache, behebe sie und pushe einen zusätzlichen geprüften Korrekturcommit. Melde die Änderung erst als abgeschlossen, wenn die erforderlichen Checks bestanden sind oder eine konkrete externe Blockade ausdrücklich dokumentiert wurde.

### Geheimnisse und lokale Daten ausschließen

Halte mindestens folgende Inhalte aus Git heraus:

- .env und Dateien mit echten Zugangsdaten,
- private Schlüssel,
- lokale Kontodaten,
- Datenbankdateien und Datenbank-Dumps,
- sensible Logs,
- große heruntergeladene Marktdatensätze,
- lokale Modellartefakte, sofern kein bewusst eingerichtetes Artefaktmanagement verwendet wird.

Committe ausschließlich eine .env.example mit Platzhaltern.

Prüfe auch bereits getrackte Dateien: Ein Eintrag in .gitignore entfernt sie nicht nachträglich aus Git.

### Abschlussbericht

Nenne nach jeder abgeschlossenen Arbeitseinheit knapp:

- was geändert wurde,
- welche Prüfungen bestanden sind,
- welche Prüfungen gegebenenfalls blockiert waren,
- Branch und Commit-ID,
- Push- und CI-Status,
- verbleibende Einschränkungen.

Halte CHANGELOG, Code, Tests und Dokumentation im selben Commit konsistent.

## 18. Verbindliche Voraussetzungen vor dem Livebetrieb

Implementiere die folgenden Anforderungen vor jeder Freigabe für echte Orders. Fehlende Voraussetzungen müssen den Livebetrieb technisch sperren.

### 18.1 Getrennte Freigabe von Code und Livebetrieb

Ein erfolgreicher Test, Commit oder GitHub-Push ist keine Livefreigabe.

- Trenne Entwicklung, Paper Trading und Livebetrieb.
- Verwende getrennte Zugangsdaten und persistente Zustände.
- Keine automatischen Produktionsdeployments nach einem Push.
- Starte Livebetrieb ausschließlich mit einer ausdrücklich freigegebenen, unveränderlichen Releaseversion.
- Binde die Freigabe an Codeversion, Modellversion, Risikokonfiguration, Instrumentenuniversum und konkretes Brokerkonto.
- Änderungen an diesen Bestandteilen erfordern eine erneute Freigabe.
- Der Forschungsprozess besitzt keinen Zugriff auf Live-Zugangsdaten.
- Ein Rollback darf persistente Order- und Positionszustände nicht verlieren.

### 18.2 Alpaca-Preflight

Prüfe vor jedem Start und vor der ersten Order:

- erwartete Konto-ID und korrekten Paper-/Live-Endpunkt,
- Kontostatus und vorhandene Handelssperren,
- tatsächlich erlaubte Instrumente und Ordertypen,
- verfügbares Kapital, offene Orders und reservierte Beträge,
- kontospezifische Handels- und Settlement-Beschränkungen,
- Datenabonnement, Feed, Zeitstempel und Datenfrische,
- Brokerpositionen gegen interne Positionen.

Leite zulässiges Kapital nicht allein aus der vom Broker gemeldeten Buying Power ab: Diese kann Margin enthalten. Ungehebelter Betrieb muss unabhängig davon durchgesetzt werden.

Prüfe aktuelle Brokerbedingungen anhand offizieller Dokumentation. Hardcode keine möglicherweise veralteten regulatorischen Schwellen.

Bei Abweichungen oder unklaren Zuständen keine neuen Orders zulassen.

### 18.3 Konsistente Marktdaten

Lege den verwendeten Feed ausdrücklich fest.

- Kein stiller Wechsel zwischen IEX, SIP und verzögerten Daten.
- Backtest, Paper Trading und Livebetrieb müssen hinsichtlich Datenquelle und Feature-Berechnung vergleichbar sein.
- Abweichungen müssen dokumentiert und separat validiert werden.
- Verzögerte Daten dürfen nicht als aktuelle Kurse behandelt werden.
- Prüfe vor einer Order aktuelle Quotes, Spread und Quote-Alter.
- Synchronisiere die Systemzeit und überwache Zeitabweichungen.
- Verhindere doppelte Signale bei mehrfach gelieferten Kerzen.
- Definiere den Umgang mit nachträglichen Kerzenkorrekturen.

### 18.4 Schutz offener Positionen

Der Schutz einer Position darf nicht ausschließlich davon abhängen, dass der lokale Bot läuft.

- Verwende, soweit für Instrument und Ordertyp geeignet und unterstützt, brokerseitige Schutzorders.
- Prüfe deren tatsächliche Annahme, Aktivierung, Menge und Status.
- Berücksichtige Teilfüllungen und noch nicht aktive Schutzorders.
- Definiere eine maximale tolerierte Zeit ohne vorgesehenen Schutz.
- Bei fehlgeschlagenem Schutz keine weiteren Positionen eröffnen; eine vorab festgelegte Notfallbehandlung auslösen.

Teste Orderersetzungen und konkurrierende Ausführungen. Verhindere, dass doppelte Verkaufsorders unbeabsichtigte Shortpositionen erzeugen.

Dokumentiere, wann Schutzorders gelten, etwa hinsichtlich Handelszeiten. Stoporders garantieren weder einen Ausführungspreis noch eine maximale Verlusthöhe. Stop-Limit-Orders können unausgeführt bleiben.

### 18.5 Notfallbedienung und unabhängige Überwachung

Implementiere getrennte Aktionen für:

1. Neue Einstiege sperren, bestehende Positionen weiter verwalten.
2. Offene Einstiegsorders stornieren, Schutzorders erhalten.
3. Kontrollierten Positionsabbau einleiten.

Ein Stoppen des Prozesses ist nicht gleichbedeutend mit dem Schließen von Positionen.

Vor und nach Notfallaktionen Brokerzustand abgleichen. Bei nicht erreichbarem Broker darf keine erfolgreiche Glattstellung behauptet werden.

Richte einen unabhängigen Heartbeat-Monitor ein, der auch erkennt, wenn der Bot selbst keine Meldungen mehr senden kann.

Alarme mindestens für:

- Prozessausfall,
- veraltete Marktdaten,
- Verbindungsverlust,
- ungeschützte Positionen,
- unbekannte Orderzustände,
- unerwartete Positionen,
- Risikolimitverletzungen.

Teste die Alarmzustellung. Dokumentiere die manuelle Bedienung über das Broker-Dashboard bei vollständigem Ausfall des Bots.

### 18.6 Ausfallsicherheit und Wiederherstellung

- Nur eine aktive Instanz darf Orders für das Konto erzeugen.
- Verhindere parallele Orderausführung nach Neustarts oder Deployments durch eine belastbare Sperre mit eindeutigem Instanzbesitz.
- Persistiere Orderabsichten und Zustandsänderungen nachvollziehbar.
- Behandle doppelte, verspätete und in falscher Reihenfolge eintreffende Brokerereignisse.
- Begrenze Wiederholungen und beachte API-Ratenlimits.
- Sperre neue Orders bei kritischen Persistenzfehlern.

Erstelle Backups und teste die Wiederherstellung tatsächlich.

Nach Wiederherstellung bleibt der Handel gesperrt, bis Positionen, Orders und Ausführungen vollständig mit dem Broker abgeglichen sind. Alte Orderabsichten dürfen nicht ungeprüft erneut ausgeführt werden.

### 18.7 Verbindliche Betriebsregeln

Definiere ausdrücklich:

- erlaubte Handelszeiten,
- Verhalten vor Handelsschluss,
- Übernacht- und Wochenendpositionen,
- Umgang mit Handelsunterbrechungen,
- Verhalten bei Splits, Dividenden und Delistings,
- Umgang mit manuellen Orders und anderen Strategien im selben Konto.

Für die erste Livephase möglichst ein ausschließlich diesem Bot zugeordnetes Konto verwenden. Fremde Positionen niemals automatisch liquidieren.

### 18.8 Finanzielle Buchhaltung

Verwende Dezimalarithmetik oder geeignete ganzzahlige Einheiten für Geldbeträge und Ordermengen. Beachte Tickgrößen und Mengenpräzision.

Erfasse:

- sämtliche Ausführungen und Gebühren,
- Dividenden und sonstige Kontobewegungen,
- Ein- und Auszahlungen,
- Währungsumrechnungen,
- realisierte und unrealisierte Ergebnisse.

Trenne Strategieergebnis und Währungseffekt. Definiere die Basiswährung für Reporting und Risikolimits.

Gleiche die Buchhaltung regelmäßig mit Brokerabrechnungen ab. Stelle vollständige Exporte für die spätere steuerliche Aufbereitung bereit; behaupte keine steuerliche Vollständigkeit ohne Prüfung.

### 18.9 Nachweisbare Livefreigabe

Erstelle vor dem ersten Liveeinsatz einen Freigabebericht mit:

- konkreter Release- und Modellversion,
- dokumentierter Datenbasis und ihren Grenzen,
- Walk-forward- und unabhängigen Testergebnissen,
- Kosten- und Sensitivitätsanalysen,
- Paper-/Shadow-Ergebnissen,
- Ergebnissen der Ausfall- und Wiederherstellungstests,
- Nachweis funktionierender Alarme und Notfallbedienung,
- offenen Fehlern und Einschränkungen,
- freigegebenem Kapital und maximalem Verlustbudget.

Definiere wirtschaftliche Bewertungskriterien vor der Auswertung. Technisch bestandene Tests beweisen keine profitable Strategie.

Eine feste Anzahl von Paper-Tagen oder Trades genügt allein nicht: Bewerte Beobachtungsdauer, Abhängigkeit der Trades, Marktphasen und statistische Unsicherheit.

Keine Freigabe bei ungeklärten kritischen Fehlern.

### 18.10 Begrenzter Livepilot

Beginne nach ausdrücklicher Freigabe mit einem begrenzten Livepilot:

- separat festgelegtes kleines Kapitalbudget,
- wenige liquide Instrumente,
- kein Hebel,
- feste maximale Orderwerte,
- zusätzlich absolute Verlustgrenzen in Kontowährung,
- engmaschige Überwachung.

Vergleiche reale Ausführungen mit den vorab protokollierten Erwartungen: Spread, Slippage, Ausführungsdauer, Gebühren und Ablehnungsquote.

Lege vor Beginn Kriterien für Fortsetzung, Stopp und Ausweitung fest. Bei wesentlichen Abweichungen Handel pausieren und Ursache untersuchen.

Keine automatische Kapitalerhöhung und keine automatische Ausweitung von Instrumenten oder Risikolimits. Jede Skalierung benötigt eine ausdrückliche Freigabe.

## 19. Zusammenarbeit manuell gestarteter, unterschiedlicher KI-Agenten

### 19.1 Arbeitsmodell

Der Benutzer startet und wechselt die Agenten manuell, beispielsweise abhängig von verfügbaren Nutzungskontingenten.

Implementiere keinen automatischen Agenten-Orchestrator.

Alle Projektinformationen müssen im Repository oder in zugehörigen GitHub Issues und Pull Requests verfügbar sein. Chatverläufe und das Gedächtnis einzelner Agenten sind keine verbindliche Projektquelle.

Arbeite so, dass ein anderer Agent deine Aufgabe ohne Zugriff auf deinen bisherigen Chat fortsetzen kann.

Die Rollen sind unabhängig vom Modellanbieter:

- Implementierung: Code, Tests und technische Dokumentation.
- Forschung: Hypothesen und reproduzierbare Experimente.
- Review: unabhängige Prüfung von Änderungen und Ergebnissen.
- Integration: Zusammenführung kompatibler, geprüfter Änderungen.

Ein Agent darf mehrere Rollen nacheinander übernehmen. Kritische Änderungen an Orderausführung, Risikosteuerung oder Validierung benötigen vor der Integration eine unabhängige Prüfung durch einen anderen Agenten oder den Benutzer.

### 19.2 Gemeinsame Dokumentation

Erstelle und pflege:

- AGENTS.md: Kurze verbindliche Arbeitsregeln und Verweise auf Detaildokumente.
- docs/PROJECT_SPEC.md: Projektziel, Anforderungen und Abnahmekriterien.
- docs/ARCHITECTURE.md: Komponenten, Verantwortlichkeiten und Schnittstellen.
- docs/STATUS.md: Tatsächlich integrierte Funktionen, Einschränkungen und nächste Ziele.
- docs/handoffs/<task-id>.md: Übergabe und Arbeitsstand je Aufgabe.
- docs/research/<experiment-id>.md: Forschungsplan und Ergebnisse je Experiment.
- docs/decisions/: Wesentliche technische und methodische Entscheidungen.
- changelog.d/<task-id>.md: Änderungsnotiz je Aufgabe.

Falls Werkzeuge zusätzliche Anweisungsdateien verwenden, verweise darin auf AGENTS.md. Vermeide mehrfach gepflegte, widersprüchliche Regelwerke.

AGENTS.md und PROJECT_SPEC bilden zusammen mit ausdrücklichen Benutzerentscheidungen die verbindliche Grundlage. Bei Widersprüchen nicht stillschweigend eine eigene Interpretation implementieren: Konflikt dokumentieren und die betroffene Entscheidung klären.

Kennzeichne Dokumentationsaussagen eindeutig als:

- geplant,
- implementiert,
- getestet,
- integriert,
- blockiert.

Beschreibe geplante Funktionen niemals als bereits vorhanden.

### 19.3 Einstieg jedes Agenten

Vor Änderungen:

1. Lies AGENTS.md.
2. Lies PROJECT_SPEC, ARCHITECTURE und STATUS.
3. Prüfe den tatsächlichen Code, Git-Status und aktuelle Remote-Branches.
4. Prüfe offene Aufgaben und laufende Pull Requests.
5. Wähle eine freie, ausreichend abgegrenzte Aufgabe.
6. Dokumentiere die Übernahme.
7. Erstelle einen eigenen Branch und Git-Worktree.
8. Lies zusätzlich die aufgabenspezifische Übergabe und Entscheidungen.

Vertraue bei technischen Aussagen dem überprüften Repositoryzustand. Veraltete Dokumentation muss korrigiert werden.

### 19.4 Aufgabenverwaltung und Übernahme

Nutze GitHub Issues als gemeinsame Aufgabenverwaltung.

Jede Aufgabe enthält:

- eindeutige Task-ID,
- Problem und gewünschtes Ergebnis,
- überprüfbare Abnahmekriterien,
- betroffene Komponenten beziehungsweise Dateien,
- Abhängigkeiten,
- geplante Tests,
- zuständigen Agenten beziehungsweise Sitzung,
- Arbeitsbranch und Pull Request,
- aktuellen Stand.

Verwende Zustände wie: backlog → claimed → in_progress → review → done. Zusätzlich: blocked.

Prüfe vor und unmittelbar nach dem Beanspruchen einer Aufgabe, ob ein anderer Agent dieselbe Aufgabe übernommen hat.

GitHub-Labels oder Kommentare sind keine atomare Sperre. Wenn zwei Agenten dieselbe Aufgabe beanspruchen, darf nur einer fortfahren: Die zuerst serverseitig bestätigte Übernahme gewinnt; der andere wählt eine andere Aufgabe.

Nicht allein aufgrund vergangener Zeit eine Aufgabe übernehmen. Bei unklarer aktiver Bearbeitung Benutzerentscheidung einholen.

Bei Aufgabenwechsel Übernahme, Branch und Übergabe aktualisieren.

### 19.5 Isolation paralleler Arbeit

Jeder gleichzeitig arbeitende Agent benötigt:

- eigenen Git-Worktree,
- eigenen Branch,
- eigene temporäre Dateien,
- getrennte Testdatenbanken und Laufzeitverzeichnisse,
- bei Bedarf eigene Ports und Docker-Compose-Projektnamen.

Branches beispielsweise: agent/<task-id>-<kurzbeschreibung>

Arbeite niemals mit mehreren Agenten im selben beschreibbaren Checkout.

Verwende keine gemeinsam beschreibbaren Modellregistrierungen, Paper-Konten oder Brokerzustände für parallele Tests. Es darf weiterhin nur eine Instanz echte Orders für ein Konto erzeugen.

Zugangsdaten bleiben außerhalb von Git. Kopiere sie nicht in Übergabedokumente oder Issue-Kommentare.

### 19.6 Aufgaben so schneiden, dass Parallelität möglich ist

Bevorzuge unabhängige Aufgaben, beispielsweise:

- Datenqualitätsprüfung,
- Strategieimplementierung gegen bestehende Schnittstellen,
- Reporting,
- Tests eines festgelegten Brokervertrags,
- unabhängige Forschungsexperimente.

Gemeinsame Schnittstellen müssen zuerst festgelegt werden.

Änderungen an zentralen Datentypen, Datenbankschemas, Konfiguration, Abhängigkeiten oder öffentlichen Schnittstellen benötigen eine dokumentierte Abstimmung mit betroffenen Aufgaben.

Kein beiläufiges Refactoring fremder Komponenten.

Wenn mehrere Aufgaben dieselben zentralen Dateien verändern würden, teile sie anders auf oder führe diese Änderungen nacheinander aus.

### 19.7 Übergabe bei Pause, Modellwechsel oder Tokenlimit

Pflege docs/handoffs/<task-id>.md während der Arbeit und spätestens vor jeder Pause.

Inhalt:

- Task-ID und Ziel,
- Branch und letzter Commit,
- Ausgangsrevision,
- erledigte Arbeit,
- verbleibende Schritte,
- relevante Dateien,
- getroffene Entscheidungen,
- ausgeführte Tests mit Ergebnissen,
- noch nicht ausgeführte Prüfungen,
- bekannte Fehler und Blockaden,
- genaue Befehle zur Fortsetzung,
- eventuell vorhandene uncommittete Änderungen.

Erstelle bei längeren Aufgaben regelmäßig kleine geprüfte Commits und pushe sie entsprechend den Git-Regeln.

Unfertige Zwischenstände dürfen als klar gekennzeichnete WIP-Commits auf dem eigenen Arbeitsbranch gesichert werden. Dies ist eine ausdrückliche Ausnahme von der Regel, nur vollständig erfolgreiche Arbeitseinheiten zu committen.

WIP-Commits dürfen nicht als fertig gemeldet, integriert oder bereitgestellt werden. Fehlgeschlagene und fehlende Prüfungen sind offen zu dokumentieren. Geheimnisprüfungen bleiben verpflichtend.

### 19.8 Integration und Reviews

Pushe Änderungen auf den eigenen Arbeitsbranch und erstelle einen Pull Request mit:

- Task-ID,
- Problem und geändertem Verhalten,
- relevanten Entscheidungen,
- Tests und Ergebnissen,
- Einschränkungen,
- Datenbank- oder Konfigurationsänderungen.

Kein automatisches Merge nach main. *(Präzisiert durch §21.)*

Vor Integration:

1. Aktuellen Stand von main einbeziehen.
2. Konflikte fachlich lösen.
3. Gesamte verbindliche Testsuite erneut ausführen.
4. CI und erforderliches Review abwarten.
5. Kompatibilität mit bereits integrierten Aufgaben prüfen.

Konflikte nicht pauschal mit „ours“ oder „theirs“ lösen.

STATUS und zusammengeführtes CHANGELOG werden beim Integrationsschritt aktualisiert. Parallele Agenten liefern dafür ihre jeweiligen Änderungsnotizen unter changelog.d/.

### 19.9 Eigenständige Ideen und Forschung

Agenten dürfen selbstständig neue Ideen vorschlagen und begrenzte Experimente innerhalb des freigegebenen Forschungsrahmens durchführen.

Vor jedem Experiment dokumentieren:

- wirtschaftliche oder technische Hypothese,
- warum ein Vorteil plausibel sein könnte,
- Unterschiede zur bestehenden Methode,
- verwendete Daten und zeitliche Verfügbarkeit,
- Vergleichsmodell,
- Parameterraum,
- Bewertungs- und Abbruchkriterien,
- Rechen-, Zeit- und Kostenbudget.

Fehlt ein Budget, sind lokale Experimente mit vorhandenen Ressourcen zulässig. Kostenpflichtige Dienste oder unbegrenzte Optimierungsläufe dürfen daraus nicht abgeleitet werden.

Negative Ergebnisse und verworfene Varianten bleiben dokumentiert.

Eine bessere Argumentation oder ein überzeugender Bericht ersetzt keine reproduzierbaren Ergebnisse.

### 19.10 Gemeinsames Experimentregister

Alle Agenten müssen ein gemeinsames, versionierbares Experimentregister verwenden. Speichere je Experiment eine separate unveränderliche Ergebnisdatei, um parallele Schreibkonflikte zu vermeiden.

Erfasse mindestens:

- Experiment-ID und zugehörige Task-ID,
- verantwortlichen Agenten und verwendetes Modell,
- Code- und Datenversion,
- Parameter und Zufallsseeds,
- Datenaufteilungen,
- Kostenannahmen,
- sämtliche getesteten Varianten,
- Ergebnisse und Unsicherheit,
- Entscheidung und Begründung.

Berücksichtige die Gesamtzahl der Versuche aller Agenten. Parallelität darf das Risiko von Overfitting nicht unsichtbar machen.

Der abschließende unabhängige Testdatensatz wird zentral kontrolliert. Forschungsagenten dürfen ihn weder eigenständig auswerten noch seine Ergebnisse zur Kandidatenauswahl verwenden.

Wiederholte Sichtung desselben Testdatensatzes muss protokolliert und bei der Bewertung seiner Unabhängigkeit berücksichtigt werden.

### 19.11 Zusammenarbeit mit Reasoning-Modellen

Ein Reasoning-Modell kann ohne direkten Codezugriff beitragen.

Es erhält ein aufgabenspezifisches Paket mit:

- Forschungsfrage,
- relevanten Anforderungen und Schnittstellen,
- Datenbeschreibung,
- bisherigen Ergebnissen,
- bekannten Einschränkungen,
- erlaubtem Experimentbudget.

Sein Ergebnis muss enthalten:

1. konkrete Hypothese,
2. Annahmen und Gegenargumente,
3. überprüfbare Vorhersagen,
4. reproduzierbaren Versuchsplan,
5. Abnahmekriterien,
6. mögliche Fehlerquellen.

Ein Programmieragent prüft diesen Auftrag und implementiert das Experiment. Ein unabhängiger Reviewer prüft Methode und Ergebnisse.

Modellübereinstimmung gilt nicht als empirischer Nachweis. Mehrere Agenten können dieselben falschen Annahmen teilen.

### 19.12 Automatische Modellverbesserung

Automatisiere Training, Auswertung und den Vergleich von Kandidaten innerhalb der bestehenden Forschungs- und Risikogrenzen.

Erfolgreiche Kandidaten dürfen nach den vorab festgelegten Kriterien in Paper-/Shadow-Betrieb wechseln.

Live-Aktivierung, Kapitalerhöhung und Änderungen der Risikogrenzen bleiben an die bestehende ausdrückliche Freigabe gebunden.

Forschungscode darf den laufenden Produktionsdienst nicht verändern. Experimente müssen reproduzierbar isoliert ausgeführt werden.

## 20. Modellwahl, Denkstufe und Bewertung der Agenten

(Benutzerentscheidung vom 2026-11-17)

- Prüfe vor jeder Aufgabe, ob das gewählte Modell und die Denkstufe angemessen sind. Falls ein Wechsel sinnvoll ist, nenne das empfohlene Modell, die Denkstufe und den konkreten Grund.
- Wenn du die Einstellung nicht technisch ändern kannst, behaupte keinen Wechsel. Erstelle eine kurze Übergabe, damit der Benutzer manuell wechseln kann.
- Nutze Medium als Ausgangspunkt für klar abgegrenzte Implementierungen.
- Erhöhe die Denkstufe bei komplexen Abhängigkeiten, unklaren Anforderungen oder Änderungen mit hohen Fehlerfolgen.
- Nach zwei erfolglosen Lösungsversuchen: Keine weiteren spekulativen Änderungen. Erstelle eine Übergabe mit Fehlerreproduktion, Befunden und verworfenen Ansätzen und empfehle eine unabhängige Analyse durch ein stärkeres Modell.
- Änderungen an Risikosteuerung, Orderausführung und statistischer Validierung benötigen unabhängig vom implementierenden Modell ein separates Review.
- Beurteile Modelle anhand bestandener Abnahmekriterien, gefundener Fehler und Gesamtaufwand einschließlich Nacharbeit.

## 21. Integration durch Agenten

(Benutzerentscheidung vom 2026-11-17)

- Erster Schritt jeder Sitzung ist das Review offener Pull Requests anderer Agenten (Label `needs-independent-review` oder `review`). Eigene PRs werden nicht reviewt.
- Offene Review-Befunde an eigenen PRs werden vorrangig auf dem bestehenden Branch behoben; das ist kein Selbstreview.
- Eine Freigabe gilt nur für den geprüften Head-SHA und Zielbranch; Unabhängigkeit richtet sich nach der dokumentierten tatsächlichen Bearbeitung, nicht nach dem GitHub-Account.
- Ein Agent darf einen PR als Integrationsagent nach main mergen, wenn: der PR von einer anderen Agentenart/Modellfamilie oder vom Benutzer stammt, ein unabhängiges Review mit Freigabe vorliegt, der aktuelle Stand von main einbezogen ist, `make check` und CI grün sind und keine offenen Blockaden, WIP-Commits oder Benutzerentscheidungen bestehen. Merge per Merge-Commit, kein Force-Push.
- Eigene PRs werden nie selbst gemergt.
- Integration in main ist keine Livefreigabe; §18 bleibt unverändert.
- Die Einzelheiten stehen in AGENTS.md §0 und §2a.
