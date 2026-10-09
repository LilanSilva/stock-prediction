ARG PYTHON_BASE_IMAGE=python:3.12-slim
FROM ${PYTHON_BASE_IMAGE}

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app
COPY requirements.txt ./
RUN pip install --require-hashes -r requirements.txt

# Copy only package sources; the extension, runtime files and secrets stay on the host.
COPY src/shared/pyproject.toml ./src/shared/pyproject.toml
COPY src/shared/shared ./src/shared/shared
COPY src/services/llm-browser-gateway/pyproject.toml ./src/services/llm-browser-gateway/pyproject.toml
COPY src/services/llm-browser-gateway/llm_browser_gateway ./src/services/llm-browser-gateway/llm_browser_gateway
RUN pip install --no-deps ./src/shared ./src/services/llm-browser-gateway

RUN useradd --create-home --uid 10001 appuser
USER appuser
EXPOSE 8091
CMD ["python", "-m", "llm_browser_gateway"]
