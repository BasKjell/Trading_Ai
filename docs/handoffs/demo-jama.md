# Übergabe Demo-Jama (PR #53)

- Codex · Klasse K · Basis PR #52. Unabhängiges erneutes Review durch Claude oder Bas nötig.
- Die vollständige Liste der geänderten PR-Dateien wird vor Prüfung des CI-Artefakts ausgewertet. Geprüft werden auch neue Dateien und die alten Pfade von Umbenennungen.
- Tests decken hinzugefügte CI-Konfiguration, Änderungen im QA-Verzeichnis, Umbenennungen, Pagination und die neueste Entscheidung von Bas ab.
- Die Kontrollmatrix und die Betreiberanleitung dokumentieren die Grenzen eines gemeinsam ausgeführten CI-Prozesses. Getrennte QA, die echte Betreiberpolicy und Abnahmeläufe bleiben Betriebsaufgaben.
- Nach Integration von #52 ist #53 auf main umzustellen und am neuen Ziel neu zu prüfen.
- Review-Nachprüfung: `pytest.toml`, `.pytest.toml` und `.coveragerc` sind im GitHub-Nachweischeck geschützt. Vier Regressionstests prüfen Neuanlagen und den alten Pfad einer Umbenennung.
- Die Betreiberanleitung dokumentiert separate Konten für die verifizierten K-Reviews und ein eigenes Verzeichnis für gebundene Abnahmetests.
- Lokale Prüfungen am korrigierten Stand: Format, Lint und Typprüfung bestanden. Der vollständige `make check` scheitert in dieser Sitzung am blockierten Loopback-Bind der Dashboard-Tests. Die offline ausführbare Testsuite einschließlich der neuen Regressionstests, Secret-Scan, Linkprüfung und Konfigurationsprüfung sind grün. Drei Dashboard-Testmethoden wurden im zweiten Lauf wegen der Loopback-Sperre ausgenommen. Der optionale MCP-Test wurde mangels Abhängigkeit übersprungen. Der vollständige Nachweis folgt per PR-CI.
