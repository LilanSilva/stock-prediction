# Market Data Service image, built from the org-approved official python:3.12-slim base.
# Build context is the repository root (see infra/docker-compose.yml) so both the shared library and
# the market-data package can be installed from source.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# 1) Install pinned, hash-verified third-party dependencies first for better layer caching.
COPY requirements.txt ./
RUN pip install --require-hashes -r requirements.txt

# 2) Install the local packages (their third-party deps are already installed above).
COPY src/shared ./src/shared
COPY src/services/market-data ./src/services/market-data
RUN pip install --no-deps ./src/shared ./src/services/market-data

# 3) Run as an unprivileged user.
RUN useradd --create-home --uid 10001 appuser
USER appuser

EXPOSE 8000
CMD ["uvicorn", "market_data.app:app", "--host", "0.0.0.0", "--port", "8000"]
