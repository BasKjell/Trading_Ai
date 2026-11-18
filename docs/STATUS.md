# Status

T-001–T-004: integriert (PR #17). Diese Angabe wird im Integrationscommit vorbereitet und gilt nach dessen
Merge. Enthalten: localtrader-Basis, korrigierte Kosten/Risikolimits, 15-Minuten-Import und kausale
Intraday-Engine. Geprüfte Codebasis `1dfb421`; 105 Tests im unabhängigen lokalen Review bestanden, CI am
Codehead grün. Kostenannahmen und kurze DAX-Historie begrenzen wirtschaftliche Aussagen. T-005, T-006 und
T-008 weiterhin geplant; Live-Trading gesperrt.

T-011 (nur Datenteil): EUR/USD-Referenzkurse der EZB integriert (PR #25). Gilt nach dessen Merge.
Enthalten: `fetch-fx`, kausale Abfrage `FxSeries.rate_at` mit Kursalter-Grenze, Holdout-Schnitt.
Buchhaltung in EUR (Umrechnung von Fills, Bewertung, Dividenden, Kosten) weiterhin geplant (T-011).
