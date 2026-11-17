# Implementierungsplan

Stand: 2026-11-17. Grundlage sind die Projektvorgaben (Abschnitte 1–17) und der übernommene `localtrader`-MVP.

## Ausgangslage

Der übernommene MVP ist reine Standardbibliothek, offline-first und enthält bereits:
Domänenmodelle, Datenvalidierung, Tages-Trendstrategien, eine zentrale Risiko-Engine,
ein gemeinsames Fill-Modell für Backtest und Paper, einen wiederanlauffähigen Offline-Paper-Simulator,
Walk-forward-Splits mit Purge/Embargo, ein SQLite-Research-Register und ein lokales Dashboard.

## Lücken gegenüber den Vorgaben

| Bereich | Ist | Soll |
|---|---|---|
| Signalintervall | Tagesbars | 15 Minuten, Vergleich 1 h / 1 d |
| Strategien | Trend, Momentum, Krypto | Trendfolge **und** Mean Reversion, Cash/Buy-and-hold-Benchmarks |
| Risiko | 0,25 %/Trade, DD 8/12/15 % | 0,20 %/Trade, 1 % offenes Stop-Risiko, 20 % je Instrument, DD 5/8/10 % |
| Kapital | 100.000 | 10.000 EUR |
| Kostenmodell | Spread/Slippage doppelt belastet | einfach, plus Mindestgebühr, Volumenbegrenzung |
| Daten | synthetisch, Alpaca-Download | CSV-Import 15 min mit Qualitätsprüfung, Zeitzonen, Datenversion |
| Orders | idempotent, nicht persistent | persistenter Zustandsautomat, Timeouts, Abgleich |
| Modi | offline | backtest / paper / shadow / live (live gesperrt) |
| Forschung | Gates, Champion-Datei | versionierte Kriterien, Champion-Challenger, Shadow, Rollback |
| Prüfungen | pytest | `make check`, GitHub Actions, Secret-Scan |

## Etappen

1. Übernahme, Tooling, CI, Dokumentation (`make check`). ✅
2. Kostenmodell korrigieren, Risikolimits gemäß Vorgaben. ✅
3. 15-Minuten-CSV-Import (DAX-Beispieldaten), Qualitätsprüfung, Handelszeiten.
4. Intraday-Trendfolge und Mean Reversion, Benchmarks, Kostenstress.
5. Persistenter Order-Zustandsautomat und Brokerabgleich.
6. Paper-Betrieb im 15-Minuten-Takt mit Wiederanlauf; Modi und Live-Sperre.
7. Dashboard und Reports erweitern.
8. Walk-forward-Forschung, versionierte Kriterien, Champion-Challenger, Rollback.
9. End-to-End-Integrationstest, Absturz/Wiederanlauf, Dokumentation.

## Konservative Annahmen

- Laufzeit nur mit Standardbibliothek; SQLite statt PostgreSQL und CSV statt Parquet, bis Abhängigkeiten
  bewusst eingeführt werden (siehe `docs/decisions/0001-codebase-and-tooling.md`).
- Die DAX-15-Minuten-Datei ist ein Kontrakt-/CFD-Kurs ohne dokumentierte Herkunft; Ergebnisse darauf sind
  nur eingeschränkt aussagekräftig (siehe Datenetappe).
