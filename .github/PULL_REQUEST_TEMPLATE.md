## Task
T-xxx (Issue #nn) · Agent/Modell/Denkstufe: …

## Problem und geändertes Verhalten

## Entscheidungen (ADR-Verweise)

## Tests und Ergebnisse
- `make check`: …
- nicht ausgeführt / übersprungen: …

## Einschränkungen

## Datenbank-, Konfigurations- oder Schnittstellenänderungen

## Demo-Jama und Nachweise
- Requirement-/Baseline-ID, Testfall-ID und CI-Evidenz oder dokumentierte Nachweislücke: …

## Review
Risikoklasse (AGENTS.md §0): **K** / **U** – Begründung: …
- [ ] Klasse K (Holdout/Daten, Engine, Orders, Risiko, Buchhaltung/Kosten, Validierung, Live-Sperren, Agentenrechte, Offenlegung geschützter Daten/Zugangsdaten, dauerhafter Zustand/Journale, Kontrollmechanismen wie CI/Branch-Schutz/Regeln) → unabhängiges Review vor dem Merge, Label `needs-independent-review`
- [ ] Klasse U → separates KI-Review vor der manuellen Integration
- [ ] `changelog.d/T-xxx.md` und `docs/handoffs/T-xxx.md` aktualisiert; **keine** Änderung an `CHANGELOG.md`/`docs/STATUS.md` (Sammel-PR)
- [ ] Klasse K → finale Integrationsfreigabe und Merge durch Bas. Klasse U → manuelle Integration durch separaten Agenten gemäß AGENTS.md §2a
- [ ] Freigabe nennt den aktuellen Head-SHA und Zielbranch; nach neuen Commits/Retargeting erneut prüfen (AGENTS.md §0)
- [ ] Alle Review-Befunde beantwortet (behoben in `<SHA>` / begründet abgelehnt)
