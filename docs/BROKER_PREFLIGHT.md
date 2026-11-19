# Alpaca-Paper-Preflight (T-012)

Befehl (nur lesende `GET`-Anfragen, keine Orders):

```bash
python3 -m localtrader alpaca-preflight --env-file .env
```

Exit-Code 0 = keine Blockade, 2 = Blockade oder Fehler. Der Bericht enthält nur Flags, Anzahlen, Symbole
und Zeitstempel. Kontonummer, Konto-ID, Salden, Preise und Stückzahlen werden weder gespeichert noch
ausgegeben. Marktdaten aus der Gegenwart (nach dem Holdout-Start) werden sofort auf Zeitstempel reduziert.
Code: `localtrader/broker_preflight.py`, Tests: `tests/test_broker_preflight.py` (Fake-API, ohne Netz).

## Prüfungen

| Bereich | Geprüft | Blockiert den Start, wenn |
|---|---|---|
| Konto | Status, Sperren, PDT-Flag, Währung, Margin-Multiplikator, Short-Freigabe, Eigenkapital > 0, Abgleich mit `PAPER_BROKER_ACCOUNT_ID` (Kontonummer oder UUID) | nicht `ACTIVE`, Sperrflag gesetzt **oder nicht gemeldet**, Soll-ID fehlt oder weicht ab, kein Eigenkapital |
| Kontokonfiguration | Bruchstücke, Short-Sperre, max. Margin, Handelsaussetzung | nicht lesbar oder `suspend_trade` nicht ausdrücklich `false` (sonst Warnungen) |
| Instrumente SPY, QQQ | aktiv, handelbar, Bruchstücke, Börse | nicht aktiv oder nicht handelbar |
| Orders und Positionen | offene Orders, Positionen, Short-Positionen, Seite je Position | nicht lesbar, Orderliste möglicherweise unvollständig (volle Seite), fremde offene Orders, Short-Positionen oder Positionen ohne bekannte Seite |
| Marktuhr | offen, nächste Öffnung/Schließung, Abweichung der lokalen Uhr (inkl. Latenz) | – (Warnung ab 2 s) |
| **SIP verzögert (ADR 0006)** | neueste **abgeschlossene** 15-Minuten-SIP-Bar je ETF über den historischen Endpunkt mit `end = jetzt − 15 min`; eine noch laufende Bar wird verworfen | nicht lesbar, keine abgeschlossene Bar oder ungültiger Zeitstempel, bei offenem (oder unbekanntem) Markt älter als 35 min |
| Datenfrische (Diagnose) | `latest`-Bar und -Quote je Feed (IEX, SIP) mit Zustand ok/missing/invalid/future/stale | – (nur Warnungen; IEX und Echtzeit-SIP treiben keine Signale) |
| Rate Limits | Limit-Header beider Hosts | – |

Nicht abgedeckt (folgt mit T-006): Abgleich Brokerpositionen ↔ interne Positionen, reservierte Beträge
offener eigener Orders, Prüfung von Quote-Alter und Spread unmittelbar vor einer Order. Die erlaubten
Ordertypen liefert die API nicht je Konto; sie werden mit der ersten Paper-Order (T-006) geprüft.

Nur die Hosts `paper-api.alpaca.markets` und `data.alpaca.markets` sind zulässig; ein Live-Host oder die
Broker-Sandbox wird abgelehnt.

## Ergebnis am 2026-11-19 mit SIP-Startbedingung (Review PR #36, Runde 2, Markt offen)

- **Keine Blockade, Exit 0.** Neueste abgeschlossene verzögerte SIP-Bar für SPY und QQQ: Ende 16:00 UTC,
  Alter 1.237 s (≈ 21 min, im Normalbereich 15–35 min). Beim ersten Versuch fiel auf, dass Alpaca `end` auf den
  Bar-Beginn anwendet und die noch laufende Bar mitliefert; sie wird seitdem verworfen (Test).
- Warnungen unverändert: Margin-Konto, Leerverkauf erlaubt, SIP-`latest` HTTP 403 (nur Diagnose).

## Ergebnis am 2026-11-19 nach den verschärften Regeln (Review PR #36, Markt offen)

- **Keine Blockade, Exit 0.** Konto bestätigt (`PAPER_BROKER_ACCOUNT_ID` stimmt), Pflichtflags und
  `suspend_trade` ausdrücklich `false`, Orders und Positionen vollständig lesbar, aktuelle IEX-Bars für SPY und QQQ.
- **Warnungen:** Margin-Konto (Multiplikator 4), Leerverkauf beim Broker erlaubt, SIP-`latest` (Bars und
  Quotes) mit HTTP 403, weil der Datenplan SIP nur verzögert liefert (ADR 0006).

## Ergebnis am 2026-11-18 (Markt offen, Regeln vor dem Review)

- **Keine Blockade.** Konto `ACTIVE`, keine Sperren, kein PDT-Flag, USD, Eigenkapital vorhanden, stimmt mit
  `PAPER_BROKER_ACCOUNT_ID` überein. Keine offenen Orders, keine Positionen.
- SPY (ARCA) und QQQ (NASDAQ): aktiv, handelbar, Bruchstücke erlaubt.
- Rate Limits: Paper-API 1.000, Daten-API 200 Anfragen je Minute.
- Lokale Uhr weicht vom Broker um 0,0 s ab (inkl. Latenz).
- **Warnungen:**
  1. **Margin-Konto (Multiplikator 4)** und **Leerverkauf erlaubt**. Spec §18.2 verlangt Handel ohne Margin.
     Der Paper-Dienst muss deshalb aus dem Bargeld dimensionieren und Verkäufe über den Bestand hinaus
     ablehnen. Sicherer ist zusätzlich, im Alpaca-Dashboard den maximalen Margin-Multiplikator auf 1 zu
     setzen und Leerverkäufe abzuschalten (Benutzeraktion).
  2. **SIP nicht in Echtzeit:** Die aktuelle Datenberechtigung erlaubt SIP nur verzögert
     (`HTTP 403: subscription does not permit querying recent SIP data`). In Echtzeit ist nur **IEX**
     verfügbar (letzte 1-Minuten-Bar etwa 90 s alt, Quote aktuell).

## Offene Benutzerentscheidung: Datenfeed für den Paper-Betrieb

Die Forschung (T-018, T-008) beruht auf **SIP**-Bars (alle US-Börsen). Für Entscheidungen zum Bar-Ende
braucht der Paper-Betrieb die gerade abgeschlossene 15-Minuten-Bar innerhalb weniger Sekunden.

| Option | Vorteil | Nachteil |
|---|---|---|
| **A: IEX in Echtzeit** (kostenlos) | sofort verfügbar | IEX ist eine einzelne Börse mit kleinem Marktanteil; OHLC und Volumen weichen von SIP ab. Signale passen nicht exakt zur Forschung; Abweichung müsste vorab an historischen IEX- und SIP-Bars gemessen werden |
| **B: SIP in Echtzeit** (kostenpflichtiges Alpaca-Datenabo) | gleiche Datenbasis wie die Forschung | laufende Kosten; Datenkauf nur nach Benutzerfreigabe (AGENTS.md §5) |
| **C: SIP verzögert** (kostenlos) | gleiche Datenbasis | Entscheidung erst eine Bar später; nur sinnvoll, wenn die Strategie im Stresstest mit `--delay-bars 1` tragfähig bleibt |

Empfehlung: zuerst A und C **messen** (Abweichung IEX/SIP an historischen Bars des Forschungszeitraums;
Ergebnis der Kandidaten mit einer Bar Verzögerung), dann zwischen A, B und C entscheiden. Bis dahin
bleibt T-006 bei Fake-Broker und Wiedergabe historischer Daten.
