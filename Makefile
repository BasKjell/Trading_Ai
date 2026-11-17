.PHONY: link-check intraday intraday-stress check format-check lint typecheck secret-scan config-check test test-cov backtest lab-bootstrap lab-search lab-deep lab-continuous lab-status dashboard

PY ?= python3

# Single entry point for all mandatory checks; exits non-zero on any failure.
check: format-check lint typecheck test secret-scan link-check config-check
	@echo "make check: all checks passed"

format-check:
	ruff format --diff localtrader tests scripts

lint:
	ruff check localtrader tests scripts

typecheck:
	mypy

test:
	$(PY) -m pytest

test-cov:
	$(PY) -m coverage run -m pytest --junitxml=reports/junit.xml
	$(PY) -m coverage xml -o reports/coverage.xml
	$(PY) -m coverage report

link-check:
	$(PY) scripts/check_links.py

secret-scan:
	$(PY) scripts/secret_scan.py

config-check:
	$(PY) -c "from localtrader.config import load_config; c = load_config(); print('config ok', c.digest[:12])"

intraday:
	$(PY) -m localtrader intraday-backtest --strategies intraday_trend --output reports/intraday_trend
	$(PY) -m localtrader intraday-backtest --strategies mean_reversion --output reports/mean_reversion

intraday-stress:
	$(PY) -m localtrader intraday-stress --strategies intraday_trend --output reports/stress_trend
	$(PY) -m localtrader intraday-stress --strategies mean_reversion --output reports/stress_mean_reversion

backtest:
	python3 -m localtrader backtest --days 1000 --output reports/baseline

lab-bootstrap:
	python3 -m localtrader evaluate-strategies --days 900

lab-search:
	python3 -m localtrader ollama-search --model qwen3:8b --iterations 3 --days 900

lab-deep:
	python3 -m localtrader ollama-search --model qwen3-coder:30b --iterations 10 --days 900

lab-continuous:
	python3 -m localtrader ollama-search --model qwen3-coder:30b --continuous --auto-data --lookback-days 900

lab-status:
	python3 -m localtrader lab-status

dashboard:
	python3 -m localtrader dashboard
