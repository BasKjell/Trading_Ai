FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

WORKDIR /app
COPY pyproject.toml ./
COPY localtrader ./localtrader
COPY config ./config
COPY strategy_specs ./strategy_specs
COPY tests ./tests

RUN useradd --create-home --uid 10001 trader && \
    mkdir -p /app/reports /app/state /app/research_registry && \
    chown -R trader:trader /app

USER trader
CMD ["python", "-m", "localtrader", "backtest", "--days", "1000", "--output", "reports/docker"]
