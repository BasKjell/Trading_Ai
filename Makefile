.PHONY: test backtest lab-bootstrap lab-search lab-deep lab-continuous lab-status dashboard

test:
	.venv/bin/python -m pytest

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
