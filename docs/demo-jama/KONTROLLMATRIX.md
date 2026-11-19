# Kontrollmatrix für das Entwicklungsdemo

Stand 19.11.2026. Vorprüfung für den Scope lokales Register, MCP, Historienimport und
Entwicklungsnachweise. Bas bestätigt Scope, Anwendbarkeit und Restrisiken. Die ISO-Volltexte
müssen für einen klauselscharfen Abgleich autorisiert vorliegen.

| Quelle und Gegenstand | Verantwortung | Nachweis im Demo | Prüfstatus und Restlücke |
|---|---|---|---|
| ISO/IEC/IEEE [29148:2018](https://www.iso.org/standard/72089.html), Requirements Engineering | Bas, Requirements-Rolle | Stabile IDs, Quelle, Revision, Risiko, Akzeptanzkriterien, Baseline und Trace | Lokale Funktionen geprüft. Fachliche Qualität aller Kandidaten und genaue Klauselzuordnung offen. |
| ISO/IEC/IEEE [29119-2:2021](https://www.iso.org/standard/79428.html) und [29119-3:2021](https://www.iso.org/standard/79429.html), Testprozesse und Dokumentation | QA, CI | Testfallversionen mit Soll, Testlaufergebnis, Ist, Prüfumgebung und Evidenzreferenz | Synthetischer Ablauf geprüft. Vollständige Testplanung und Migration historischer Evidenz offen. |
| ISO/IEC/IEEE [12207:2026](https://www.iso.org/standard/90219.html), Softwarelebenszyklus | Projektleitung | Quellennachweis, Baseline, Implementierung, Prüfung und Entscheidung | Ausschnitt umgesetzt. Betrieb, Wartung, Stilllegung und klauselscharfer Abgleich offen. |
| ISO/IEC [27001:2022](https://www.iso.org/standard/27001) und [27002:2022](https://www.iso.org/standard/75652.html), ISMS und sichere Entwicklung | Bas, Informationssicherheit | Rollenmodell, getrennte Prozesse, Versionshistorie, Freigabebindung | Technischer Demo-Nachweis. ISMS-Scope, Risikobehandlung, SoA, externe Erfassung und Volltextabgleich offen. |
| BSI IT-Grundschutz Edition 2023 APP.6 und APP.7, allgemeine und individuell entwickelte Software | Fachverantwortung | Anforderungsbestand, Quellen, getrennte Entwürfe, Review | Historische Quellreferenzen erfasst; fachliche Bestätigung, Baseline und Schutzbedarf offen. |
| BSI CON.8.A7, entwicklungsbegleitende Tests | Entwicklungs- und QA-Rolle | Testdefinitionen, synthetischer Test mit verweigerter Entscheidung und Prozesstest | Demo-Nachweise vorhanden. Vollständiger bausteinweiser Abgleich offen. |
| BSI OPS.1.1.6.A1–A5 und A7, Testplanung, Auswertung, Abnahme und unabhängige Prüfung | QA und Bas | Testfälle, Soll/Ist, Review und getrennte Entscheidung | GitHub-PR, CI-Artefakt und formales Review werden gegen eine Betreiber-Policy geprüft. Getrennte Konten, geschützte CI und Anwendbarkeit pro Anforderung im Betrieb sind nachzuweisen. |
| BSI OPS.1.1.3, Änderungsmanagement | Projektleitung | Unveränderliche Baselines, Versionierung und reproduzierbarer Export | Vollständige Bewertung von Änderungsbetrieb und Patchprozess offen. |
| BSI [Grundschutz++](https://github.com/BSI-Bund/Stand-der-Technik-Bibliothek), DEV/TEST und weitere relevante Controls | Informationssicherheit | Verknüpfung mit bisherigem Katalog vorbereitet | Zielstand, offizielle Zuordnung und modellierter Anforderungssatz offen. |

[BSI IT-Grundschutz-Kompendium Edition 2023](https://www.bsi.bund.de/SharedDocs/Downloads/DE/BSI/Grundschutz/IT-GS-Kompendium/IT_Grundschutz_Kompendium_Edition2023.pdf?__blob=publicationFile&v=4)

Technischer Abnahmenachweis: [Verfahrensbeschreibung](README.md) und [Negativtests für GitHub-Evidenz](../../tests/test_demo_jama_github.py). Die Tests nutzen synthetische API-Antworten und begründen keine Zertifizierung.

## OPS.1.1.6 im Entwicklungs- und Freigabeprozess

Quellenstand Edition 2023. Anwendbarkeit und Status sind in dieser Tabelle vorläufig, bis Bas
den Schutzbedarf und die organisatorische Umsetzung bestätigt.

| ID | Anwendbarkeit im Modell | Verantwortlich | Nachweis und Restlücke |
|---|---|---|---|
| A1 | Ja, Planung repräsentativer Tests | QA, Bas | Versionierte Testfälle sind vorhanden. Ein vollständiger Abnahmeplan und die Plattformmatrix sind offen. |
| A2 | Ja, funktionale Prüfung | QA | Import, Rollenpfad, Webansicht und MCP haben lokale Tests. Eine fachlich bestätigte vollständige Abdeckung fehlt. |
| A3 | Ja, Soll/Ist und dokumentierte Auswertung | QA | Versionierte Erwartung und verifizierter CI-Pass sind verknüpft. Historische manuelle Nachweise bleiben offen. |
| A4 | Ja, formale Produktivfreigabe | Bas | Der Integrationsnachweis bindet PR und GitHub-Review. Produktivfreigabe und fachliche Abnahme werden separat erteilt. |
| A5 | Ja, wesentliche nichtfunktionale und Sicherheitsanforderungen | QA, Informationssicherheit | Negativtests decken Artefaktintegrität und Freigabebindung ab. Vollständige Sicherheits- und Lastabnahme offen. |
| A6 | Ja, Anwendungs- und Aufgabenbriefing | Fachverantwortung | Laufanleitung vorhanden. Nachweis der tatsächlichen Einweisung offen. |
| A7 | Ja, Auswahl und unabhängige Prüfung | Bas, QA, Review-Rolle | Getrennte Review-Identitäten vorgesehen. Eignung, Zuordnung und tatsächliche Trennung sind zu bestätigen. |
| A8, A9 | Entfallen im Katalog | Informationssicherheit | Keine technische Umsetzung erforderlich. |
| A10 | Ja, Abnahmeplan und Vorgehen bei Ablehnung | QA, Bas | Freigabegates und Fehlerpfade vorhanden. Vollständigen Abnahmeplan und Eskalation bestätigen. |
| A11 | Bedingt, wenn schützenswerte Produktivdaten für Tests eingesetzt werden | Datenschutz, QA | Demo-Tests verwenden synthetische Eingaben. Entscheidung für weitere Umgebungen offen. |
| A12 | Ja, Regressionen nach Änderungen | QA | Reproduzierbare positive und negative Testfälle existieren. Projektweite Dokumentation verbleibender Fälle offen. |
| A13 | Ja, getrennte Testumgebung | Betreiber | Lokaler Smoke-Test nutzt isolierte Daten. Produktive Rollen- und Betriebsgrenzen nachweisen. |
| A14 | Abhängig vom festgestellten Schutzbedarf | Informationssicherheit | Entscheidung im Rahmen der Risikoanalyse offen. |
| A15 | Ja, Installation und Dokumentation | Betreiber, QA | Einstieg und SDK-Test sind dokumentiert. Vollständige Prüfung der vorgesehenen Betriebsumgebung offen. |

[BSI OPS.1.1.6](https://www.bsi.bund.de/SharedDocs/Downloads/DE/BSI/Grundschutz/IT-GS-Kompendium_Einzel_PDFs_2023/04_OPS_Betrieb/OPS_1_1_6_Software_Tests_und_Freigaben_Edition_2023.pdf?__blob=publicationFile&v=3)
