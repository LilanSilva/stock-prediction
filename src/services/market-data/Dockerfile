# Market Data Service image, built from the org-approved official python:3.12-slim base.
# Build context is the repository root (see infra/docker-compose.yml) so both the shared library and
# the market-data package can be installed from source.
ARG PYTHON_BASE_IMAGE=python:3.12-slim
FROM ${PYTHON_BASE_IMAGE}

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PLAYWRIGHT_BROWSERS_PATH=/opt/playwright \
    NODE_EXTRA_CA_CERTS=/etc/ssl/certs/ca-certificates.crt

WORKDIR /app

# 1) Install pinned, hash-verified third-party dependencies first for better layer caching.
COPY requirements.txt ./
COPY src/services/market-data/requirements-snapshots.txt ./requirements-snapshots.txt
RUN pip install --timeout 15 --retries 1 --require-hashes -r requirements.txt \
    && pip install --timeout 15 --retries 1 --require-hashes -r requirements-snapshots.txt
RUN python -m playwright install --with-deps chromium
RUN apt-get update && apt-get install -y --no-install-recommends libnss3-tools

# 2) Install the local packages (their third-party deps are already installed above).
COPY src/shared ./src/shared
COPY src/services/market-data ./src/services/market-data
RUN pip install --no-deps ./src/shared ./src/services/market-data

# 3) Run as an unprivileged user.
RUN useradd --create-home --uid 10001 appuser \
    && chmod -R a+rX /opt/playwright
USER appuser
RUN mkdir -p /home/appuser/.pki/nssdb \
    && certutil -N --empty-password -d sql:/home/appuser/.pki/nssdb \
    && for cert in /usr/local/share/ca-certificates/*.crt; do \
         if [ -f "$cert" ]; then \
           certutil -A -d sql:/home/appuser/.pki/nssdb -t "C,," -n "$(basename "$cert")" -i "$cert"; \
         fi; \
       done

EXPOSE 8000
CMD ["uvicorn", "market_data.app:app", "--host", "0.0.0.0", "--port", "8000"]
