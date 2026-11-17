# ADR 0001: localtrader als Codebasis, Standardbibliothek, make check

Datum: 2026-11-17 · Status: angenommen

## Kontext

Das Repository enthielt nur ein Reinforcement-Learning-Notebook von 2021 (gym, stable-baselines 2),
das mit aktuellen Python-Versionen nicht mehr lauffähig ist und weder Kostenmodell noch Risiko-Engine hat.
Lokal existierte ein unversionierter, getesteter MVP (`localtrader`) mit passender Architektur.

## Entscheidung

- `localtrader` wird unverändert als Basis übernommen und schrittweise an die Projektvorgaben angepasst.
- Das Notebook bleibt unter `legacy/` als historische Referenz und wird nicht weiterentwickelt.
- Die Laufzeit bleibt bei der Python-Standardbibliothek (≥ 3.12). Entwicklungswerkzeuge sind exakt gepinnt
  (`requirements-dev.txt`): pytest, ruff, mypy, coverage, pyyaml.
- `make check` bündelt Formatprüfung, Lint, Typprüfung, Tests, Secret-Scan und Konfigurationsprüfung.
  GitHub Actions führt dieselben Ziele bei jedem Push und Pull Request aus.

## Alternativen

- Neuaufbau: verworfen, weil vorhandene Komponenten die meisten Architekturvorgaben bereits abbilden.
- pandas/pyarrow/PostgreSQL sofort einführen: zurückgestellt. Ohne Paketinstallation in der
  Entwicklungsumgebung wären Builds nicht reproduzierbar prüfbar. SQLite und CSV sind ausreichend
  für den MVP; ein Wechsel wird eigenständig entschieden und dokumentiert.

## Konsequenzen

- Keine externen Laufzeitabhängigkeiten, einfache Docker-Images.
- Größere Datenmengen sind mit reinen Python-Listen langsamer; das ist für 15-Minuten-Daten weniger
  Instrumente akzeptabel.
