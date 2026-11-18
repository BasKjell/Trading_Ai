# Lokaler Coding-Runner

Status: **Planung und Offline-Tests** (T-020). Die Ausführung von Codex- oder Claude-Workern
ist technisch gesperrt. Aufrufe von `once` und `watch` ohne `--dry-run` brechen vor Login, Claims,
Worktree-Erstellung und Worker-Start ab. GitHub-Labels und Konfiguration ändern diese Sperre nicht.

## Zweck und Freigabe

Ein Python-Skript fragt GitHub ab und zeigt geeignete Jobs an. Die geplante Ausführung sieht höchstens eine Sitzung vor.
Ein lokales LLM, kostenpflichtige API-Schlüssel und ein externer Scheduler sind nicht erforderlich.
Es gibt höchstens einen Worker pro Runner; der nächste geeignete Anbieter kann eine andere Aufgabe
übernehmen, während ein Anbieter pausiert. Laufende Arbeit wechselt nicht automatisch den Anbieter.

Die Benutzerentscheidung vom 2026-11-17 erlaubt diese Automation für Programmierung mit Medium.
Neue Forschungsideen, Reasoning-Aufträge und kritische Aufgaben bleiben manuell. Der Runner führt
keine Merges aus. Branch-Schutz und unabhängige Reviews bleiben erforderlich.

GitHub-Labels sind die Freigabe durch den Benutzer, nicht durch den Worker:

| Ziel | Erforderliche Labels |
|---|---|
| Neues Coding-Issue | `auto-ready`, `effort:medium`, `work:coding` |
| Bestehenden PR automatisch prüfen / eigene Befunde beheben | `auto-ready`, `effort:medium` |
| Anbieter optional festlegen | zusätzlich `agent:codex` oder `agent:claude` |
| Automatik untersagen | `manual-only` oder `blocked` |

Bei Issues müssen Task-ID, Scope, Abnahme und Abhängigkeiten vorher feststehen. Der Benutzer setzt
`auto-ready` erst nach Auflösung der Abhängigkeiten. Entfernen des Labels verhindert den nächsten
Start, beendet aber keinen bereits laufenden Worker. Worker dürfen sich keine neue Freigabe erteilen.

`needs-independent-review`, `effort:high`, `research` sperren die automatische Bearbeitung. Zusätzlich
sind T-005, T-006, T-008 und T-011 bis T-016 im Code gesperrt, unabhängig von Labels. Neue kritische
Aufgaben müssen ebenfalls `manual-only` bekommen; das Skript kann fachliche Risiken nicht vollständig
aus Freitext erkennen. Keine automatische Herabstufung auf Medium.

Priorität: eigene aktuelle Review-Befunde → fremde PRs ohne Review dieses Agenten am aktuellen
Head → freie Coding-Issues. Unklare Agentenherkunft, mehrere Task-IDs, Drafts und Fork-PRs werden
übersprungen. Herkunft kommt aus dem Agent-Feld, nicht dem gemeinsam verwendeten GitHub-Login.

## Einrichtung auf dem Mac

1. Python ≥3.12, Git und GitHub CLI installieren. Die Planung benötigt einen GitHub-Lesezugang.
2. Einen sauberen separaten Clone ohne `.env`, Broker-Zustände oder Zugangsdaten verwenden.
3. Gepinnte Abhängigkeiten installieren und `make check` ausführen. `doctor` prüft die Planungsvoraussetzungen.

```bash
python3.12 scripts/coding_runner.py doctor
python3.12 scripts/coding_runner.py once --dry-run
python3.12 scripts/coding_runner.py status
```

## Voraussetzungen für eine spätere Ausführung

Ein getrennter Ausführungsbereich muss beliebigen Worker-Code von Benutzerverzeichnis, Host-Dateien,
Git-/GitHub-Identität und Brokergeheimnissen isolieren. Der vertrauenswürdige Steuerprozess muss
veröffentlichbare Änderungen und GitHub-Mutationen separat kontrollieren. Worker erhalten keine
GitHub-Schreibidentität und keine Merge-, Force-Push- oder Löschbefugnis. Die künftige GitHub-Identität
des Steuerprozesses und der Branch-Schutz müssen diese Trennung ergänzen. Ein separater Clone und
Provider-Toolfilter allein bieten diese Isolation nicht. Der erste echte Test erfordert eine geprüfte
Implementierung und ausdrückliche Freigabe. Aktuell fehlt dieser Ausführungsbereich.

## Usage und Kosten

Es wird keine verlässliche gemeinsame Restkontingent-API vorausgesetzt. Erkannte CLI-Limitfehler
pausieren nur den betreffenden Anbieter dauerhaft im gespeicherten Zustand bis zum Ablauf des
konfigurierten Cooldowns. Das gilt auch, wenn die CLI mit Exitcode 0 eine strukturierte Fehlermeldung
liefert. Es gibt keine automatischen API-Aufrufe und keinen Modell-Fallback. Ein früherer tatsächlicher
Reset bleibt bis zum nächsten Versuch ungenutzt; ein späterer Reset führt erneut zu einer Pause.

Das Skript kann weder Verbrauch vorhersagen noch Abrechnungsoptionen des Kontos abschalten.
Zusätzliche Usage-Credits/Extra Usage müssen in den Anbieter-Konten deaktiviert sein, wenn ausschließlich
das Abo verbraucht werden darf. Auch gespeicherte Provider-/Gateway-Konfigurationen müssen geprüft
werden. Subscription-Login allein beweist keine kostenlose weitere Nutzung.

## Zustand, Sicherheit und Fortsetzung

`.coding-runner/` ist lokal und ignoriert. Für die geplante Ausführung gibt es atomar geschriebenen Zustand, Prozesssperre und einen eigenen
Git-Worktree je Arbeitslauf. Keine Broker-Zustände werden gemeinsam genutzt; kein .env wird kopiert.
Bekannte Broker-/API-Key-Umgebungsvariablen werden entfernt. Der Runner ist kein vollständiger
Secret-Sandbox: Coding-Werkzeuge können Code ausführen. Daher keine Live-Secrets, produktiven
Brokerprozesse oder untrusted Forks in der Runner-Umgebung bereitstellen. Die Live-Sperre des Projekts
bleibt maßgeblich; ein Prompt ersetzt keine technische Broker-Sperre.

Der erste serverseitig gespeicherte Claim-Kommentar gewinnt auch bei konkurrierenden Rechnern.
Claims verfallen nie allein wegen Zeitablaufs. Ein zweiter Anbieter darf nicht denselben Issue-Job
übernehmen. Eine manuelle ältere Übernahme wird respektiert. Claim-Kommentare nach einem Verlust
des lokalen Zustands nicht einfach löschen: erst laufende Prozesse, Branch und Handoff abgleichen.

Das Skript loggt nur Job-ID, Anbieter und Status, niemals rohe Agenten-/Werkzeugausgaben. Diese werden
temporär mit begrenzter Größe gehalten. Dauer und Ausgabe sind begrenzt; bei Abbruch wird die gesamte
Worker-Prozessgruppe beendet. Uncommittierte Arbeit bleibt erhalten. Ein SIGKILL des Runners kann
Worker hinterlassen; nach erkanntem unklaren Neustart werden beide Anbieter vorsorglich pausiert.

```bash
python3.12 scripts/coding_runner.py status
# Job-ID aus status; erst Prozesse und Worktree prüfen:
python3.12 scripts/coding_runner.py retry JOB_ID
python3.12 scripts/coding_runner.py resume-provider codex
python3.12 scripts/coding_runner.py resume-provider claude
```

Eine Limitpause wird ohne diese Befehle automatisch nach dem Cooldown erneut versucht. Ein Absturz,
Berechtigungsproblem, fehlgeschlagenes Check oder eine unklare Fertigmeldung benötigt bewusste
Wiederfreigabe. Hat sich ein PR-Head inzwischen geändert, wird neu geplant; eine alte Fix-Sitzung darf
nicht blind wiederholt werden. Eine Aufgabe mit vorhandenem, nicht vom Runner verwaltetem lokalen
Branch blockiert statt diesen Branch zurückzusetzen. Manuell bearbeiten oder nach Abstimmung bereinigen.
`status` liest auch bei aktivem Watch-Prozess atomar den Zustand. Für `retry`/`resume-provider`
zuerst den Watch-Prozess stoppen; diese Befehle schreiben den Zustand unter derselben Prozesssperre.

Eine Fertigmeldung reicht nicht: Der Runner prüft einen sauberen Worktree, führt `make check` selbst
aus und prüft das tatsächliche PR-/Review-Artefakt. Implementierungen müssen gepusht und CI-Prüfungen
erfolgreich sein; ausstehende CI wird als Blockade dokumentiert und kann später explizit erneut geprüft
werden. Ein Review mit „Änderungen nötig“ ist ein erledigter Reviewauftrag, keine Merge-Freigabe.

Eine mögliche spätere Ausführung kann zwischen Jobs über `.coding-runner/STOP` pausieren. Die Datei hebt die aktuelle Ausführungssperre nicht auf.
`Ctrl+C` beendet auch den aktiven Worker; Arbeit danach prüfen. Nach einem harten Stopp darf der
Benutzer erst nach Prüfung der Prozesse `resume-provider` ausführen.

## Nachweise und Grenzen

Offline-Tests verwenden Fake-GitHub und lokale Fake-Worker: Aufgabenfilter, Agentenunabhängigkeit,
Claim-Rennen, persistente Limitpausen, Quarantäne nach Absturz, parallele Prozesssperren, reine
Vorschau, strukturierte Fehler und Prozess-/Ausgabegrenzen. Ein Test für echte Worker folgt frühestens nach Implementierung und Prüfung der Rechteisolation. Die vorhandenen Tests und CI bleiben
unverändert verpflichtend; fehlende Werkzeuge werden nicht durch Erfolgsmeldungen ersetzt.

CLI-Referenzen (geprüft 2026-11-17): [Codex non-interactive](https://developers.openai.com/codex/noninteractive),
[Codex CLI](https://developers.openai.com/codex/cli/reference),
[Claude headless](https://code.claude.com/docs/en/headless),
[Claude CLI](https://code.claude.com/docs/en/cli-reference).
