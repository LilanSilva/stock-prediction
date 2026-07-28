# T02: Conservative dual-gate incremental clustering

## Context

This task implements the clustering logic that groups related articles into candidate events within the Cleansing Service. It is called for each article after embedding (S02/T01). Given an article's 1024-dim embedding and its action signature (from S01/T02), it decides: does this article belong to an existing open cluster, or should it start a new one?

This is the most architecturally critical component in the entire Cleansing Service. Getting the thresholds wrong in either direction causes downstream prediction failures: over-merging creates fabricated multi-source events; under-merging misses the multi-source confirmation signal that the Prediction Service uses to judge event credibility.

## Background

**Dual-gate clustering:** Two independent conditions must BOTH be true to merge an article into an existing cluster:

- **Gate 1 (semantic similarity):** Cosine similarity between the new article's embedding and the cluster centroid embedding >= 0.80. This gate catches articles about the same event even if worded differently.
- **Gate 2 (action compatibility):** The new article's action verb lemma must be identical to, or a WordNet synonym of, the cluster's action verb lemma. This gate prevents merging causally distinct events that happen to be semantically related (e.g., "Iran launches strike" and "Iran closes strait" are both about Iran and both conflict-related, but represent different market-moving actions).

**Why 0.80 threshold:** Tested empirically — BGE-m3 cosine similarity for same-event articles (different wordings of the same story) typically falls in [0.82, 0.97]. Different-event articles about related topics (same actor, different action) typically fall in [0.60, 0.78]. The 0.80 threshold sits in the gap.

**Cluster lifecycle:**
- **Open:** accepting new articles (created within last 24 hours, or last article was within 24 hours)
- **Closed:** no new articles in 24 hours — eligible for LLM merge (T03)
- **Ready:** has >= 2 articles OR has been open for >= 30 minutes with any articles — trigger LLM merge

**Cluster centroid:** The cluster's representative embedding is the mean of all member article embeddings, recomputed after each new article is added.

**Postgres tables used:**
- `article_embeddings` — stored embeddings (from T01)
- `article_action_signatures` — stored action signatures (from S01/T02)
- `event_clusters` — cluster metadata (centroid, status, timestamps)
- `cluster_articles` — membership join table

## Inputs

- **In-memory:** article's embedding (`list[float]`, length 1024) and `ActionSignature` (from S01/T02)
- **In-memory:** `article_id` (str), `correlation_id` (str)
- **Postgres `event_clusters`:** existing open clusters with their centroid embeddings and representative action verbs
- **Postgres `cluster_articles`:** membership records for computing updated centroids

## Outputs

- **Postgres `event_clusters`** (write/update): new cluster created, or existing cluster's centroid updated
- **Postgres `cluster_articles`** (write): new membership row linking article to cluster
- **Postgres `article_action_signatures`** (update): `cluster_id` column filled with assigned cluster UUID
- **In-memory return:** `cluster_id` (UUID) of the assigned cluster — passed to T03 to check if cluster is ready for merge

## Technical Requirements

### Python Libraries
- `pgvector==0.3.*` — cosine similarity via `<=>` operator in SQL
- `nltk==3.*` with WordNet (`nltk.corpus.wordnet`) — verb synonym check for Gate 2
- `asyncpg` — async Postgres driver
- `uuid` (stdlib) — cluster ID generation
- `numpy` — centroid mean computation

### Postgres Table Schemas

```sql
-- Migration 005_create_event_clusters.sql
CREATE TABLE IF NOT EXISTS event_clusters (
    cluster_id      UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    centroid        vector(1024) NOT NULL,
    action_verb     TEXT,        -- representative verb lemma for Gate 2
    article_count   INT         NOT NULL DEFAULT 0,
    first_seen      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_updated    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    status          TEXT        NOT NULL DEFAULT 'open'  -- 'open' | 'ready' | 'closed' | 'merged'
);
CREATE INDEX idx_clusters_status ON event_clusters (status);
CREATE INDEX idx_clusters_last_updated ON event_clusters (last_updated);

-- Separate index for vector search on open clusters only (partial index not supported
-- for vector columns in all pgvector versions — index full table, filter in query)
CREATE INDEX IF NOT EXISTS idx_clusters_centroid_hnsw
    ON event_clusters
    USING hnsw (centroid vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

-- Migration 006_create_cluster_articles.sql
CREATE TABLE IF NOT EXISTS cluster_articles (
    id          BIGSERIAL   PRIMARY KEY,
    cluster_id  UUID        NOT NULL REFERENCES event_clusters(cluster_id),
    article_id  TEXT        NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (cluster_id, article_id)
);
CREATE INDEX idx_cluster_articles_cluster ON cluster_articles (cluster_id);
```

### Clustering Algorithm

```python
import uuid
import numpy as np
from nltk.corpus import wordnet

SIMILARITY_THRESHOLD = 0.80
CLUSTER_WINDOW_HOURS = 24

def are_actions_compatible(verb_a: str | None, verb_b: str | None) -> bool:
    """Gate 2: same lemma OR WordNet synonyms."""
    if verb_a is None or verb_b is None:
        return True  # no action info → skip Gate 2, let Gate 1 decide
    if verb_a == verb_b:
        return True
    synsets_a = set(wordnet.synsets(verb_a, pos=wordnet.VERB))
    synsets_b = set(wordnet.synsets(verb_b, pos=wordnet.VERB))
    return bool(synsets_a & synsets_b)  # non-empty intersection = synonym

async def assign_to_cluster(
    conn: asyncpg.Connection,
    article_id: str,
    embedding: list[float],
    action: ActionSignature,
) -> uuid.UUID:
    # 1. Find nearest open cluster by cosine similarity
    rows = await conn.fetch(
        """
        SELECT cluster_id, action_verb,
               1 - (centroid <=> $1::vector) AS cosine_similarity
        FROM event_clusters
        WHERE status = 'open'
          AND last_updated >= NOW() - INTERVAL '24 hours'
        ORDER BY centroid <=> $1::vector
        LIMIT 5
        """,
        embedding,
    )

    target_cluster_id = None
    for row in rows:
        sim = float(row["cosine_similarity"])
        cluster_verb = row["action_verb"]
        if sim >= SIMILARITY_THRESHOLD and are_actions_compatible(action.action, cluster_verb):
            target_cluster_id = row["cluster_id"]
            break  # take the nearest passing cluster

    if target_cluster_id is None:
        # Open a new cluster
        target_cluster_id = await create_cluster(conn, embedding, action.action)
    else:
        # Update existing cluster centroid and metadata
        await update_cluster_centroid(conn, target_cluster_id, article_id, embedding)

    # Record membership
    await conn.execute(
        """
        INSERT INTO cluster_articles (cluster_id, article_id)
        VALUES ($1, $2)
        ON CONFLICT (cluster_id, article_id) DO NOTHING
        """,
        target_cluster_id, article_id,
    )

    # Update action_signatures with cluster assignment
    await conn.execute(
        "UPDATE article_action_signatures SET cluster_id = $1 WHERE article_id = $2",
        target_cluster_id, article_id,
    )

    # Check if cluster is ready for merge
    await maybe_mark_cluster_ready(conn, target_cluster_id)

    return target_cluster_id
```

### Centroid Update

```python
async def update_cluster_centroid(
    conn: asyncpg.Connection,
    cluster_id: uuid.UUID,
    new_article_id: str,
    new_embedding: list[float],
) -> None:
    # Fetch all current member embeddings
    rows = await conn.fetch(
        """
        SELECT ae.embedding
        FROM cluster_articles ca
        JOIN article_embeddings ae ON ae.article_id = ca.article_id
        WHERE ca.cluster_id = $1
        """,
        cluster_id,
    )
    embeddings = [np.array(row["embedding"]) for row in rows]
    embeddings.append(np.array(new_embedding))
    new_centroid = np.mean(embeddings, axis=0)
    # Re-normalize the centroid
    new_centroid = new_centroid / np.linalg.norm(new_centroid)

    await conn.execute(
        """
        UPDATE event_clusters
        SET centroid = $1::vector,
            article_count = $2,
            last_updated = NOW()
        WHERE cluster_id = $3
        """,
        new_centroid.tolist(), len(embeddings), cluster_id,
    )
```

### Ready Condition

```python
async def maybe_mark_cluster_ready(conn: asyncpg.Connection, cluster_id: uuid.UUID) -> None:
    row = await conn.fetchrow(
        "SELECT article_count, first_seen FROM event_clusters WHERE cluster_id = $1",
        cluster_id,
    )
    if row is None:
        return
    article_count = row["article_count"]
    age_minutes = (datetime.utcnow() - row["first_seen"].replace(tzinfo=None)).total_seconds() / 60

    if article_count >= 2 or age_minutes >= 30:
        await conn.execute(
            "UPDATE event_clusters SET status = 'ready' WHERE cluster_id = $1 AND status = 'open'",
            cluster_id,
        )
```

### File Layout

```
src/services/cleansing/
  clustering/
    __init__.py
    cluster.py      # assign_to_cluster(), create_cluster(), update_cluster_centroid()
    gate2.py        # are_actions_compatible() with WordNet
    ready.py        # maybe_mark_cluster_ready(), background task for 30min timeout
  db/
    migrations/
      005_create_event_clusters.sql
      006_create_cluster_articles.sql
```

### Background Timeout Task

A separate `asyncio` background task runs every 5 minutes and marks as `ready` any `open` clusters that have been open >= 30 minutes (regardless of article count, including single-article clusters):

```python
async def timeout_old_clusters(conn_pool: asyncpg.Pool) -> None:
    async with conn_pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE event_clusters
            SET status = 'ready'
            WHERE status = 'open'
              AND first_seen <= NOW() - INTERVAL '30 minutes'
            """
        )
```

## Acceptance Criteria

1. Two articles about the same event (cosine similarity >= 0.80, same action verb) are assigned to the same `cluster_id` in `cluster_articles`.
2. Two articles with cosine similarity >= 0.80 but different action verbs (not WordNet synonyms) are assigned to different clusters (Gate 2 prevents merge).
3. Two articles with the same action verb but cosine similarity < 0.80 are assigned to different clusters (Gate 1 prevents merge).
4. After assigning a second article to a cluster, the cluster's `article_count` is 2 and `status` becomes `'ready'`.
5. A single-article cluster transitions from `status='open'` to `status='ready'` after 30 minutes (verified by directly calling `maybe_mark_cluster_ready()` with a mocked `first_seen` 31 minutes ago).
6. The background timeout task correctly updates clusters older than 30 minutes to `'ready'` status.
7. `are_actions_compatible("launch", "fire")` returns `True` (both are WordNet verb synonyms for projectile discharge).
8. `are_actions_compatible("launch", "close")` returns `False`.
9. `are_actions_compatible(None, "close")` returns `True` (missing action — Gate 2 skipped).
10. Unit tests in `src/services/cleansing/tests/test_clustering.py` cover all three merge/no-merge scenarios and the ready-condition logic.

## Implementation Notes

- **LIMIT 5 in cosine search:** We retrieve the 5 nearest clusters and check each against Gate 2. This handles the case where the nearest cluster fails Gate 2 but the second-nearest passes both gates. If all 5 fail, open a new cluster.
- **WordNet download:** NLTK WordNet data must be downloaded in the Docker image: `RUN python -c "import nltk; nltk.download('wordnet'); nltk.download('omw-1.4')"`. The `omw-1.4` (Open Multilingual Wordnet) dataset is needed for word lookups.
- **Swedish verb compatibility:** WordNet has limited Swedish coverage. For Swedish articles, the action verb from `sv_core_news_sm` is a Swedish lemma (e.g., "skjuta" = "shoot"). WordNet won't match Swedish-to-English synonyms. Accept this limitation: Swedish articles will only cluster with other Swedish articles via Gate 2. Cross-language clustering still works via Gate 1 (BGE-m3 is multilingual). This is acceptable product behavior.
- **Transaction safety:** `assign_to_cluster()` should run inside a single Postgres transaction to prevent race conditions where two concurrent articles try to create the same cluster. Use `asyncpg` `async with conn.transaction():`.
- **Centroid normalization:** Always re-normalize the centroid after computing the mean. Un-normalized centroids give wrong cosine similarities via the `<=>` operator.
- **Cluster status state machine:** `open` → `ready` → `merged`. Never transition backwards. The `maybe_mark_cluster_ready()` uses `WHERE status = 'open'` to prevent overwriting `'merged'` status.
- **pgvector HNSW vs IVFFlat:** HNSW is preferred for low-latency single-query lookup (our case). IVFFlat requires a training phase with `nlist` tuning. Use HNSW.

## Definition of Done

> Verified against the delivered flat-module implementation; legacy migration `005`/`006` names and
> the NLTK WordNet synonym gate are superseded — Gate 2 uses canonical taxonomy mapping
> (`cleansing/taxonomy.py`), and clustering lives in `cleansing/clustering.py` + `cleansing/repository.py`.

- [x] Unit tests pass (`tests/test_clustering.py`)
- [x] Code passes `ruff check .` with zero errors
- [x] Code passes `mypy cleansing` with no type errors (`cleansing/clustering.py`)
- [x] `cleansing.event_clusters` and `cleansing.cluster_articles` tables created by `apply_schema` (replaces migrations `005`/`006`)
- [x] Both gates enforced: cosine < 0.80 prevents merge; incompatible canonical event type prevents merge (OTHER never merges)
- [x] Cluster centroid maintained as a running mean under a row lock; similarity compared via cosine
- [x] Event-time close sweep marks quiet/lifetime-due clusters ready (time-based; article count never closes — per overrides)
- [x] ~~NLTK WordNet~~ superseded: Swedish/English actions mapped to the canonical taxonomy locally instead
- [x] Cluster assignment runs inside a Postgres transaction (`add_article_to_cluster`, `FOR UPDATE`)
- [x] EN + SV clusters form correctly — live-verified end-to-end (integration tests + replay run)
