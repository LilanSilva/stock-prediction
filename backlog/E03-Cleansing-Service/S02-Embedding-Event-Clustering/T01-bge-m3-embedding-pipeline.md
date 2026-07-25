# T01: BGE-m3 embedding pipeline

## Context

This task implements the embedding stage of the Cleansing Service. After an article has passed SimHash deduplication (S01/T01) and its action signature has been extracted (S01/T02), it is embedded using the BAAI/bge-m3 multilingual model. The resulting 1024-dimensional vector is stored in Postgres using the pgvector extension, where it enables fast approximate nearest-neighbour search for clustering (S02/T02).

BGE-m3 is a multilingual embedding model that handles both English and Swedish news articles in the same vector space, meaning cross-language similarity is meaningful. This is essential because GDELT and Swedish RSS feeds publish in different languages about the same geopolitical events.

## Background

**BAAI/bge-m3:** A state-of-the-art multilingual text embedding model from the Beijing Academy of Artificial Intelligence. Produces 1024-dimensional dense vectors. Loaded via `sentence-transformers` library. Supports 100+ languages including Swedish.

**pgvector:** A Postgres extension that adds a `vector` column type and vector operations including `<=>` (cosine distance), `<->` (L2 distance), and `<#>` (inner product). We use cosine distance: `1 - cosine_similarity`. A pgvector index (`ivfflat` or `hnsw`) enables sub-millisecond ANN search over tens of thousands of vectors.

**Embedding input:** `title + " " + body[:500]` — the title carries the most signal; the first 500 chars of body add named-entity context without exceeding BGE-m3's effective context window (~512 tokens).

**Batch processing:** The model processes texts most efficiently in batches. At startup the service processes a backlog; during steady state articles arrive one at a time. Always use `model.encode(texts, batch_size=32)` even for a single article (a list of one).

**Postgres table:** `article_embeddings` in the `events` database. Uses `pgvector`'s `vector(1024)` column type.

## Inputs

- **In-memory:** `ArticleIngested` Pydantic object (after dedup and action signature steps)
  - Fields used: `article_id` (str), `title` (str), `body` (str)
- **Model loaded at startup:** `BAAI/bge-m3` via `sentence_transformers.SentenceTransformer`

## Outputs

- **Postgres table `article_embeddings`** (write): one row per embedded article containing the 1024-dim vector
- **In-memory return value:** `list[float]` of length 1024 — the embedding vector, passed to S02/T02 for cluster assignment

## Technical Requirements

### Python Libraries
- `sentence-transformers==3.*` — `from sentence_transformers import SentenceTransformer`
- `pgvector==0.3.*` — `from pgvector.asyncpg import register_vector` (pgvector asyncpg adapter)
- `numpy==1.*` — vector handling
- `asyncpg` — async Postgres driver

### Postgres Setup

Ensure pgvector extension is enabled (run once, in `infra/postgres/init.sql` or migration `003_enable_pgvector.sql`):

```sql
CREATE EXTENSION IF NOT EXISTS vector;
```

Create table `article_embeddings`:

```sql
CREATE TABLE IF NOT EXISTS article_embeddings (
    id          BIGSERIAL PRIMARY KEY,
    article_id  TEXT        NOT NULL UNIQUE,
    embedding   vector(1024) NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- HNSW index for fast cosine ANN search
CREATE INDEX IF NOT EXISTS idx_embeddings_hnsw
    ON article_embeddings
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);
```

### Model Loading

```python
# services/cleansing/embedding/model.py
from sentence_transformers import SentenceTransformer
import numpy as np

_model: SentenceTransformer | None = None

def load_embedding_model() -> None:
    global _model
    _model = SentenceTransformer("BAAI/bge-m3")
    # Warm-up: encode a dummy sentence to trigger JIT compilation
    _model.encode(["warmup"], batch_size=1)

def get_model() -> SentenceTransformer:
    if _model is None:
        raise RuntimeError("Embedding model not loaded — call load_embedding_model() at startup")
    return _model
```

### Embedding Function

```python
import asyncio
from sentence_transformers import SentenceTransformer
import numpy as np

async def embed_article(title: str, body: str) -> list[float]:
    text = f"{title} {body[:500]}"
    model = get_model()
    # Run sync encode in thread pool to avoid blocking event loop
    loop = asyncio.get_event_loop()
    embedding: np.ndarray = await loop.run_in_executor(
        None,
        lambda: model.encode([text], batch_size=1, normalize_embeddings=True)[0]
    )
    return embedding.tolist()
```

`normalize_embeddings=True` ensures unit-norm vectors, which makes cosine similarity equivalent to dot product — critical for the `<=>` pgvector operator.

### Storing Embeddings

```python
# Use pgvector asyncpg adapter
from pgvector.asyncpg import register_vector
import asyncpg

async def store_embedding(conn: asyncpg.Connection, article_id: str, embedding: list[float]) -> None:
    await register_vector(conn)
    await conn.execute(
        """
        INSERT INTO article_embeddings (article_id, embedding)
        VALUES ($1, $2)
        ON CONFLICT (article_id) DO NOTHING
        """,
        article_id,
        embedding,
    )
```

### File Layout

```
services/cleansing/
  embedding/
    __init__.py
    model.py         # load_embedding_model(), get_model()
    embed.py         # embed_article(), store_embedding()
  db/
    migrations/
      003_enable_pgvector.sql
      004_create_embeddings.sql
```

### Docker Configuration

BGE-m3 model weights (~2.3 GB) must be available in the container. Options:
1. **Pre-download in Docker image** (recommended for production): `RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('BAAI/bge-m3')"`
2. **Mount HuggingFace cache volume**: set `TRANSFORMERS_CACHE=/cache` and mount a persistent volume

Set environment variable in `docker-compose.yml`: `TRANSFORMERS_CACHE: /app/.cache/huggingface`

### Memory Requirements

BGE-m3 loaded in memory: ~900 MB (float32). The container must have at least 1.5 GB RAM. Set `mem_limit: 2g` in `docker-compose.yml` for the cleansing service.

## Acceptance Criteria

1. `embed_article("Iran launches missile strike", "Iran fired ballistic missiles...")` returns a `list[float]` of exactly length 1024.
2. All returned vector values are in range `[-1.0, 1.0]` and the L2 norm of the vector is approximately 1.0 (normalized).
3. Two semantically similar titles produce cosine similarity >= 0.80 when computed as `1 - (sum(a_i*b_i for ...) / ...)` over their embeddings.
4. Two semantically unrelated titles (e.g., "Iran missile strike" vs "Swedish housing prices rise") produce cosine similarity < 0.60.
5. The model is loaded exactly once — subsequent calls to `embed_article()` do not reload the model from disk.
6. `store_embedding()` writes one row to `article_embeddings`; re-inserting the same `article_id` does not raise an exception.
7. The HNSW index exists on `article_embeddings.embedding` after migration runs.
8. `embed_article()` does not block the asyncio event loop (uses `run_in_executor`).
9. Unit tests in `services/cleansing/tests/test_embedding.py` cover: vector length, normalization, storage mock, and idempotent insert.

## Implementation Notes

- **Model cold start:** BGE-m3 takes 5-15 seconds to load on CPU. This is acceptable at service startup. Add a health-check endpoint that returns HTTP 503 until the model is loaded. In `docker-compose.yml`, set `healthcheck` accordingly.
- **CPU vs GPU:** The service runs on CPU by default. BGE-m3 on CPU embeds ~10 articles/second — more than sufficient for hourly ingestion batches of ~50-200 articles. If GPU is available, `sentence-transformers` will use it automatically via `torch`.
- **Float precision:** Store as `vector(1024)` (float32 in pgvector). Do NOT use float64 — pgvector uses float32 internally and the extra precision has no practical benefit.
- **normalize_embeddings=True:** This is critical. Without normalization, cosine similarity via pgvector's `<=>` operator gives incorrect results. Always normalize at encoding time.
- **Batch size of 32:** For the steady-state single-article path, `batch_size=1` is fine. The `batch_size=32` parameter in `model.encode()` is relevant only if processing a backlog of many articles at once (e.g., after a service restart).
- **pgvector adapter:** The `pgvector` Python package provides an `asyncpg` codec that converts Python lists/numpy arrays to and from the `vector` Postgres type. Always call `await register_vector(conn)` on each new connection before using vector columns.
- **HuggingFace download:** If the model is not pre-downloaded and HuggingFace is unreachable, the service fails at startup. Log a clear error message: "Failed to load BAAI/bge-m3 — ensure model is pre-downloaded or HuggingFace Hub is accessible".

## Definition of Done

- [ ] Unit tests pass (`pytest services/cleansing/tests/test_embedding.py`)
- [ ] Code passes `ruff check services/cleansing/embedding/` with zero errors
- [ ] Code passes `mypy services/cleansing/embedding/model.py services/cleansing/embedding/embed.py` with no type errors
- [ ] pgvector extension enabled in Postgres via migration `003_enable_pgvector.sql`
- [ ] `article_embeddings` table with HNSW index created via migration `004_create_embeddings.sql`
- [ ] Model loaded once at startup; health-check returns 200 only after model is ready
- [ ] `normalize_embeddings=True` used in all `model.encode()` calls
- [ ] `run_in_executor` used to avoid blocking event loop
- [ ] Docker image pre-downloads model weights (no runtime HuggingFace fetch)
- [ ] `docker-compose.yml` sets `mem_limit: 2g` for the cleansing service
