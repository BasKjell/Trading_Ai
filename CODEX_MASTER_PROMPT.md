# Codex-Masteranweisung: Lokaler KI-Trading-Bot

## Auftrag

Arbeite als leitender Python-, Quant- und DevOps-Entwickler direkt im bestehenden Projektordner. Entwickle aus dem vorhandenen Stand schrittweise eine vollständig lokale, reproduzierbare Research- und Paper-Trading-Plattform für ETFs, liquide Aktien und Kryptowährungen.

Der erste Meilenstein ist ein belastbarer MVP mit:

- Docker und Python,
- ETF-Trendfolge,
- Aktien-Momentum,
- BTC-/ETH-Trendfolge,
- einer zentralen Risiko- und Portfolio-Engine,
- realistischem Backtesting,
- ausschließlich simuliertem Paper-Trading,
- automatisierten Tests,
- nachvollziehbaren Ergebnisberichten und Dokumentation.

Bereite klare Schnittstellen für spätere Module vor:

- Machine Learning,
- Small-Cap-News-Analyse,
- Shortseller-Intelligence,
- Event- und Crisis-Trading.

Die spätere Erweiterbarkeit darf den MVP nicht unnötig kompliziert machen. Baue zuerst einen kleinen, überprüfbaren und stabilen Kern.

## Projektkontext und Forschungsziel

Das System ist ein Forschungsprojekt, kein Versprechen auf Profitabilität und keine Anlageberatung.

- Forschungsziel: langfristig durchschnittlich etwa **1 % pro Monat** nach realistisch modellierten Kosten.
- Risikotoleranz: maximal **15 % Portfolio-Drawdown** als harte Risikoschwelle.
- Maximal geplantes Risiko pro neuem Trade: **0,5 % des aktuellen Portfoliowerts**.
- Bevorzugtes Profil: ausgewogen, ohne aggressiven Hebel.
- Startmodus: Backtesting und Paper-Trading.
- Echtgeldorders sind ausdrücklich verboten.

Die Zielrendite ist nur ein Evaluationsziel. Optimiere niemals durch versteckten Hebel, übermäßige Positionskonzentration, unrealistische Ausführung, Datenlecks oder selektive Ergebnisdarstellung auf diese Zahl hin. Negative Ergebnisse sind valide Ergebnisse und müssen unverfälscht dokumentiert werden.

## Nicht verhandelbare Sicherheitsregeln

1. **Keine Echtgeldorders.** Implementiere und teste ausschließlich Backtests, Simulationen und nachweislich als Paper/Sandbox gekennzeichnete Broker-Verbindungen.
2. Eine Live-Order darf weder durch eine Konfiguration noch versehentlich durch einen Standardwert möglich sein. Unbekannte oder fehlende Umgebungswerte müssen zu einem sicheren Abbruch führen.
3. Falls bereits Broker-Code existiert, prüfe vor jeder Order, dass der Paper-Endpunkt und ein Paper-Konto aktiv sind. Bei unklarer Umgebung: keine Order.
4. Implementiere keine Auszahlungsfunktion, keine Überweisungsfunktion und keine Verwaltung von Broker-Zugangsdaten.
5. Secrets gehören ausschließlich in lokale Umgebungsvariablen oder eine ignorierte `.env`-Datei. Niemals Zugangsdaten, Tokens, Kontonummern oder persönliche Daten ausgeben, protokollieren oder committen.
6. Keine ungedeckten Optionen, keine nackten Short-Positionen, keine Futures mit physischer Lieferung und kein Hebel im MVP.
7. Die KI oder ein LLM darf niemals unmittelbar Orders erzeugen oder an einen Broker senden. Jede Entscheidung muss eine deterministische Risiko- und Freigabeschicht durchlaufen.
8. Keine Strategie darf automatisch vom Research- in den Paper- oder Live-Modus wechseln. Freigaben müssen explizit, nachvollziehbar und manuell sein.
9. Erfinde keine Marktdaten, Backtest-Ergebnisse, Testresultate oder Leistungskennzahlen. Kennzeichne Beispielwerte immer als Beispiele.
10. Wenn ein Risikolimit, Datenqualitätscheck oder Positionsabgleich fehlschlägt, gilt **fail closed**: keine neue Position.

## Arbeitsweise im bestehenden Repository

Beginne nicht mit einer Neuanlage des Projekts. Der Ordner enthält bereits Code und möglicherweise uncommittete Arbeit.

1. Lies zuerst vorhandene Dateien wie `README.md`, `AGENTS.md` (falls vorhanden), Projektkonfiguration, Docker-Dateien, Tests und Quellcode.
2. Prüfe den Git-Status und behandle alle vorhandenen Änderungen als Arbeit des Nutzers.
3. Bewahre funktionierende Komponenten. Refaktoriere nur, wenn dies für die Anforderungen nötig ist.
4. Überschreibe oder lösche keine unbekannten Dateien und führe keine destruktiven Git-Befehle aus.
5. Lies niemals den Inhalt echter Secret-Dateien wie `.env` in Protokolle oder Abschlussberichte ein.
6. Erstelle nach der Bestandsaufnahme einen kurzen, priorisierten Implementierungsplan mit überprüfbaren Meilensteinen.
7. Implementiere danach selbstständig und schrittweise. Bleibe nicht bei einem Plan stehen, solange die Arbeit lokal und sicher fortgesetzt werden kann.
8. Führe nach jedem sinnvollen Abschnitt die betroffenen Tests aus und behebe Fehler, bevor du weiterbaust.
9. Halte Änderungen klein, modular und gut benannt. Vermeide unnötige Microservices und unnötige Abstraktionen.
10. Dokumentiere Annahmen und Entscheidungen dort, wo sie später für Reproduzierbarkeit wichtig sind.

## Prioritäten

### Priorität P0 – Wochenend-MVP

P0 muss vollständig funktionieren, bevor komplexe KI- oder News-Module implementiert werden:

1. Bestandsaufnahme und sichere Projektkonfiguration.
2. Einheitliches Datenmodell für Bars, Signale, Orders, Fills, Positionen und Portfoliostand.
3. ETF-Trendfolge.
4. Aktien-Momentum.
5. BTC-/ETH-Trendfolge am Spotmarkt.
6. Zentrale Risiko- und Portfolio-Engine.
7. Realistisches Backtesting mit Kosten und zeitlich sauberer Validierung.
8. Paper-Trading-Simulator und optional sicher verifizierte Broker-Sandbox.
9. Tests, reproduzierbare Beispielkonfigurationen und Dokumentation.
10. Ergebnisbericht mit ehrlicher Bewertung gegen Benchmarks.

### Priorität P1 – Stabilisierung

- Monitoring und Alarmierung.
- Persistenz und Wiederanlauf nach Fehlern.
- Datenqualitätsprüfungen.
- Positionsabgleich und idempotente Orderverarbeitung.
- Walk-forward-Experimentverwaltung.
- Champion/Challenger-Registry für Strategiekonfigurationen.
- Optional FastAPI und ein lokales Dashboard, wenn der Kern bereits stabil ist.

### Priorität P2 – spätere Forschungsbausteine

- ML-Signalfilter.
- Small-Cap-News-Modul.
- Shortseller-Intelligence.
- Crisis-Radar und definierte Event-Strategien.

P2 bleibt zunächst im Shadow-, Research- oder Paper-Modus und darf den P0-Kern nicht destabilisieren.

## Zielarchitektur

Passe die Architektur an den vorhandenen Code an. Nutze dieses Modell als Ziel, nicht als Grund für einen Komplettumbau:

```text
Marktdaten / News / Broker-Sandbox
              |
              v
       Daten- und Eventschicht
              |
              v
  Strategie- und spätere ML-Schicht
              |
              v
    Zentrale Risiko-Engine
              |
              v
   Portfolio- und Order-Engine
              |
              v
 Backtest oder Paper-Execution
              |
              v
 Monitoring, Audit und Reports
```

Empfohlene logische Module:

- `data`: Adapter, Normalisierung, Kalender, Zeitzonen und Datenvalidierung.
- `strategies`: reine Signalerzeugung ohne Brokerzugriff.
- `portfolio`: Zielgewichte, Positionen, Cash und Rebalancing.
- `risk`: Positionsgrößen, Risikolimits, Drawdown-Regeln und Kill-Switch.
- `execution`: Backtest-Fills, Paper-Fills und ausschließlich Paper-Broker-Adapter.
- `backtest`: Event- oder vektorbasiertes Testen mit identischer Strategie- und Risikologik.
- `paper`: dauerhafte Simulation, Zustandswiederherstellung und Positionsabgleich.
- `research`: Walk-forward-Tests, Parameterstudien und Benchmark-Vergleiche.
- `ml`: spätere Feature-, Training-, Registry- und Shadow-Scoring-Funktionen.
- `events`: spätere News-, Shortseller- und Crisis-Events.
- `api` beziehungsweise `dashboard`: optionaler lokaler Lesezugriff auf Status und Kennzahlen.

Die Strategie darf nicht selbst Orders versenden. Sie erzeugt Signale oder Zielpositionen. Risiko- und Order-Engine entscheiden deterministisch, ob und in welcher Größe eine simulierte Order zulässig ist.

## Technische Leitplanken

- Python 3.12 oder die bereits sauber verwendete kompatible Version.
- Typisierte Datenmodelle, klare Interfaces und verständliche Fehlertexte.
- `pytest` für Unit- und Integrationstests.
- Docker Compose für reproduzierbare lokale Ausführung.
- Abhängigkeiten versionieren beziehungsweise mit sinnvollen Obergrenzen fixieren.
- Konfiguration über versionierte YAML/TOML-Dateien und Umgebungsvariablen für Secrets.
- UTC intern als Zeitstandard; Darstellung in der jeweils passenden Börsenzeitzone.
- Deterministische Seeds für Experimente.
- Parquet für historische Zeitreihen ist zulässig und für den MVP oft ausreichend.
- PostgreSQL kann für dauerhaften Paper-Zustand, Events und Audit-Logs ergänzt werden, wenn es zum vorhandenen Projekt passt.
- Redis, FastAPI, Prometheus und Grafana sind optional. Füge sie nur hinzu, wenn ein konkreter MVP-Nutzen besteht.
- Verwende bestehende, gepflegte Open-Source-Bibliotheken, wenn sie den Bedarf klar abdecken. Vermeide einen erzwungenen Framework-Wechsel.

## Einheitliche Domänenmodelle

Definiere mindestens folgende typisierte Objekte oder äquivalente Strukturen:

- `Bar`: Symbol, Markt, Zeiteinheit, Start/Ende, OHLCV, Quelle, Empfangszeit.
- `Signal`: Strategie, Symbol, Signalzeit, frühester Ausführungszeitpunkt, Richtung, Stärke, Begründung und Versionskennung.
- `OrderIntent`: gewünschte Richtung, Zielgröße, Orderart, Gültigkeit und Risiko-Metadaten.
- `RiskDecision`: erlaubt/abgelehnt, genehmigte Größe, verletzte Limits und Begründung.
- `PaperOrder`: eindeutige ID, Status, Zeitstempel, gewünschter und ausgeführter Preis.
- `Fill`: Menge, Preis, simulierte Gebühren, Spread und Slippage.
- `Position`: Menge, durchschnittlicher Einstieg, realisierte und unrealisierte Gewinne/Verluste.
- `PortfolioSnapshot`: Cash, Equity, Exposure, offene Risiken, Drawdown und Höchststand.
- `MarketEvent`: späterer gemeinsamer Eventtyp für News, Filings, Shortseller-Berichte und Krisensignale.

Alle Zeitstempel müssen timezone-aware sein. Jede Order und jeder Fill muss idempotent verarbeitbar und auditierbar sein.

## Marktdaten und Datenqualität

Baue Datenquellen über Adapter an, damit Anbieter austauschbar bleiben.

### MVP-Universum

- ETFs: mindestens ein breiter US-Aktienindex-ETF und optional Nasdaq-100, globale Aktien, Staatsanleihen, Gold und Rohstoffe.
- Aktien: zunächst liquide Large Caps beziehungsweise ein begrenztes, reproduzierbares Universum.
- Krypto: ausschließlich BTC und ETH, Spotmarkt, zunächst in einer stabilen Quote-Währung.

### Pflichtprüfungen

- Keine doppelten Zeitstempel.
- Streng monoton sortierte Zeitachse.
- Plausible OHLC-Beziehungen und nichtnegative Volumina.
- Erkennung fehlender Bars und erklärbarer Marktschließungen.
- Einheitliche Symbol- und Börsen-Metadaten.
- Korrekte Handelskalender für Aktien/ETFs und 24/7-Kalender für Krypto.
- Splits, Dividenden und Corporate Actions korrekt behandeln.
- Angepasste und nicht angepasste Preise nicht unkontrolliert vermischen.
- Datenquelle, Abrufzeit, Zeitraum und Hash des Datensatzes speichern.
- Ausfälle oder unvollständige Daten dürfen nicht stillschweigend durch Zukunftsdaten gefüllt werden.

Signale auf Basis eines Schlusskurses dürfen frühestens nach Verfügbarkeit dieses Schlusskurses ausgeführt werden. Der Standard im Tages-Backtest soll eine Ausführung zur nächsten realistisch verfügbaren Periode modellieren, nicht rückwirkend zum bekannten Schlusskurs.

## Startstrategie A: ETF-Trendfolge

Implementiere eine einfache, erklärbare Long/Cash-Strategie als Baseline.

Mögliche Startregeln, vollständig konfigurierbar:

- Long nur, wenn der Kurs über einem langfristigen Trendfilter liegt, zum Beispiel SMA 200.
- Einstieg bei positivem mittelfristigem Momentum oder einem Ausbruch über ein rollierendes Hoch.
- Volatilitätsbasierte Positionsgröße.
- ATR-basierter Trailing-Stop oder regelbasierter Trend-Ausstieg.
- Kein Short und kein Hebel.
- Optionaler Wechsel in Cash oder einen defensiven, liquiden Baustein, wenn der Trendfilter negativ ist.

Teste robuste Parameterbereiche, aber suche nicht nach einem einzelnen historisch perfekten Wert. Vergleiche mindestens gegen Buy-and-Hold und eine einfache statische Portfolio-Benchmark.

## Startstrategie B: Aktien-Momentum

Implementiere ein transparentes Long-only-Momentum-Modell für liquide Aktien.

Mögliche Regeln:

- Mindestpreis und Mindestliquidität.
- Positiver langfristiger Trend.
- Relative Stärke über mehrere mittlere Horizonte.
- Optional die jüngste kurze Periode aus dem Momentum-Score auslassen.
- Volatilitätsanpassung und Begrenzung einzelner Positionen.
- Wöchentliches oder monatliches Rebalancing statt unnötig hoher Handelsfrequenz.
- Sektor- und Korrelationslimits.
- Keine Käufe bei fehlenden, veralteten oder offensichtlich fehlerhaften Daten.

Survivorship Bias muss explizit behandelt werden. Falls zunächst nur ein heutiges Universum verfügbar ist, kennzeichne die Resultate deutlich als vorläufig und nicht als belastbaren Nachweis.

## Startstrategie C: BTC-/ETH-Trendfolge

Implementiere eine getrennte, aber strukturell ähnliche Spot-Strategie für BTC und ETH.

Mögliche Regeln:

- Tages- und optional 4-Stunden-Zeiteinheit.
- EMA 50/200 oder vergleichbarer Trendfilter.
- Ausbruch beziehungsweise positives Momentum als Einstieg.
- ATR- oder volatilitätsbasierter Ausstieg und Trailing-Stop.
- Positionsgröße aus Portfolio-Risiko und Stop-Distanz.
- Nur Long oder Cash im MVP.
- Kein Margin-Handel, keine Perpetuals, keine Futures und kein Hebel.

Modelliere Krypto-Gebühren, Spread, Slippage sowie den durchgehenden 24/7-Handel getrennt von Aktien- und ETF-Kosten.

## Zentrale Risiko-Engine

Die Risiko-Engine ist unabhängig von den Strategien und muss vor jedem simulierten Auftrag entscheiden.

### Harte Anfangswerte

- Maximal geplantes Risiko pro Trade: **0,5 % der aktuellen Portfolio-Equity**.
- Konservativer Standardwert: **0,25 %**, konfigurierbar bis höchstens 0,5 %.
- Drawdown-Risikoleiter:
  - ab 8 % Drawdown: neue Positionsrisiken und Ziel-Exposure reduzieren,
  - ab 12 % Drawdown: keine neuen spekulativen oder experimentellen Positionen,
  - ab 15 % Drawdown: keine neuen Orders, offene Einstiegsorders stornieren, Kill-Switch setzen und kontrollierten Risikoabbau nach einer dokumentierten Regel einleiten.
- Maximaler geplanter Tagesverlust: anfänglich **1,5 %**.
- Kein Hebel im MVP.
- Maximalgewicht pro ETF, Aktie und Kryptowährung konfigurierbar.
- Gesamt-Exposure, Sektor-Exposure, Krypto-Exposure und korrelierte Risiken begrenzen.
- Nach Daten-, Broker-, Zustands- oder Positionsabweichungen keine neuen Orders.

Die 15-%-Schwelle ist keine Garantie, dass der tatsächliche Drawdown nie größer wird. Gap-Risiken, illiquide Märkte und Slippage müssen in Dokumentation und Tests berücksichtigt werden.

### Positionsgröße

Berechne die Positionsgröße grundsätzlich aus:

```text
zulässiger Geldverlust = Portfolio-Equity × Risikoanteil pro Trade
Risikodistanz je Einheit = |Einstiegspreis - Stoppreis|
Rohgröße = zulässiger Geldverlust / Risikodistanz je Einheit
```

Begrenze die Rohgröße anschließend durch Cash, Maximalgewicht, Liquidität, Exposure, Mindestordergröße und weitere Portfoliolimits. Bei fehlender oder unplausibler Stop-Distanz wird der Trade abgelehnt.

### Konfigurierbare Forschungsaufteilung

Lege höchstens eine Beispielkonfiguration an, keine fest verdrahtete Allokation:

- 40 % ETF-Trendfolge,
- 25 % Aktien-Momentum,
- 10 % BTC-/ETH-Trendfolge,
- später maximal 5 % Event-/Crisis-Sleeve,
- 20 % Liquidität beziehungsweise Risikoreserve.

Unterscheide klar zwischen Kapitalgewicht, Ziel-Exposure und Risikobudget. Diese Werte sind Forschungsparameter und keine Anlageempfehlung.

## Backtesting

Backtest und Paper-Trading sollen möglichst dieselbe Strategie-, Risiko-, Portfolio- und Fill-Logik verwenden.

### Pflichtanforderungen

- Keine Look-ahead- oder Target-Leakage.
- Chronologische Datenaufteilung.
- Walk-forward-Tests statt zufälliger Zeitreihen-Splits.
- Bei überlappenden Labels geeignete Purge- und Embargo-Zonen.
- Strikte Trennung von Entwicklung, Validierung und finalem Holdout.
- Das finale Holdout darf nicht wiederholt zur Parameterwahl genutzt werden.
- Gebühren, halber beziehungsweise vollständiger Spread, Slippage und Umsatzkosten modellieren.
- Mindestens Basiskosten und ein verschärfter Kosten-Stresstest, zum Beispiel 2× und 3× Kosten.
- Ausführungslatenz und frühesten realistischen Fill-Zeitpunkt berücksichtigen.
- Dividenden, Splits und Delistings soweit für die Daten verfügbar berücksichtigen.
- Fehlgeschlagene, unvollständige und abgelehnte Orders abbilden.
- Keine nachträgliche Auswahl nur erfolgreicher Märkte oder Zeiträume.

### Testzeiträume

Nutze mehrere Marktregime, soweit Daten vorhanden sind:

- steigende Märkte,
- fallende Märkte,
- Seitwärtsphasen,
- Hoch- und Niedrigvolatilitätsphasen,
- Stressphasen.

Baue keine Strategie ausschließlich um bekannte Krisentermine herum. Vergleiche positive Beispiele immer auch mit Fehlalarmen und normalen Perioden.

### Kennzahlen

Berichte mindestens:

- Gesamtrendite und CAGR,
- monatliche Renditeverteilung,
- Anteil positiver Monate,
- annualisierte Volatilität,
- Sharpe Ratio,
- Sortino Ratio,
- maximaler Drawdown und Drawdown-Dauer,
- Calmar Ratio,
- Profit Factor,
- Trefferquote und durchschnittlicher Gewinn/Verlust,
- Turnover, Kosten, Slippage und Handelsfrequenz,
- durchschnittliche und maximale Exposure,
- Anzahl unabhängiger Trades,
- Ergebnis pro Strategie und Gesamtportfolio,
- Vergleich mit passenden passiven Benchmarks.

Das Forschungsziel von 1 % pro Monat darf zusätzlich ausgewertet werden, aber nicht als bestanden gelten, wenn es nur durch einzelne Ausreißer, unrealistische Annahmen oder einen unvertretbaren Drawdown erreicht wird.

## Paper-Trading

Implementiere zuerst einen internen deterministischen Simulator. Ein externer Broker-Sandbox-Adapter ist optional und darf nur ergänzt werden, wenn eindeutig sichergestellt ist, dass ausschließlich Paper-Trading möglich ist.

### Anforderungen

- Zustände über Neustarts hinweg wiederherstellen.
- Eindeutige Client-Order-IDs und idempotente Verarbeitung.
- Orderstatus, Teilfüllungen, Ablehnungen und Stornierungen modellieren.
- Vor jeder Order Portfolio- und Broker-/Simulatorpositionen abgleichen.
- Bei Abweichung keine neue Order und einen Alarm erzeugen.
- Marktzeiten, Feiertage, Krypto-24/7-Betrieb und Zeitzonen korrekt behandeln.
- Nach einem Neustart keine doppelte Order erzeugen.
- Alle Entscheidungen, Ablehnungen, Orders und Fills in einem Audit-Log speichern.
- Einen globalen Kill-Switch und einen manuellen Pause-Modus bereitstellen.
- Standardmäßig in einem Offline-/No-op-Modus starten.

Erstelle niemals einen Live-Modus als versteckte Option. Falls vorhandener Code Live-Endpunkte unterstützt, trenne oder blockiere diese für dieses Projekt ausdrücklich.

## Monitoring und Berichte

Der MVP soll mindestens lokale, reproduzierbare Berichte erzeugen. Ein Dashboard ist optional.

Zeige beziehungsweise speichere:

- Equity-Kurve,
- Drawdown-Kurve,
- Monatsrenditen,
- Positionen und offene Risiken,
- Exposure nach Markt und Strategie,
- Kosten und Slippage,
- letzte Signale und Risikoentscheidungen,
- Datenalter und Datenqualitätsstatus,
- Paper-Orderstatus,
- Kill-Switch- und Pausenstatus,
- Abweichung zwischen Backtest-Annahme und Paper-Ausführung.

Alarme sollen bei veralteten Daten, fehlgeschlagenen Jobs, Positionsabweichungen, Limitverletzungen und ungewöhnlicher Slippage entstehen. Für den MVP reicht eine lokale, gut sichtbare Ausgabe oder eine persistierte Alarmtabelle; externe Benachrichtigungen sind nicht erforderlich.

## Späteres ML-Modul

Machine Learning wird erst ergänzt, wenn die regelbasierten Baselines korrekt und reproduzierbar laufen.

### Rolle des Modells

- ML dient zunächst nur als Filter, Ranking oder Positionsgrößen-Modifikator.
- ML darf keine Risiko-Engine umgehen.
- Das Modell läuft zuerst im Shadow-Modus und beeinflusst keine Paper-Orders.
- Ein LLM ist kein Preisprognosemodell und gibt keine direkten Handelssignale an den Executor.

### Startmodell

Beginne mit einfachen, kontrollierbaren Modellen, beispielsweise logistischer Regression, LightGBM oder Gradient Boosting. Ein komplexes neuronales Netz ist nur gerechtfertigt, wenn es out-of-sample nachweislich Mehrwert bringt.

### Mögliche Features

- Trend und EMA-Abstände,
- Momentum über mehrere Horizonte,
- ATR und realisierte Volatilität,
- relatives Volumen,
- relative Stärke gegenüber Index oder Sektor,
- Korrelation und Marktregime,
- Wochentag und Handelszeit, falls sachlich begründet,
- später point-in-time verfügbare Eventmerkmale.

### Validierung und Freigabe

- Zeitliche Splits, Walk-forward, Purge und Embargo.
- Wahrscheinlichkeitskalibrierung.
- Feature-Verfügbarkeit zum tatsächlichen Signalzeitpunkt dokumentieren.
- Vergleich gegen exakt dieselbe Strategie ohne ML.
- Champion/Challenger-Ansatz mit unverändertem Holdout.
- Promotion nur bei robuster Verbesserung nach Kosten und über mehrere Regime.
- Keine automatische Promotion in Paper-Trading.
- Modellversion, Features, Datenhash, Konfiguration, Seed und Codeversion speichern.

Wenn ML keinen stabilen out-of-sample Mehrwert liefert, bleibt es deaktiviert. Das ist ein akzeptables und wichtiges Forschungsergebnis.

## Späteres Small-Cap-News-Modul

Dieses Modul bleibt zunächst ein Scanner und Paper-Research-System.

### Forschungsuniversum

Konfigurierbare Startfilter, deren Wirkung historisch geprüft werden muss:

- regulierte Hauptbörsen, zunächst kein OTC,
- Kurs über 5 USD,
- Marktkapitalisierung grob 300 Mio. bis 2 Mrd. USD,
- ausreichender durchschnittlicher Tagesumsatz,
- Spread möglichst unter 0,5 %,
- keine ausgesetzten Werte,
- keine ungeklärten Corporate Actions.

### Quellen und Datenmodell

- Primärquellen wie regulatorische Filings und Unternehmensmeldungen priorisieren.
- Nur lizenzkonforme News-Quellen verwenden.
- Veröffentlichungszeit, erste Empfangszeit, Quelle, Symbolzuordnung und spätere Korrekturen speichern.
- Deduplizieren und wiederveröffentlichte alte Meldungen erkennen.
- Point-in-time-Fundamentaldaten verwenden.

### LLM-Aufgabe

Das LLM extrahiert ausschließlich strukturierte Merkmale, zum Beispiel:

- Ereignistyp,
- Neuheit,
- positive/negative/unklare Richtung,
- wirtschaftliche Größenordnung,
- verbindlicher Vertrag oder unverbindliche Absicht,
- betroffene Produkte und Regionen,
- Evidenzqualität,
- mögliche Verwässerung,
- Unsicherheiten und fehlende Informationen.

Die Ausgabe wird gegen ein festes Schema validiert. Unsichere oder widersprüchliche Ergebnisse führen nicht zu einem Trade.

### Forschungsstrategien

- News Momentum nach bestätigter Meldung, Liquidität und technischer Bestätigung.
- News Underreaction über mehrere Tage.

Vergleiche beide Ansätze gegen dieselben Regeln ohne LLM. Berücksichtige, dass ein nach Veröffentlichung sichtbarer Kurssturz nicht beweist, dass ein später Einstieg noch profitabel möglich war.

## Spätere Shortseller-Intelligence

Baue keinen automatischen Kopierhandel. Das Modul soll öffentliche Berichte und gesetzlich veröffentlichte Short-Positionen als Research-Ereignisse erfassen.

### Aufgaben

- Neue Berichte bekannter Research-Anbieter erkennen.
- Öffentliche Positionsänderungen, soweit rechtmäßig verfügbar, zeitgestempelt erfassen.
- Behauptungen gegen Primärquellen, Filings, Abschlüsse und Unternehmensreaktionen prüfen.
- Qualität, Neuheit, Unabhängigkeit der Belege und Gegenargumente strukturiert bewerten.
- Kursreaktion nach Veröffentlichung über mehrere Horizonte untersuchen.
- Historische Fälle mit dem damals tatsächlich verfügbaren Informationsstand testen.

Ein Shortseller hat ein eigenes wirtschaftliches Interesse. Ein Bericht allein ist niemals ein ausreichendes Handelssignal. Ein mögliches Research-Signal erfordert unabhängige Bestätigung, ausreichende Liquidität, kontrolliertes Squeeze-Risiko und eine negative Kursbestätigung.

Im ersten Ausbau bleibt dieses Modul bei Alerts und Paper-Trades. Leihbarkeit, Leihgebühren, Rückrufrisiko, Margin und Gaps müssen später explizit modelliert werden.

## Späteres Crisis- und Event-Trading

Ziel ist nicht, Krisen zuverlässig vorherzusagen. Das Modul soll neue fundamentale Brüche erkennen, ihre mögliche Marktwirkung strukturieren und prüfen, ob noch eine asymmetrische, liquide und klar begrenzte Paper-Chance besteht.

### Ereignisklassen

- Unternehmenskrisen: verweigertes Testat, Finanzierungsprobleme, Bilanzvorwürfe, Insolvenzrisiko.
- Makro- und geopolitische Schocks: Krieg, Sanktionen, Handelsrouten und Energieversorgung.
- Systemische Krisen: Banken-, Kredit- oder Liquiditätsstress.
- Marktstrukturereignisse: extreme Futures-Kurven, Liquidationen, Handelsaussetzungen und stark verändertes Open Interest.

### Module

- `crisis-radar`: erkennt und dedupliziert ungewöhnliche Ereignisse.
- `event-intelligence`: sammelt Primärbelege und erzeugt eine strukturierte These mit Gegenargumenten.
- `opportunity-engine`: prüft Marktreaktion, Liquidität, Instrumente, Kosten und verbleibendes Potenzial.
- `thesis-risk-engine`: begrenzt Gesamtrisiko, Tranchen und Abbruchkriterien.

### Positionsaufbau

- Nie allein deshalb aufstocken, weil der Kurs bereits in die gewünschte Richtung läuft.
- Eine neue Tranche erfordert neue unabhängige Evidenz, weiterhin ausreichende Liquidität und freies Risikobudget.
- Alle Tranchen einer These teilen ein gemeinsames Gesamtrisikolimit.
- Gewinner dürfen nicht benutzt werden, um das ursprüngliche Risikolimit stillschweigend zu erhöhen.
- Teilgewinn-, Trailing- und These-widerlegt-Regeln vor dem Einstieg definieren.

### Instrumente und Limits

- Zunächst nur simulierte, verlustbegrenzte Strukturen erforschen.
- Gekaufte Optionen oder definierte Spreads dürfen später simuliert werden; maximaler Prämienverlust und Zeitwertverfall müssen korrekt modelliert werden.
- Keine nackten Optionen und keine unbegrenzt verlustfähigen Short-Konstruktionen.
- Futures mit physischer Lieferung sind ausgeschlossen.
- Beispielwert: Event-/Crisis-Kapital höchstens 5 % des Portfolios; geplanter Maximalverlust einer einzelnen These anfänglich höchstens 1 % und absolut höchstens 2 %. Diese Grenzen sind konfigurierbare Forschungswerte und müssen von der zentralen Risiko-Engine zusätzlich begrenzt werden.

Krisen-Trading ist unregelmäßig und darf nicht als Quelle einer konstanten Monatsrendite dargestellt werden.

## Tests

Schreibe Unit-, Integrations- und Smoke-Tests für mindestens:

- Indikatorberechnung ohne Zukunftsdaten.
- Signalzeit und frühesten Ausführungszeitpunkt.
- Positionsgrößenformel und Grenzfälle.
- 0,5-%-Risikolimit.
- Drawdown-Stufen bei 8 %, 12 % und 15 %.
- Tagesverlustlimit.
- Exposure- und Konzentrationslimits.
- Ablehnung bei veralteten oder fehlerhaften Daten.
- Gebühren-, Spread- und Slippageberechnung.
- Orderstatus und Teilfüllungen.
- Idempotente Wiederholung desselben Events.
- Neustart ohne doppelte Paper-Order.
- Kill-Switch und fail-closed-Verhalten.
- Aktienhandelszeiten und Krypto-24/7-Kalender.
- Reproduzierbarkeit mit festem Seed.
- Datenhash und Konfigurationssnapshot.
- Backtest ohne Look-ahead anhand gezielter Fixtures.
- Broker-Adapter, der Live-Endpunkte zuverlässig ablehnt.

Nutze kleine synthetische Fixtures für deterministische Grenztests. Verwende echte historische Daten nur zusätzlich für Smoke- und Research-Läufe.

## Akzeptanzkriterien für den MVP

Der MVP ist erst abgeschlossen, wenn:

1. Das bestehende Projekt ohne Verlust vorhandener Arbeit erweitert wurde.
2. Die dokumentierte lokale Installation auf einem sauberen System nachvollziehbar ist.
3. Docker-Image und relevante Compose-Dienste erfolgreich gebaut werden.
4. Alle automatisierten Tests bestehen.
5. Mindestens je ein reproduzierbarer Backtest für ETF, Aktien-Momentum und BTC/ETH möglich ist, sofern die erforderlichen Daten verfügbar sind.
6. Gebühren, Spread, Slippage und realistische Signal-/Fill-Zeitpunkte einbezogen sind.
7. Die Risiko-Engine 0,5 % pro Trade und die Drawdown-Leiter korrekt erzwingt.
8. Eine deterministische Paper-Session ohne Echtgeldzugang ausgeführt werden kann.
9. Der Kill-Switch nachweislich funktioniert.
10. Benchmarks und Strategieergebnisse ehrlich nebeneinander berichtet werden.
11. Kein Secret im Repository oder in Logs enthalten ist.
12. README und Beispielkonfigurationen die tatsächlich funktionierenden Befehle enthalten.
13. Bekannte Grenzen, fehlende Daten und nicht bestandene Forschungsziele dokumentiert sind.

Falls externe Daten oder eine Broker-Sandbox nicht erreichbar sind, muss eine Offline-Demo mit Fixtures oder gespeicherten Testdaten trotzdem funktionieren. Kennzeichne diesen Ersatz klar.

## Verifikation

Führe am Ende mindestens aus, angepasst an das vorhandene Projekt:

```bash
python -m pytest
docker compose config
docker compose build
```

Führe außerdem einen kleinen reproduzierbaren Backtest und eine kurze Offline-Paper-Simulation aus. Verwende nur Befehle, die keine Echtgeldorder erzeugen können.

Prüfe zusätzlich:

- Git-Diff beziehungsweise Liste geänderter Dateien.
- Keine versehentlich eingecheckten Secrets.
- Keine Live-Broker-URL in aktiver Konfiguration.
- Keine Ergebnisse oder Artefakte, die fälschlich als garantiert profitabel beschrieben werden.
- Dokumentation stimmt mit den tatsächlich getesteten Befehlen überein.

## Abschlussbericht von Codex

Beende die Arbeit mit einem kompakten, evidenzbasierten Bericht:

1. Was wurde gebaut oder geändert?
2. Welche vorhandenen Komponenten wurden weiterverwendet?
3. Welche Tests und Verifikationsbefehle wurden ausgeführt und mit welchem Ergebnis?
4. Welche Backtest- und Paper-Ergebnisse wurden tatsächlich gemessen?
5. Welche Benchmarks wurden verwendet?
6. Welche Risiken und bekannten Einschränkungen bestehen?
7. Was ist der nächste kleinste sinnvolle Schritt?
8. Welche P1-/P2-Module wurden bewusst noch nicht umgesetzt?

Unterscheide klar zwischen:

- implementiert und getestet,
- implementiert, aber nicht vollständig getestet,
- nur vorbereitet,
- noch nicht implementiert.

Wenn das Forschungsziel von durchschnittlich 1 % pro Monat nicht erreicht wird, sage das ausdrücklich. Verändere die Risikolimits nicht, um ein besseres Ergebnis vorzutäuschen.

## Definition des Erfolgs

Erfolg bedeutet für diese Aufgabe nicht, eine bestimmte Rendite zu produzieren. Erfolg bedeutet:

- korrekte und reproduzierbare Forschung,
- realistische Kosten und Ausführung,
- kein Datenleck,
- konsequentes Risikomanagement,
- sichere Trennung von Research, Paper und Live,
- ehrliche Benchmarks,
- ein stabiler, verständlicher und erweiterbarer Codebestand.

Arbeite bis zum letzten sicher erreichbaren grünen Meilenstein. Falls eine echte Blockade entsteht, dokumentiere den konkreten Grund, die bereits geprüften Alternativen und den kleinsten notwendigen nächsten Schritt. Frage nur dann nach, wenn eine fehlende Entscheidung das Ergebnis wesentlich verändern würde oder zusätzliche Berechtigungen erforderlich sind.
