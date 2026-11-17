# Bewertung der Agenten/Modelle

Bewertet werden **bestandene Abnahmekriterien, gefundene Fehler und Gesamtaufwand einschließlich Nacharbeit**
(AGENTS.md §3). Ein Eintrag je abgeschlossener Aufgabe; Fehler, die später (Review, CI, andere Aufgaben)
gefunden werden, werden beim verursachenden Eintrag nachgetragen.

| Task | Agent / Modell / Denkstufe | Abnahmekriterien erfüllt | Fehler gefunden (durch wen, wann) | Nacharbeit | Aufwand (Sitzungen/Commits) | Bemerkung |
|---|---|---|---|---|---|---|
| T-001 | Claude Code / claude-opus-5-5 (konfiguriert) / unbekannt | ja | Secret-Scan meldete eigene Testdaten in CI (selbst behoben, 1 Folgecommit) | 1 Commit | 1 Sitzung / 3 Commits | Serving-Modell kann abweichen |
| T-002 | Claude Code / claude-opus-5-5 / unbekannt | ja, Review ausstehend | doppelte Kostenbuchung im übernommenen Code gefunden und behoben | – | 1 Commit | kritisch: separates Review nötig |
| T-003 | Claude Code / claude-opus-5-5 / unbekannt | ja | – | – | 1 Commit | |
| T-004 | Claude Code / claude-opus-5-5 / unbekannt | ja, Review ausstehend | 1-µs-Ausführungsverzögerung im eigenen Code durch Tests gefunden | – | 1 Commit | kritisch: separates Review nötig |
| T-009 | Claude Code / claude-opus-5-5 / unbekannt | siehe Issue | – | – | – | |
