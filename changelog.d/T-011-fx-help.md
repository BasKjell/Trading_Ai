## 2026-11-18 – fetch-fx: Hilfetext für --end (Issue #33)

- `fetch-fx --help` nennt als Standard für `--end` den letzten Tag vor dem wirksamen Cutoff (Holdout-Start
  oder `--available-until`) statt „today“. Verhalten unverändert.

**Tests:** `tests/test_fx_rates.py` (Hilfetext).
