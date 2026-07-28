# T02: Action signature extractor

## Context

This task implements Gate 2 of the dual-gate clustering system in the Cleansing Service. After SimHash deduplication (T01) confirms an article is not a near-duplicate, we extract its (actor, action, object) triple from the article title using spaCy. This triple is stored alongside the article and used during clustering (S02/T02) to prevent merging events that are semantically similar but causally distinct.

The problem this solves: BGE-m3 embeddings capture semantic similarity, so "Iran launches missile strike" and "Iran closes Strait of Hormuz" might have cosine similarity >= 0.80 (both are about Iran, both are conflict-related). But they represent different causal events with different market implications. Gate 2 prevents merging them by requiring the action verb lemma to match.

## Background

**Action signature:** A `(actor, action, object)` triple where:
- `actor` = the subject noun phrase (the entity performing the action)
- `action` = the root verb lemma (normalized base form, e.g., "launch" not "launches")
- `object` = the direct object noun phrase (what the action is performed on)

**spaCy dependency parsing:** spaCy's dependency parser labels each token with its syntactic role. The root verb is `token.dep_ == "ROOT"`. The subject NP is the noun phrase whose head is the root verb with `dep_` in `{"nsubj", "nsubjpass"}`. The object NP has `dep_` in `{"dobj", "obj", "pobj"}`.

**Multilingual support:** Articles can be English or Swedish. Load both `en_core_web_sm` (English) and `sv_core_news_sm` (Swedish) at startup. Select the model based on the `language` field of `ArticleIngested` (`"en"` → English model, `"sv"` → Swedish model). For other/unknown languages, default to the English model.

**Verb synonym matching:** During clustering (S02/T02), two action signatures are "compatible" if their action verb lemmas are identical OR are WordNet synonyms (same synset). The synonym check is implemented in S02/T02, not here. This task only extracts and stores the raw verb lemma.

**Storage:** Action signatures are stored in a Postgres table `article_action_signatures`. The `cluster_id` column is initially NULL and is filled in by S02/T02 when the article is assigned to a cluster.

## Inputs

- **In-memory:** `ArticleIngested` Pydantic object (passed from T01 after dedup check passes)
  - Fields used: `article_id` (str), `title` (str), `language` (str — `"en"` or `"sv"`)
- **Loaded at startup:** spaCy models `en_core_web_sm` and `sv_core_news_sm` (kept in memory for the service lifetime)

## Outputs

- **Postgres table `article_action_signatures`** (write): one row per processed article containing the extracted triple
- **In-memory return value:** `ActionSignature` dataclass with fields `actor: str | None`, `action: str | None`, `object: str | None` — passed to S02/T01 for embedding

## Technical Requirements

### Python Libraries
- `spacy==3.7.*` — NLP pipeline
- `en_core_web_sm` — English spaCy model (install via `python -m spacy download en_core_web_sm`)
- `sv_core_news_sm` — Swedish spaCy model (install via `python -m spacy download sv_core_news_sm`)
- `asyncpg` or `psycopg[async]` — Postgres driver (consistent with T01)
- Standard library `dataclasses` — `ActionSignature` dataclass

### Postgres Table Schema

Create table `article_action_signatures` in the `events` Postgres database:

```sql
CREATE TABLE IF NOT EXISTS article_action_signatures (
    id          BIGSERIAL PRIMARY KEY,
    article_id  TEXT        NOT NULL UNIQUE,
    actor       TEXT,
    action      TEXT,
    object      TEXT,
    cluster_id  UUID,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_action_sigs_cluster_id ON article_action_signatures (cluster_id)
    WHERE cluster_id IS NOT NULL;
```

### ActionSignature Dataclass

```python
from dataclasses import dataclass

@dataclass
class ActionSignature:
    actor: str | None   # subject NP text, lowercased
    action: str | None  # root verb lemma, lowercased
    object: str | None  # direct object NP text, lowercased
```

### Extraction Logic

```python
import spacy

_nlp_en = spacy.load("en_core_web_sm")
_nlp_sv = spacy.load("sv_core_news_sm")

def extract_action_signature(title: str, language: str) -> ActionSignature:
    nlp = _nlp_sv if language == "sv" else _nlp_en
    doc = nlp(title)

    root_token = next((t for t in doc if t.dep_ == "ROOT"), None)
    if root_token is None:
        return ActionSignature(actor=None, action=None, object=None)

    action = root_token.lemma_.lower()

    # Subject: noun phrase whose root has nsubj/nsubjpass dep to ROOT
    actor = None
    for chunk in doc.noun_chunks:
        if chunk.root.head == root_token and chunk.root.dep_ in {"nsubj", "nsubjpass"}:
            actor = chunk.text.lower()
            break

    # Object: noun phrase whose root has dobj/obj/pobj dep to ROOT
    obj = None
    for chunk in doc.noun_chunks:
        if chunk.root.head == root_token and chunk.root.dep_ in {"dobj", "obj", "pobj"}:
            obj = chunk.text.lower()
            break

    return ActionSignature(actor=actor, action=action, object=obj)
```

### File Layout

```
src/services/cleansing/
  nlp/
    __init__.py
    action_signature.py   # ActionSignature dataclass, extract_action_signature()
    models.py             # load_spacy_models() called at startup
  db/
    migrations/
      002_create_action_signatures.sql
```

### Model Loading

Load both spaCy models once during service startup (not per-message):

```python
# src/services/cleansing/nlp/models.py
import spacy

_models: dict[str, spacy.language.Language] = {}

def load_spacy_models() -> None:
    _models["en"] = spacy.load("en_core_web_sm")
    _models["sv"] = spacy.load("sv_core_news_sm")

def get_model(language: str) -> spacy.language.Language:
    return _models.get(language, _models["en"])
```

### Idempotency

Insert uses `ON CONFLICT (article_id) DO NOTHING` — same as T01.

### Graceful Degradation

If spaCy fails to parse a title (exception or no ROOT token found), store `ActionSignature(None, None, None)` and continue processing. A NULL action signature means Gate 2 is skipped during clustering — the article can merge with any cluster that passes Gate 1 (cosine similarity). Log a warning with the `article_id` and title.

## Acceptance Criteria

1. `extract_action_signature("Iran launches missile strike on Saudi oil facilities", "en")` returns an `ActionSignature` where `action == "launch"` (lemma, not surface form).
2. `extract_action_signature("Iran closes Strait of Hormuz", "en")` returns `action == "close"`.
3. Both signatures from criteria 1 and 2 have different `action` values, confirming Gate 2 would prevent them from being merged.
4. A Swedish title `"Riksbanken höjer räntan med 25 punkter"` processed with `language="sv"` returns a non-None `action` (Swedish model is active).
5. A title with no clear verb (e.g., a headline fragment like "Oil prices") returns `ActionSignature(actor=None, action=None, object=None)` without raising an exception.
6. Both spaCy models (`en_core_web_sm`, `sv_core_news_sm`) are loaded exactly once at service startup — verified by checking `_models` dict is populated after `load_spacy_models()` and not re-loaded per message.
7. Each processed article produces exactly one row in `article_action_signatures` with the correct `article_id`.
8. `ON CONFLICT (article_id) DO NOTHING` prevents duplicate rows on reprocessing.
9. Unit tests in `src/services/cleansing/tests/test_action_signature.py` cover: English extraction, Swedish extraction, missing verb handling, and storage mock.

## Implementation Notes

- **spaCy model size:** `en_core_web_sm` is ~12 MB, `sv_core_news_sm` is ~86 MB. Both are loaded into memory at startup. On a container with 512 MB RAM this is fine. Add both to the Dockerfile as `RUN python -m spacy download en_core_web_sm && python -m spacy download sv_core_news_sm`.
- **Verb lemmatization accuracy:** spaCy's lemmatizer is highly accurate for news headlines. Edge cases: passive constructions ("Saudi Arabia was struck by Iran" → actor becomes "Saudi Arabia", action becomes "strike" — acceptable, the event is the same).
- **Swedish model availability:** `sv_core_news_sm` is part of the official spaCy model distribution. If it's unavailable in the container (network issues during build), fall back to `en_core_web_sm` for Swedish articles and log a startup warning.
- **Async vs sync:** spaCy's `nlp(text)` call is synchronous CPU-bound. Run it in a thread pool executor to avoid blocking the async event loop: `await asyncio.get_event_loop().run_in_executor(None, nlp, title)`.
- **Title length:** News titles are typically 60-120 characters. No truncation needed.
- **Noun chunk availability:** `noun_chunks` requires the dependency parser, which is included in both `sm` models. If using a model without a parser (not the case here), the extraction would fail silently — not a concern with the specified models.
- **WordNet synonym check** for clustering compatibility is NOT implemented here — it belongs to S02/T02. This task only stores the raw verb lemma.

## Definition of Done

> Verified against the delivered flat-module implementation; legacy `nlp/action_signature.py`,
> `article_action_signatures`, and migration `002` names are superseded by `cleansing/extraction.py`
> (+ `cleansing/taxonomy.py`) and the `cleansing.article_actions` table created by `apply_schema`.

- [x] Unit tests pass (`tests/test_extraction.py` + `tests/test_taxonomy.py`)
- [x] Code passes `ruff check .` with zero errors
- [x] Code passes `mypy cleansing` with no type errors (`cleansing/extraction.py`)
- [x] Both spaCy models (`en_core_web_sm`, `sv_core_news_sm`) download and load in the container (`Dockerfile.ml`, live-verified)
- [x] `cleansing.article_actions` table created by `apply_schema` on startup (replaces migration `002`)
- [x] English and Swedish extraction both produce verb lemmas / actors — live-verified (Swedish `utbildningsminister`)
- [x] Parse failure backs off to the deterministic keyword classifier (no exception raised)
- [x] spaCy runs off the event loop via `asyncio.to_thread` (equivalent to `run_in_executor`)
- [x] Dockerfile updated with spaCy model download commands (`Dockerfile.ml`)
