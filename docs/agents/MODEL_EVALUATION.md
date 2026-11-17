# Bewertung der Agenten/Modelle

Bewertet werden **bestandene Abnahmekriterien, gefundene Fehler und Gesamtaufwand einschließlich Nacharbeit**
(AGENTS.md §3). Ein Eintrag je abgeschlossener Aufgabe; Fehler, die später (Review, CI, andere Aufgaben)
gefunden werden, werden beim verursachenden Eintrag nachgetragen.

| Task | Agent / Modell / Denkstufe | Abnahmekriterien erfüllt | Fehler gefunden (durch wen, wann) | Nacharbeit | Aufwand (Sitzungen/Commits) | Bemerkung |
|---|---|---|---|---|---|---|
| T-001 | Claude Code / claude-opus-5-5 (konfiguriert) / unbekannt | ja | Secret-Scan meldete eigene Testdaten in CI (selbst behoben, 1 Folgecommit) | 1 Commit | 1 Sitzung / 3 Commits | Serving-Modell kann abweichen |
| T-002 | Claude Code / claude-opus-5-5 / unbekannt | ja, Review ausstehend | doppelte Kostenbuchung im übernommenen Code gefunden und behoben | – | 1 Commit | kritisch: separates Review nötig |
| T-003 | Claude Code / claude-opus-5-5 / unbekannt | ja | NaN/±Inf im CSV-Import akzeptiert (Codex-Review #17, 2026-11-17, P2) | 1 Commit (`ee24cdd`) | 1 Commit | |
| T-004 | Claude Code / claude-opus-5-5 / unbekannt | nein, Review-Befunde | 1-µs-Ausführungsverzögerung (eigene Tests); Codex-Review #17 (2026-11-17): [P1] verspätete Bars in früheren Kontexten sichtbar (Look-ahead), [P1] ausstehender Einstieg auf Bar nach Datenlücke; Codex-Review 2 am `be6bf16`: [P1] Tief der laufenden Bar beeinflusst über den Stop frühere Entscheidungen (Look-ahead) | 2 Korrekturrunden (`be6bf16`, `1dfb421`) | 1 + 2 Commits | kritisch: separates Review nötig; zweiter Look-ahead entstand durch die erste Korrektur |
| T-009 | Claude Code / claude-opus-5-5 / unbekannt | nach Korrektur ja (Codex-Review 2) | Codex-Review #18 (2026-11-17, P2): kollidierende Experiment-IDs zwischen Worktrees | 1 Commit (`e0852a6`) + Regelpräzisierung | 2 + 2 Commits | |
| T-018 | Claude Code / claude-opus-5-5 / niedrig, Korrektur medium | nein, Review-Befunde | Codex-Review #21 am `9c47014`: [P2] Holdout-Preise im Forschungsmanifest, [P2] bereinigte Kurse als Ausführungspreise, [P2] Halbtage nicht erkannt, [P2] Holdout-Grenze innerhalb einer Bar; Review 2 am `535493e`: [P2] Dividenden nicht an Datenversion gebunden, [P2] Dividende am Ex-Tag als verfügbares Cash; selbst gefunden: zwei fehlende SPY-Dividenden beim Anbieter | 2 Korrekturrunden, Neuberechnungen EXP-T018-002/-003 | 2 + 4 Commits | Holdout-Trennung ist Validierungslogik; Einordnung im PR zunächst falsch |
| T-007 (Report-Teil) | Claude Code / claude-opus-5-5 / medium | nein, Review-Befund | Codex-Review #24 am `83fae16`: [P2] falsch typisierte Reportdatei legt die ganze Übersicht lahm | 1 Commit | 1 + 1 Commits | |
| T-008 (Bausteine) | Claude Code / claude-opus-5-5 / medium | nein, Review-Befunde | Codex-Review #23 am `2c72d7f`: [P1] Startsperre prüfte Freeze-Beleg nur auf Vorhandensein des Pfads, [P2] abgeschnittenes Journalende unerkannt (Neustart möglich), [P2] Registry-Zeiger vor Audit veröffentlicht | 1 Commit | 1 + 1 Commits | P1 in Validierungsinfrastruktur bei medium |
