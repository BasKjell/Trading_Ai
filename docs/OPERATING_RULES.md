# Verbindliche Betriebsregeln (Spec §18.7, T-019)

Die Regeln stehen maschinenlesbar in `[operating_rules]` (`config/default.toml`) und werden beim Start
von `localtrader/operating_rules.py` geprüft: Fehlende, unbekannte, falsch geschriebene oder
abgeschwächte Werte verhindern den Start. **Zwei Regeln sind nicht konfigurierbar:** Fremde Positionen
werden nie automatisch liquidiert, und Livebetrieb verlangt ein ausschließlich diesem Bot zugeordnetes
Konto.

Status je Regel: **implementiert (Backtest)** = in der Intraday-Engine durchgesetzt und getestet
(`tests/test_operating_rules.py`); **implementiert (Daten)** = im Datenabruf durchgesetzt;
**geplant** = gilt für Paper-/Livebetrieb, Durchsetzung folgt mit T-005/T-006/T-014. Livebetrieb bleibt
technisch gesperrt.

| Thema | Regel (`[operating_rules]`) | Bedeutung | Status |
|---|---|---|---|
| Handelszeiten | `trading_hours = regular_session_only` | Einstiege nur in der regulären Sitzung des Handelsplatzes (SPY/QQQ: 09:30–16:00 New York, an NYSE-Halbtagen bis 13:00). Keine vor- oder nachbörslichen Orders. | implementiert (Backtest): Einstieg auf einer Bar außerhalb der Sitzung wird verworfen |
| Vor Handelsschluss | `before_close = no_new_entries_in_last_bar` | Ein Signal aus der letzten Bar würde erst am nächsten Morgen ausgeführt; es verfällt. Offene Positionen werden nicht zwangsweise geschlossen. Alternative `flatten_all` (alles vor Schluss schließen) ist vorgesehen, aber nicht umgesetzt. | implementiert (Backtest) für `no_new_entries_in_last_bar` (Signal verfällt als veraltet); `flatten_all`: geplant |
| Übernacht | `overnight_positions = allowed` | Positionen dürfen über Nacht gehalten werden; Schutzstop bleibt aktiv. Kurslücken können den Stop überspringen (Ausführung zu `min(Open, Stop)`). | implementiert (Backtest) |
| Wochenende | `weekend_positions = allowed` | Wie über Nacht. Nicht erlaubt, wenn Übernacht verboten ist (Prüfung beim Start). | implementiert (Backtest) |
| Handelsunterbrechung | `trading_halt = no_orders_while_halted` | Während einer Unterbrechung keine Orders; ausstehende Einstiege verfallen; Schutzstops bleiben beim Broker. | Backtest: Datenlücke sperrt neue Einstiege (implementiert); Erkennung echter Halts beim Broker: geplant (T-006/T-014) |
| Dividenden | `dividends = entitlement_at_ex_date_cash_at_payment` | Am Ex-Tag Forderung (zählt zum Kontowert, nicht investierbar), Cash am Zahltag, abzüglich Quellensteuer. | implementiert (Backtest) in PR #21 (in Prüfung) |
| Splits | `splits = block_symbol_until_reviewed` | Ein Split sperrt das Instrument, bis Daten und Positionen manuell geprüft sind. | implementiert (Daten) in PR #21: Abruf bricht bei einem Split ab; Sperre im Betrieb: geplant |
| Delisting | `delisting = block_new_entries_and_alert` | Keine neuen Einstiege, Alarm, kontrollierter Abbau nach manueller Entscheidung. | geplant (T-014) |
| Fremde Positionen | `foreign_positions = never_liquidate` (fest) | Positionen, die der Bot nicht eröffnet hat, werden nie automatisch verkauft, verändert oder abgesichert; sie zählen beim Risiko als belegtes Kapital. | Regel fest in der Konfiguration; Durchsetzung beim Brokerabgleich: geplant (T-005) |
| Manuelle Orders | `manual_orders = never_touch_and_reconcile_as_foreign` | Manuell erteilte Orders werden nie storniert oder geändert und beim Abgleich als fremd geführt. | geplant (T-005) |
| Konto | `dedicated_account_required_for_live = true` (fest) | Livebetrieb nur mit einem ausschließlich diesem Bot zugeordneten Konto. | Regel fest; Prüfung im Live-Preflight: geplant (T-012/T-016) |

## Begründungen

- **Keine Zwangsschließung vor Handelsschluss:** Die Trendfolge hält im Mittel rund 27 Stunden
  (EXP-T018); eine Schließung zum Sitzungsende würde die Strategie ändern und Kosten verdoppeln.
  Übernacht-Risiko wird über Risikobudget, Schutzstop und Instrumentgrenzen begrenzt.
- **Signal aus der letzten Bar verfällt:** Eine Ausführung erst am Folgetag würde auf veralteter
  Information beruhen und Eröffnungslücken ungeprüft übernehmen.
- **Fremde Positionen nie liquidieren:** Der Bot kann ihre Absicht nicht kennen; ein automatischer
  Verkauf könnte Verluste oder Steuerfolgen auslösen (Spec §18.7).
