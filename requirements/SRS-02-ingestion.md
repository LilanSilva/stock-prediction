# SRS-02: Ingestion Service

## 1. Document control

| | |
|---|---|
| Document ID | `SRS-02` |
| Component | Ingestion Service |
| Requirement ID prefix | `ING` |
| Status | `Implemented` |
| Version | `1.0.0` |
| Source code | [src/services/ingestion/ingestion/](../src/services/ingestion/ingestion/) |
| Tests | [src/services/ingestion/tests/](../src/services/ingestion/tests/) |
| Owned schema | `ingestion` |
| Last verified against code | `2026-08-05` |

## 2. Purpose and scope

### 2.1 What this component does

The Ingestion Service is the system's entry point. It polls news sources on a schedule, fetches each
article's text safely, normalises it into a canonical record, stores it exactly once, and announces it
to the rest of the pipeline as an `ArticleIngested` message.

It is deliberately unintelligent. It does not decide what an article means, which asset it affects,
or whether the source is trustworthy — it only guarantees that **an article enters the system once,
in a clean and consistent form, with its provenance intact.**

### 2.2 In scope

- Scheduled polling of RSS feeds and the FreeNewsApi.io search API.
- URL canonicalisation for de-duplication.
- SSRF-hardened article body fetching.
- Unicode, whitespace, and timestamp normalisation; content hashing.
- Idempotent storage with a transactional outbox.
- Per-source circuit breaking, so one failing source cannot stop the others.
- Time-based retention cleanup.

### 2.3 Explicitly out of scope

| Out of scope | Where it belongs |
|---|---|
| Classifying an event type | Cleansing |
| Detecting duplicate *events* (as opposed to duplicate URLs) | Cleansing |
| Deciding which assets an article affects | Cleansing |
| Scoring source quality | Credibility |
| Publishing raw HTML on the message bus | Nowhere — prohibited |
| Consuming any queue | This service has no consumer; it is source-driven |

## 3. Definitions

| Term | Meaning |
|---|---|
| Source | One configured news origin, identified by a `source_id` such as `di` or `freenewsapi` |
| Adapter | The code that talks to one kind of source and returns `RawArticle` records |
| `RawArticle` | An unnormalised article as an adapter returned it |
| Canonical URL | A URL reduced to a stable form, used as the de-duplication key |
| Body fetch | Downloading the article page to extract its text |
| Summary fallback | Using the feed's own summary text when a body fetch fails |
| SSRF | Server-Side Request Forgery — tricking a server into fetching an internal address |
| Circuit breaker | A guard that stops calling a repeatedly failing source for a cooldown period |
| Outbox | The table holding messages to publish, so a commit and a publish cannot diverge |
| Poll | One scheduled pass over all configured sources |

## 4. System context

### 4.1 Position in the pipeline

```text
  RSS feeds (di, dn, svd, aftonbladet)
  FreeNewsApi.io search API
              |
              v
  +---------------------------+
  |   INGESTION SERVICE       |
  |   - poll on a schedule    |
  |   - fetch body (SSRF-safe) |
  |   - normalise + hash      |
  |   - store + outbox        |
  +---------------------------+
              | article.ingested
              v
     cleansing.articles queue --> Cleansing Service
```

### 4.2 Dependencies

| Dependency | Purpose | Failure impact |
|---|---|---|
| PostgreSQL `ingestion` schema | Article storage and outbox | Readiness fails; no polling result can be stored |
| RabbitMQ `feed.events` | Publishing `ArticleIngested` | Articles still store; outbox retains rows and retries |
| RSS endpoints | Article metadata | That source's circuit opens; other sources continue |
| FreeNewsApi.io | Global English news | That source's circuit opens; the four RSS feeds continue |
| Article websites | Full body text | Falls back to the feed summary for that article only |

## 5. Functional requirements

### 5.1 Polling and sources

| ID | Requirement | Priority | Status |
|---|---|---|---|
| `ING-1` | The service **shall** poll every configured source on a fixed configurable interval. | Must | Implemented |
| `ING-2` | The service **shall** perform one poll shortly after startup rather than waiting a full interval. | Must | Implemented |
| `ING-3` | The service **shall** support RSS sources, each declaring its own source ID, feed URL, language, and country. | Must | Implemented |
| `ING-4` | The service **shall** support the FreeNewsApi.io search API as a keyed source. | Must | Implemented |
| `ING-5` | The service **shall** disable the FreeNewsApi source when no API key is configured, and **shall** continue polling the RSS sources. | Must | Implemented |
| `ING-6` | A failure in one source **shall not** prevent the remaining sources from completing their poll. | Must | Implemented |
| `ING-7` | The service **shall** skip a malformed individual feed entry rather than discarding the whole feed. | Must | Implemented |
| `ING-8` | The service **shall** pace FreeNewsApi requests to stay below the provider's documented rate cap. | Must | Implemented |

### 5.2 Body fetching and security

| ID | Requirement | Priority | Status |
|---|---|---|---|
| `ING-9` | The service **shall** permit only the `http` and `https` URL schemes when fetching an article body. | Must | Implemented |
| `ING-10` | The service **shall** resolve the target hostname and **shall** reject any destination resolving to a loopback, private, link-local, multicast, reserved, or unspecified address. | Must | Implemented |
| `ING-11` | The service **shall** reject a cloud metadata endpoint destination. | Must | Implemented |
| `ING-12` | The service **shall** re-resolve and re-validate every redirect target before following it. | Must | Implemented |
| `ING-13` | The service **shall** bound the number of redirects it follows. | Must | Implemented |
| `ING-14` | The service **shall** bound the number of response bytes it reads. | Must | Implemented |
| `ING-15` | The service **shall** apply an overall timeout to a body fetch, independent of per-read timeouts. | Must | Implemented |
| `ING-16` | The service **shall** accept only the configured content types and **shall** reject any other. | Must | Implemented |
| `ING-17` | The service **shall not** forward authorization, cookie, or proxy-authorization headers on an outbound fetch. | Must | Implemented |
| `ING-18` | The service **shall** fall back to the feed's own summary text when a body fetch fails, rather than discarding the article. | Must | Implemented |
| `ING-19` | The service **shall** bound the concurrency of simultaneous body fetches. | Must | Implemented |
| `ING-20` | The service **shall** be able to run with body fetching disabled entirely. | Should | Implemented |

### 5.3 Normalisation and identity

| ID | Requirement | Priority | Status |
|---|---|---|---|
| `ING-21` | The service **shall** canonicalise each article URL by lowercasing the scheme and host, removing a default port, dropping tracking parameters and the fragment, sorting the remaining query, and trimming a trailing slash on a non-root path. | Must | Implemented |
| `ING-22` | The service **shall** apply Unicode NFC normalisation, collapse whitespace runs, and strip leading and trailing whitespace from the title and body. | Must | Implemented |
| `ING-23` | The service **shall** truncate a stored body to the configured maximum character count. | Must | Implemented |
| `ING-24` | The service **shall** compute a SHA-256 content hash over the normalised title and body. | Must | Implemented |
| `ING-25` | The service **shall** store `published_at` as a timezone-aware UTC datetime. | Must | Implemented |
| `ING-26` | The service **shall** record a two-letter lowercase language code and a two-letter uppercase country code for every article. | Must | Implemented |
| `ING-27` | The service **shall** generate a fresh `correlation_id` for each new article, since each article is the root of its own causal chain. | Must | Implemented |

### 5.4 Storage and publication

| ID | Requirement | Priority | Status |
|---|---|---|---|
| `ING-28` | The service **shall** treat the canonical URL as unique, so a repeated poll creates no second article row. | Must | Implemented |
| `ING-29` | The service **shall** insert the article row and its outbox row in one database transaction. | Must | Implemented |
| `ING-30` | The service **shall** publish `ArticleIngested` to the `feed.events` exchange with routing key `article.ingested`. | Must | Implemented |
| `ING-31` | The service **shall not** publish raw HTML in any message. | Must | Implemented |
| `ING-32` | The service **shall** relay pending outbox rows after every poll. | Must | Implemented |
| `ING-33` | The service **shall** mark an outbox row delivered only after its publication succeeds. | Must | Implemented |
| `ING-34` | The service **shall** record the attempt count and last error on an outbox row whose publication failed, and **shall** continue with the remaining rows. | Must | Implemented |
| `ING-35` | The service **shall** publish outbox rows left pending by a crash when it next relays the outbox. | Must | Implemented |
| `ING-36` | A single failing article **shall not** abort the storage of the remaining articles from that source. | Must | Implemented |

### 5.5 Resilience

| ID | Requirement | Priority | Status |
|---|---|---|---|
| `ING-37` | The service **shall** give each source its own independent circuit breaker. | Must | Implemented |
| `ING-38` | The service **shall** open a source's circuit after the configured number of consecutive failures. | Must | Implemented |
| `ING-39` | The service **shall** reject calls to a source whose circuit is open, without contacting it. | Must | Implemented |
| `ING-40` | The service **shall** allow one probe call after the configured reset timeout elapses, closing the circuit on success and reopening it on failure. | Must | Implemented |
| `ING-41` | The service **shall** treat an HTTP 429 from FreeNewsApi as a source failure that counts toward its circuit breaker. | Must | Implemented |
| `ING-42` | The service **shall not** use a fixed sleep as its throttling strategy. | Must | Implemented |

### 5.6 Retention

| ID | Requirement | Priority | Status |
|---|---|---|---|
| `ING-43` | The service **shall** run a scheduled retention cleanup on a configurable interval. | Must | Implemented |
| `ING-44` | The service **shall** delete article rows older than the configured retention window. | Must | Implemented |
| `ING-45` | The service **shall** delete outbox rows that are delivered and older than the configured outbox retention window. | Must | Implemented |
| `ING-46` | The service **shall not** delete a pending outbox row, regardless of age. | Must | Implemented |
| `ING-47` | The service **shall** allow retention to be disabled by configuration. | Should | Implemented |
| `ING-48` | The article retention window **shall** exceed the longest configured source lookback window. | Must | Implemented |

### 5.7 Operations

| ID | Requirement | Priority | Status |
|---|---|---|---|
| `ING-49` | The service **shall** expose `GET /health` reporting process liveness. | Must | Implemented |
| `ING-50` | The service **shall** expose `GET /ready` reporting PostgreSQL, RabbitMQ, and scheduler readiness. | Must | Implemented |
| `ING-51` | On shutdown the service **shall** stop new polls, allow active fetches to finish or cancel safely, flush eligible outbox work, and close its pools. | Must | Implemented |

## 6. Non-functional requirements

| ID | Requirement | Priority | Status |
|---|---|---|---|
| `ING-52` | Logs **shall** include the source ID, article ID, message ID, correlation ID, operation, and status. | Must | Implemented |
| `ING-53` | Logs **shall not** contain full article bodies or secrets. | Must | Implemented |
| `ING-54` | The FreeNewsApi key **shall** be supplied only as an environment variable and **shall not** be committed. | Must | Implemented |
| `ING-55` | The service **shall** bound its PostgreSQL connection pool size. | Must | Implemented |
| `ING-56` | Downloaded article content **shall** be treated as untrusted data. | Must | Implemented |
| `ING-57` | The service **shall** pass `ruff check` and `mypy --strict`. | Must | Implemented |

## 7. How it works

### 7.1 One poll cycle

**Purpose:** discover new articles from every source and get them into the pipeline exactly once.

**Steps** → [pipeline.py](../src/services/ingestion/ingestion/pipeline.py)

1. The scheduler fires. A poll ID is generated for log correlation.
2. **For each source, in turn:**
   1. Call the adapter's `fetch()`, wrapped by that source's circuit breaker.
   2. If the circuit is open, or the fetch raises, log a warning, record zero articles for that
      source, and **continue to the next source**. The poll never aborts.
   3. Resolve bodies for the returned articles concurrently, bounded by a semaphore.
   4. Store each article one at a time. A failure on one article is logged and skipped; the rest
      still store.
3. After all sources are done, relay the outbox.
4. Log the per-source counts, the total new articles, and the number published.

**Why sources are sequential but bodies are concurrent:** sources are polite one at a time and a
slow source shouldn't multiply load; body fetches are the slow part and benefit from parallelism, so
they are bounded by a semaphore rather than serialised.

### 7.2 Body resolution and its fallback

**Purpose:** get the best available text without ever letting a fetch failure lose an article.

**Steps** → `_resolve_body` in [pipeline.py](../src/services/ingestion/ingestion/pipeline.py)

1. If body fetching is disabled or no fetcher is configured, return the feed summary.
2. Otherwise attempt the SSRF-safe fetch.
3. On **any** exception, log at info level and return the feed summary.

This function is documented as never raising. It is the fault-isolation boundary for concurrent body
fetching: if it could raise, one bad article would fail the whole gathered batch.

### 7.3 The SSRF-safe fetch

**Purpose:** an article URL comes from an external feed, so it must be treated as attacker-controlled.
Without these controls, a malicious feed entry could make the service fetch an internal address —
for example a cloud metadata endpoint holding credentials.

**Steps** → [fetcher.py](../src/services/ingestion/ingestion/fetcher.py)

1. Wrap the whole attempt in an overall timeout. This catches a slow-trickle server that never trips
   a per-read timeout.
2. Set a minimal header set. Credential-bearing headers are never included.
3. **Loop, up to the redirect limit:**
   1. **Validate the URL:**
      - Scheme must be `http` or `https`.
      - A host must be present.
      - If the host is an IP literal, use it; otherwise resolve it via DNS.
      - Reject if **any** resolved address is private, loopback, link-local, multicast, reserved, or
        unspecified.
   2. Issue a streaming GET with redirects **not** followed automatically.
   3. If the response is a redirect, join the `Location` header to the current URL and loop back to
      step 3.1 — so the new target is fully re-validated.
   4. Otherwise: raise for HTTP error status, check the content type against the allowlist, then read
      the body in chunks, stopping at the byte limit.
   5. Decode using the response encoding, replacing undecodable bytes.
4. Exceeding the redirect limit is an error.

**Rules:**

- Link-local rejection covers `169.254.169.254`, the cloud metadata address.
- **Every** resolved address is checked, not just the first — a hostname resolving to both a public
  and a private address is rejected.
- Redirects are re-validated because a public URL can redirect to an internal one. This is why the
  client must not follow redirects itself.

### 7.4 URL canonicalisation

**Purpose:** the same article often arrives with different tracking parameters. Without
canonicalisation the unique constraint would not catch it and the same story would enter twice.

**Steps** → [urls.py](../src/services/ingestion/ingestion/urls.py)

1. Lowercase the scheme and host.
2. Drop the port if it is the scheme's default (80 for http, 443 for https).
3. Drop tracking parameters: anything starting with `utm_`, plus `fbclid`, `gclid`, `igshid`,
   `mc_cid`, `mc_eid`, `ref`, `ref_src`, `cmpid`.
4. Drop blank-valued parameters and the fragment entirely.
5. Sort the remaining query parameters, so ordering cannot produce two forms.
6. Trim a trailing slash from a non-root path.

**Example:**

```text
https://Example.COM:443/news/story/?utm_source=twitter&id=5#top
                    |
                    v
https://example.com/news/story?id=5
```

### 7.5 Normalisation and hashing

**Steps** → [normalize.py](../src/services/ingestion/ingestion/normalize.py)

1. **Unicode NFC** on the title and body, so visually identical text has one byte representation.
2. **Collapse whitespace** — every run of whitespace becomes one space; ends are stripped.
3. **Truncate** the body to `BODY_MAX_CHARS` (default 2000).
4. **Hash** — SHA-256 over the normalised title and body joined by a null byte. The null separator
   prevents a title/body boundary shift from producing the same hash.

The hash is stored and indexed for content-based de-duplication, alongside the canonical URL, which
is the enforced unique constraint.

### 7.6 Transactional storage

**Purpose:** an article row and its outgoing message must either both exist or neither.

**Steps** → [storage.py](../src/services/ingestion/ingestion/storage.py)

1. Canonicalise the URL, normalise the title and body, compute the hash, generate a new `article_id`.
2. Build the complete `ArticleIngested` message and serialise it to JSON. Building it now means an
   invalid value is caught **before** anything is written.
3. **Open one transaction:**
   1. `INSERT ... ON CONFLICT (canonical_url) DO NOTHING RETURNING article_id`.
   2. If nothing was returned, the URL already exists → **return `None`**, an idempotent no-op. No
      outbox row, no message, no duplicate.
   3. Otherwise insert the outbox row carrying the serialised message and its routing key.
4. Commit. Return the message.

**Why the message is built before the insert:** if construction fails validation, no partial state
was created.

### 7.7 Outbox relay

**Purpose:** guarantee that a stored article is eventually announced, even across a crash.

**Steps** → `OutboxPublisher.publish_pending`

1. Select up to `batch_size` (default 100) rows with `delivery_status = 'PENDING'`, oldest first.
2. For each row:
   - Deserialise the payload and publish it.
   - On **success**, set `delivery_status = 'DELIVERED'`, stamp `delivered_at`, clear `last_error`.
   - On **failure**, increment `attempts`, record `last_error`, and **continue to the next row**.
3. Return the delivered count.

**Recovery:** if the process dies between commit and publish, the row stays `PENDING`. The next relay
— after the next poll, or after restart — publishes it. Nothing is lost, and no duplicate article
identity is created because the article row already exists.

### 7.8 Circuit breaking

**Purpose:** stop hammering a source that is down, without letting it affect healthy sources.

**States** → [resilience.py](../src/services/ingestion/ingestion/resilience.py)

```text
   CLOSED  --- failure_threshold consecutive failures --->  OPEN
     ^                                                       |
     |                                              reset_timeout elapses
     |                                                       |
     |                                                       v
     +----------- probe succeeds --------------------  HALF_OPEN
                                                             |
                                                    probe fails
                                                             |
                                                             v
                                                           OPEN
```

| State | Behaviour |
|---|---|
| `CLOSED` | Calls pass through. Reaching the failure threshold opens the circuit |
| `OPEN` | Calls are rejected immediately without contacting the source, until the reset timeout elapses |
| `HALF_OPEN` | Exactly one probe is allowed. Success closes the circuit; failure reopens it |

**Rules:**

- Each source is wrapped in its **own** breaker instance, so a flaky source cannot trip a healthy
  one's breaker.
- A success resets the failure count to zero.
- The breaker replaces the fixed sleep that a previous design used, which was insufficient: a fixed
  sleep keeps calling a dead source forever.

### 7.9 Retention cleanup

**Purpose:** keep tables bounded without weakening de-duplication.

**Steps** → [retention.py](../src/services/ingestion/ingestion/retention.py)

1. Delete `ingestion.articles` rows where `ingested_at` is older than `ARTICLE_RETENTION_DAYS`
   (default 30).
2. Delete `ingestion.outbox` rows that are `DELIVERED` **and** whose `delivered_at` is older than
   `OUTBOX_RETENTION_DAYS` (default 7).
3. Log both counts.

**Why deletion is safe:** the sources only surface *recent* items. RSS lists the last few days;
FreeNewsApi is queried newest-first. An article older than any feed's lookback window can never be
re-offered, so deleting its row cannot cause a re-ingest.

**The critical constraint:** the retention window must always exceed the longest source lookback. If
it did not, a still-listed article would lose its row and be ingested a second time as a new article.

**Why it is decoupled from consumers:** Cleansing consumes the *queue*, not the table. Deletion is
driven purely by row age, never by whether a consumer read it.

**Pending outbox rows are never pruned** regardless of age — pruning one would silently lose an
article that was stored but never announced.

## 8. Interfaces

### 8.1 Messages consumed

None. This service is source-driven, not message-driven.

### 8.2 Messages published

| Routing key | Message | When |
|---|---|---|
| `article.ingested` | `ArticleIngested` | One per newly stored article, relayed from the outbox |

**`ArticleIngested` payload**, in addition to the envelope from
[SRS-01 §8.1](SRS-01-shared-foundation.md#81-message-envelope):

| Field | Type | Notes |
|---|---|---|
| `article_id` | UUID | Generated by this service |
| `source_id` | non-empty string | e.g. `di`, `dn`, `svd`, `aftonbladet`, `freenewsapi` |
| `canonical_url` | HTTP URL | The canonicalised URL; also the uniqueness key |
| `title` | non-empty string | Normalised |
| `body` | string | Normalised and truncated |
| `published_at` | UTC datetime | From the source |
| `language` | string | Two lowercase letters, ISO 639-1 |
| `country` | string | Two uppercase letters, ISO 3166-1 alpha-2 |
| `content_hash` | non-empty string | SHA-256 over normalised title and body |

**Raw HTML is never included.** If a source's terms permit retention it stays in the `ingestion`
schema with a bounded size, never on the bus.

### 8.3 HTTP endpoints

| Method | Path | Purpose | Response |
|---|---|---|---|
| `GET` | `/health` | Process liveness | `200` with status |
| `GET` | `/ready` | PostgreSQL, RabbitMQ, and scheduler readiness | `200` ready, `503` not ready |

### 8.4 Scheduled jobs

| Job | Interval | Purpose |
|---|---|---|
| Poll all sources | `INGESTION_POLL_INTERVAL_SECONDS` (default 3600) | One pass over every source; also fires once shortly after startup |
| Retention cleanup | `RETENTION_INTERVAL_SECONDS` (default 86400) | Delete aged articles and delivered outbox rows |

### 8.5 External sources

**RSS sources** — the four Swedish feeds that passed the POC-6 availability canary:

| `source_id` | Feed URL | Language | Country |
|---|---|---|---|
| `di` | `https://www.di.se/rss` | `sv` | `SE` |
| `dn` | `https://www.dn.se/rss/` | `sv` | `SE` |
| `svd` | `https://www.svd.se/feed/articles.rss` | `sv` | `SE` |
| `aftonbladet` | `https://rss.aftonbladet.se/rss2/small/pages/sections/senastenytt/` | `sv` | `SE` |

Feed requests send a browser-like User-Agent and Accept header, because some feeds — `dn.se` among
them — answer a bare request with HTTP 406.

**FreeNewsApi.io** — `source_id` `freenewsapi`:

| Aspect | Detail |
|---|---|
| Base URL | `https://api.freenewsapi.io/v1` |
| Authentication | `x-api-key` header |
| List endpoint | `GET /news?language=&order_by=recent&page_size=` |
| Detail endpoint | `GET /details?uuid=` — supplies `original_url` and `body` |
| Rate budget | 5000 requests/day, visible in `X-RateLimit-*-Day` headers |
| Pacing | Minimum 0.6 s between calls, below the documented 2 req/s cap |

Two calls are needed per article because the list response has no URL. Errors map as follows: `429`
→ rate-limited failure, `401` → rejected key, any `4xx`/`5xx` → failure, non-JSON → failure. Every
one raises an adapter error, which trips the circuit breaker. A genuinely empty result returns an
empty list, which is not a failure.

No keyword pre-filtering is used: fetching recent articles and letting Cleansing classify relevance
proved more reliable than `in_title` queries, which returned HTTP 500 from the provider.

## 9. Data design

### 9.1 Owned schema

`ingestion` in the `feed` database. DDL applied idempotently at startup by
[db.py](../src/services/ingestion/ingestion/db.py).

### 9.2 Table: `ingestion.articles`

One row per unique article. This table is the de-duplication anchor.

| Column | Type | Notes |
|---|---|---|
| `article_id` | `UUID` | Primary key, generated per article |
| `source_id` | `TEXT NOT NULL` | Which source supplied it |
| `canonical_url` | `TEXT NOT NULL UNIQUE` | **The idempotency key** |
| `title` | `TEXT NOT NULL` | Normalised |
| `body` | `TEXT NOT NULL` | Normalised and truncated |
| `published_at` | `TIMESTAMPTZ NOT NULL` | From the source |
| `language` | `TEXT NOT NULL` | ISO 639-1 |
| `country` | `TEXT NOT NULL` | ISO 3166-1 alpha-2 |
| `content_hash` | `TEXT NOT NULL` | SHA-256 over normalised title and body |
| `ingested_at` | `TIMESTAMPTZ NOT NULL DEFAULT now()` | Drives retention |

**Indexes and constraints:**

- `UNIQUE (canonical_url)` — the guarantee that a repeated poll cannot create a second row.
- `ix_articles_content_hash` on `content_hash` — supports content-based duplicate lookups.

### 9.3 Table: `ingestion.outbox`

One row per message awaiting publication.

| Column | Type | Notes |
|---|---|---|
| `id` | `BIGSERIAL` | Primary key |
| `message_id` | `UUID NOT NULL UNIQUE` | Matches the message envelope |
| `aggregate_id` | `UUID NOT NULL` | The `article_id` this message concerns |
| `routing_key` | `TEXT NOT NULL` | `article.ingested` |
| `payload` | `JSONB NOT NULL` | The complete serialised message |
| `created_at` | `TIMESTAMPTZ NOT NULL DEFAULT now()` | Relay order |
| `delivery_status` | `TEXT NOT NULL DEFAULT 'PENDING'` | `PENDING` or `DELIVERED` |
| `attempts` | `INTEGER NOT NULL DEFAULT 0` | Incremented on each failure |
| `last_error` | `TEXT` | Most recent failure reason |
| `delivered_at` | `TIMESTAMPTZ` | Set on success; drives outbox retention |

**Indexes:**

- `ix_outbox_pending` on `created_at` **where** `delivery_status = 'PENDING'` — a partial index, so
  the relay query stays fast even as delivered rows accumulate.

## 10. Configuration

Read from the environment by [config.py](../src/services/ingestion/ingestion/config.py). Connection
strings use their conventional unprefixed names.

### 10.1 Infrastructure

| Variable | Default | Effect |
|---|---|---|
| `DATABASE_URL` | `postgresql://feed_user:local_dev_pw@localhost:5432/feed` | PostgreSQL connection |
| `RABBITMQ_URL` | `amqp://feed_user:local_dev_pw@localhost:5672/` | Broker connection |
| `LOG_LEVEL` | `INFO` | Structured log threshold |

### 10.2 Polling

| Variable | Default | Effect |
|---|---|---|
| `INGESTION_POLL_INTERVAL_SECONDS` | `3600` | Seconds between polls. Must be `> 0` |
| `FEED_FETCH_TIMEOUT_SECONDS` | `30.0` | Timeout for retrieving a feed |

**Rate-budget guidance:** only FreeNewsApi counts against its 5000 requests/day cap. Worst case is
`(86400 / interval) × (keywords + keywords × page_size)`. At 900 s that is roughly 2880/day, about
58% of budget. **Do not go below about 600 s** (roughly 86%) without trimming keywords or page size.

### 10.3 Body fetching

| Variable | Default | Effect |
|---|---|---|
| `BODY_FETCH_ENABLED` | `true` | When false, the feed summary is always used |
| `BODY_FETCH_OVERALL_TIMEOUT_SECONDS` | `20.0` | Whole-fetch timeout, catching slow-trickle servers |
| `BODY_FETCH_CONCURRENCY` | `10` | Simultaneous body fetches, minimum 1 |
| `BODY_MAX_CHARS` | `2000` | Stored body truncation length |

Fixed in code, not configurable: maximum 2 000 000 response bytes, maximum 3 redirects, and the
accepted content types `text/html`, `application/xhtml+xml`, `text/plain`.

### 10.4 Resilience

| Variable | Default | Effect |
|---|---|---|
| `CIRCUIT_FAILURE_THRESHOLD` | `5` | Consecutive failures that open a source's circuit |
| `CIRCUIT_RESET_TIMEOUT_SECONDS` | `60.0` | Cooldown before a half-open probe is allowed |

### 10.5 Retention

| Variable | Default | Effect |
|---|---|---|
| `RETENTION_ENABLED` | `true` | When false, no cleanup job is scheduled |
| `ARTICLE_RETENTION_DAYS` | `30` | Age at which an article row is deleted. **Must exceed the longest source lookback** |
| `OUTBOX_RETENTION_DAYS` | `7` | Age at which a delivered outbox row is deleted |
| `RETENTION_INTERVAL_SECONDS` | `86400` | Cleanup cadence |

### 10.6 FreeNewsApi

| Variable | Default | Effect |
|---|---|---|
| `FREENEWSAPI_KEY` | empty | **Secret.** Empty disables the source; the RSS feeds still run |
| `FREENEWSAPI_BASE_URL` | `https://api.freenewsapi.io/v1` | API base |
| `FREENEWSAPI_LANGUAGE` | `en` | Search language |
| `FREENEWSAPI_PAGE_SIZE` | `40` | Items per search, 1 to 100 |

### 10.7 Database pool

| Variable | Default | Effect |
|---|---|---|
| `DB_POOL_MIN_SIZE` | `1` | Minimum pooled connections |
| `DB_POOL_MAX_SIZE` | `5` | Maximum pooled connections |

## 11. Verification

| Requirement | Method | Evidence |
|---|---|---|
| `ING-1`, `ING-2` | Test | [test_integration.py](../src/services/ingestion/tests/test_integration.py) — scheduled poll and startup poll |
| `ING-3`, `ING-7` | Test | [test_rss_adapter.py](../src/services/ingestion/tests/test_rss_adapter.py) — feed parsing, malformed entry skipped |
| `ING-4`, `ING-5`, `ING-8`, `ING-41` | Test | [test_freenewsapi_adapter.py](../src/services/ingestion/tests/test_freenewsapi_adapter.py) — list plus details, pacing, 429 and 401 handling, empty key |
| `ING-6`, `ING-36` | Test | [test_pipeline.py](../src/services/ingestion/tests/test_pipeline.py) — a failing source and a failing article do not abort the poll |
| `ING-9`…`ING-17` | Test | [test_fetcher.py](../src/services/ingestion/tests/test_fetcher.py) — scheme, address blocklist, metadata endpoint, redirect revalidation, redirect and byte limits, timeout, content type, header stripping |
| `ING-18`, `ING-20` | Test | [test_pipeline.py](../src/services/ingestion/tests/test_pipeline.py) — summary fallback on fetch failure and when disabled |
| `ING-19` | Test | [test_pipeline_concurrency.py](../src/services/ingestion/tests/test_pipeline_concurrency.py) — concurrency bounded by the semaphore |
| `ING-21` | Test | [test_urls.py](../src/services/ingestion/tests/test_urls.py) — every canonicalisation rule |
| `ING-22`…`ING-24` | Test | [test_normalize.py](../src/services/ingestion/tests/test_normalize.py) — NFC, whitespace, truncation, stable hash |
| `ING-25`…`ING-27` | Test | [test_integration.py](../src/services/ingestion/tests/test_integration.py) — UTC timestamps, code formats, fresh correlation ID |
| `ING-28`, `ING-29` | Test | [test_integration.py](../src/services/ingestion/tests/test_integration.py) — re-poll creates no duplicate row or message |
| `ING-30`, `ING-31` | Test | [test_integration.py](../src/services/ingestion/tests/test_integration.py) — routing key correct, no raw HTML present |
| `ING-32`…`ING-35` | Test | [test_integration.py](../src/services/ingestion/tests/test_integration.py) — relay, delivery marking, error recording, crash recovery |
| `ING-37`…`ING-40`, `ING-42` | Test | [test_resilience.py](../src/services/ingestion/tests/test_resilience.py) — per-source isolation, threshold, fast rejection, half-open probe |
| `ING-43`…`ING-47` | Test | [test_integration.py](../src/services/ingestion/tests/test_integration.py) — aged rows deleted, pending outbox retained, disable flag |
| `ING-48` | Inspection | Default 30 days versus the longest feed lookback of a few days |
| `ING-49`…`ING-51` | Test | [test_integration.py](../src/services/ingestion/tests/test_integration.py) — health, readiness, lifespan shutdown |
| `ING-52`, `ING-53` | Inspection | Structured log calls throughout the service |
| `ING-54` | Inspection | [config.py](../src/services/ingestion/ingestion/config.py) — key read from `FREENEWSAPI_KEY`, default empty |
| `ING-55` | Inspection | [config.py](../src/services/ingestion/ingestion/config.py) — pool bounds |
| `ING-57` | Demonstration | `ruff check` and `mypy --strict` |

## 12. Failure handling

| Failure | Behaviour | Recovery |
|---|---|---|
| One RSS feed returns an error | Warning logged, zero articles for that source, other sources continue | Next poll; circuit opens after repeated failures |
| A feed entry is malformed | That entry is skipped | Not applicable — the rest of the feed still processes |
| FreeNewsApi returns 429 | Adapter error, circuit failure recorded | Circuit opens; half-open probe after the reset timeout |
| FreeNewsApi rejects the key | Adapter error identifying a key problem | Requires a configuration fix |
| A body fetch fails or is blocked | Info logged; the feed summary is used for that article | Not applicable — the article is still ingested |
| A body fetch is slow | Overall timeout fires; the summary is used | Not applicable |
| A destination is non-public | `SsrfBlockedError`; the summary is used | Not applicable — the block is correct behaviour |
| PostgreSQL unavailable | Storage raises; readiness fails | Automatic on reconnection |
| One article's insert fails | Error logged, that article skipped, the rest still store | Next poll retries it, since no row exists |
| RabbitMQ unavailable | Outbox rows stay `PENDING` with `attempts` incremented | Next relay drains them |
| Crash between commit and publish | The outbox row remains `PENDING` | The next relay publishes it; no duplicate article identity |

## 13. Assumptions, dependencies, and known limitations

### 13.1 Assumptions

- Sources surface only recent items, which is what makes time-based retention safe.
- The four Swedish RSS feeds remain available; they passed the POC-6 availability canary.
- A canonical URL identifies an article. Two genuinely different articles at one URL would be treated
  as one.
- The rough 4-characters-per-token estimate is irrelevant here — this service makes no LLM calls.

### 13.2 Accepted design decisions

| Decision | Reason |
|---|---|
| GDELT was replaced by FreeNewsApi.io | GDELT returned 0 records under an undocumented HTTP 429 IP rate limit. FreeNewsApi exposes a header-visible 5000/day budget that hourly polling never approaches |
| No keyword pre-filtering on FreeNewsApi | `in_title` queries returned HTTP 500 from the provider. Fetching recent articles and letting Cleansing classify relevance is more reliable |
| A body fetch failure falls back to the summary rather than failing | A partial article is more useful than no article, and the summary is already validated feed content |
| Bodies are truncated to 2000 characters by default | Bounds storage and downstream prompt size; the summary is often comparable in length anyway |
| Two API calls per FreeNewsApi article | The list response contains no URL, so `/details` is required |
| A circuit breaker replaced a fixed sleep | A fixed sleep keeps calling a dead source indefinitely |

### 13.3 Known limitations

| Limitation | Consequence |
|---|---|
| The retention window must be maintained by hand against source lookbacks | Setting it too low would let a still-listed article be re-ingested as new |
| Only the canonical URL is enforced unique; the content hash is indexed but not constrained | The same story republished at a different URL creates two articles. Cleansing catches this later with SimHash |
| Body extraction takes the whole page text | No boilerplate stripping, so navigation and footer text can enter the body |
| Circuit breaker state is in-memory | A restart resets every circuit to closed, so a dead source is retried immediately after restart |
| All four RSS sources are Swedish | English coverage depends entirely on FreeNewsApi, so an unset key leaves the corpus Swedish-only |
| A source's failure is silent beyond the logs | There is no alerting; a permanently dead source shows up only as zero articles |

## 14. How to update this document

Follow the rules in [README.md](README.md#how-to-update-these-documents).

Component-specific notes:

- **Adding a source** means a new adapter, a new entry in section 8.5, and — critically — checking
  that `ARTICLE_RETENTION_DAYS` still exceeds that source's lookback window (`ING-48`).
- **Changing the `ArticleIngested` shape** is a contract change. Update
  [SRS-01 §8](SRS-01-shared-foundation.md#8-interfaces), [SRS-03](SRS-03-cleansing.md) as the
  consumer, and [SyRS §8.2](SyRS-system.md#82-message-catalogue).
- **Changing a fetcher control** requires a matching test in
  [test_fetcher.py](../src/services/ingestion/tests/test_fetcher.py). These are security controls;
  do not relax one without recording why in section 13.2.
- **Changing retention** means re-checking `ING-48` and the note about pending rows never being
  pruned.

## 15. Change history

| Date | Version | Change | Driver |
|---|---|---|---|
| `2026-08-05` | `1.0.0` | Initial specification, written from the implemented code | E02 complete; replaces the E02 epic and task files |
