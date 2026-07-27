# Cleansing Service image — FULL-SPEC ML variant.
#
# Adds the heavy multilingual backends the functional document specifies:
#   - BGE-m3 embeddings (sentence-transformers + torch, CPU) for cross-language Gate-1 similarity;
#   - spaCy en_core_web_sm / sv_core_news_sm for actor/action extraction (Gate 2 + LLM triggering).
#
# These are installed on top of the hash-pinned core requirements (they are large, optional, and
# carry a big transitive tree incl. torch, so they are intentionally NOT in the pinned lockfile).
# The models are pre-downloaded at build time so the first request is fast and the container needs
# no network at runtime. Expect a multi-GB image and a slow first build.
#
# Run with CLEANSING_EMBEDDING_BACKEND=bge-m3 and CLEANSING_NLP_BACKEND=spacy (set in compose).
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/opt/hf-cache

WORKDIR /app

# 1) Pinned, hash-verified core dependencies first (best layer caching).
COPY requirements.txt ./
RUN pip install --require-hashes -r requirements.txt

# 2) Heavy optional ML backends (unpinned within version ranges; pulls torch CPU).
RUN pip install "sentence-transformers>=3,<4" "spacy>=3.7,<4"

# 3) spaCy language models (installed as wheels into site-packages, available to all users).
RUN python -m spacy download en_core_web_sm \
    && python -m spacy download sv_core_news_sm

# 4) Pre-download the BGE-m3 weights into the image cache so startup is fast and offline-capable.
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('BAAI/bge-m3')"

# 5) Install the local packages (their third-party deps are already installed above).
COPY src/shared ./src/shared
COPY src/services/cleansing ./src/services/cleansing
RUN pip install --no-deps ./src/shared ./src/services/cleansing

# 6) Run as an unprivileged user; give it ownership of the model cache.
RUN useradd --create-home --uid 10001 appuser \
    && chown -R appuser /opt/hf-cache
USER appuser

EXPOSE 8000
CMD ["uvicorn", "cleansing.app:app", "--host", "0.0.0.0", "--port", "8000"]
