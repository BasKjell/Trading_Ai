## 2026-11-18 – Review nach Risikoklasse, Sammel-PR für den Integrationsstand (T-009)

**Anlass/Ziel:** Benutzerentscheidung 2026-11-18: Reviews dort behalten, wo Fehler unumkehrbar sind, und
die Zahl der Review-Runden senken (die meisten Runden entstanden durch CHANGELOG/STATUS-Konflikte und
verfallene Freigaben nach reinen main-Merges).

**Änderungen**
- AGENTS.md §0: Risikoklassen K (Review vor Merge) und U (Klassenprüfung, Review danach gesammelt); nur
  P1/P2 blockieren, P3 → Folge-Issue; Eskalation nach zwei Runden; Merge-Diff-Prüfung genügt nach einem
  konfliktfreien main-Merge.
- AGENTS.md §2/§2a: Einzel-PRs ändern CHANGELOG.md/STATUS.md nicht mehr; täglicher Sammel-PR.
- Spec §21, PR-Vorlage (Klassenangabe), changelog.d/README.md angepasst.

**Tests:** reine Regeländerung; `make check` (inkl. Linkprüfung).
