# Feed Analyzer Development Guidance

## Start here

**Read the repository root `README.md` first** to understand the project folder structure and each
folder's purpose. Every top-level folder has its own `README.md` describing its inner folders and
files — **read the README of the folder you are working in before changing anything in it.**

This file contains only the conventions a coding agent must follow. Everything else lives where the
root README points.

## Documentation rules

Documentation is a **three-level chain, and each level explains only its own scope.** This is the flow
every reader follows, and the one you must preserve when updating docs:

```text
.github/copilot-instructions.md   agent conventions      -> sends the reader to the root README
        v
README.md                         top-level folders      -> one row per folder, linking to its README
        v
{folder}/README.md                that folder's contents -> its inner folders and files
        v
{folder}/{document}.md            the actual detail      -> specifications, ADRs, tasks, findings
```

**One fact, one home.** Never state the same thing in two documents. Before adding anything, find
where the topic already lives:

| Topic | Its only home |
|---|---|
| Folder structure and what each folder is for | root `README.md` |
| What is inside one folder | that folder's `README.md` |
| Requirements, service behaviour, contracts, schemas, config | the relevant `requirements/SRS-*.md` or `SyRS-system.md` |
| Event types, asset registry | `requirements/REF-01-*`, `requirements/REF-02-*` |
| Why a design decision was made | `requirements/ADR-decisions.md` |
| Environment setup, project scope | root `README.md` |
| Secrets rule, infrastructure validation | `infra/README.md` |
| Test commands, code layout conventions | `src/README.md` |
| Unbuilt work, milestones, POC findings | `backlog/README.md` and its subfolders |
| Rules for agents (this file's subject) | `.github/copilot-instructions.md` |

**When you update documentation:**

1. **Find the owner first.** Search the repository for the topic before writing. If it already exists,
   edit it there — do not restate it somewhere more convenient.
2. **Link instead of repeating.** A second document that needs the fact gets a one-line pointer with a
   relative link, never a copy. The exception is a hard safety constraint (for example "never commit a
   secret"), which may be restated briefly while still naming the owning document.
3. **Keep the chain intact.** Adding, moving, or deleting a folder or a significant file means updating
   that folder's `README.md` — and the root `README.md` too if a top-level folder changed.
4. **Do not document temporary or tool-owned paths.** Dot-folders (`.venv/`, `.claude/`, `.agents/`,
   caches) and generated output stay out of every README.
5. **Update the specification in the same change as the code.** See *Specifications* below — a
   requirement with no proving test is `Approved`, not `Implemented`.
6. **Delete what a change makes false.** A stale document is worse than a missing one, because it is
   still trusted. When content moves, remove the original rather than leaving both.
7. **Verify links before finishing.** Every relative link must resolve, including its `#anchor`.

**Test to apply before adding a paragraph:** *does this fact already have a home?* If yes, link to it.
If no, put it in the one document that owns the topic — and only there.

## Specifications

**Read the SRS for the service you are changing before you change it.** It lists every requirement,
message, table, config key, and the tests that protect existing behaviour.

The full authority order is in `requirements/README.md` ("Authority"). The part that governs your work:
executable code outranks every document, so if code and a specification disagree, **the code is the
truth and the specification is a defect** — fix the specification, or fix the code if the specification
describes agreed intent. Task files under `backlog/` are the lowest authority and predate the
specifications; never implement one that conflicts with a higher source.

**When you change observable behaviour, update the SRS in the same change** — a requirement with no
proving test is `Approved`, not `Implemented`. The update rules are in `requirements/README.md`
("How to update these documents").

## Architecture invariants

- Use one PostgreSQL database named `feed` with service-owned schemas: `ingestion`, `cleansing`,
  `prediction`, `market_data`, `verification`, and `credibility`.
- Neo4j stores the causal graph only.
- Publish persistent domain events to the durable `feed.events` topic exchange by canonical routing
  key. Every consumer owns an independent queue; observers never consume another service's work
  queue.
- Every durable work queue has a dedicated DLQ through `feed.dlx`.
- Use canonical asset IDs at service boundaries. Provider symbols such as `XAUUSD` and `SAAB-B.ST`
  belong only inside market-data adapters. An asset ID absent from the loaded registry is rejected at
  every message boundary.
- Market Data routes per asset on the registry's `provider` field: `biquote.io` for a curated list of
  US mega-caps, `yahoo` for European markets and the US names biquote lacks. The Yahoo adapter must
  send a browser User-Agent.
- Verification alone produces `PriceRequested`, containing both baseline and settlement sessions.
  Market Data returns both immutable closes in one `PriceObserved`.
- State-changing persistence plus publication requires an outbox or equivalent reconciliation.
  Consumers must be idempotent.

## Canonical messages

The six domain messages are `ArticleIngested`, `EventDetected`, `PredictionMade`,
`PriceRequested`, `PriceObserved`, and `PredictionScored`.

All envelopes contain `message_id`, `correlation_id`, `causation_id`, `occurred_at`, and
`schema_version`. Business timestamps are timezone-aware UTC, enum values use uppercase snake case,
and the initial schema version is `1.0`.

When a message contract changes, update the executable model, contract documentation, routing map,
and producer/consumer contract tests together. Adding an optional field with a default may be
compatible within major version 1; removing, renaming, changing a type, or changing enum meaning is
a major-version change.

## LLM policy

- Prefer local deterministic processing.
- **Never add an LLM call to the Prediction Service.** Prediction is graph-only and makes zero LLM
  calls; the POC-6 controlled rerun recorded `STOP` for prediction-time arbitration. Only a new,
  explicitly approved controlled hypothesis can change this.
- Cleansing is the only service permitted to call the LLM, and only for ambiguous extraction, merge, or
  factual-conflict resolution.
- Keep provider and model selection in environment-backed settings.
- Enforce compact input/output budgets, bounded retries, structured-output validation, and cache
  identity based on provider, model, task, prompt version, schema, and context/input.
- Capture provider-reported token usage and latency without making an additional call.
- Do not log secrets, complete prompts, or full article bodies.

## Python conventions

- Python version: 3.12 or newer.
- Shared library project root: `src/shared/`; import package: `shared`.
- Use Pydantic v2 models and `pydantic-settings` for environment configuration.
- Keep public functions and classes fully typed. The project uses `mypy --strict`.
- Format and lint with Ruff using the settings in `src/shared/pyproject.toml`.
- Prefer immutable message/value models and explicit typed exceptions.
- Add comments only for non-obvious design constraints or reliability behavior.
- Preserve async cancellation and use graceful close/shutdown paths for external clients.

## Infrastructure changes

`infra/README.md` is the authority: the secrets rule, the environment-specific image and broker notes,
the seed-idempotency requirement, and the commands that validate a change. Two constraints matter most:

- **Never commit a credential, including a derived one such as a password hash.** Secrets live only in
  `infra/.env`.
- RabbitMQ definitions must match `requirements/SRS-01-shared-foundation.md` section 8.4 exactly.

## Testing and review

Test commands are in `src/README.md`. Use the fastest targeted test first, then the full shared suite.

- Unit tests must not require live infrastructure or provider credentials. Mark live RabbitMQ and
  real-provider checks as integration tests and skip them when required configuration is absent.
- Validate infrastructure changes with the commands in `infra/README.md` before claiming they work.

Review changes for contract alignment, canonical identifiers, idempotency, failure handling,
correlation propagation, secret safety, and backward compatibility. Do not broaden scope into deferred
epics while completing an earlier epic.

### Failing-test handoff policy

When a test fails, attempt a fix first. But if the investigation exceeds roughly **3 minutes of effort
or a comparable token budget without a clear resolution**, stop and hand the failure back to a human
with:

- the failing test name and file,
- the observed vs. expected behaviour,
- the most likely root cause, and
- a concrete suggested fix (without applying it).

Do not brute-force test failures. A fast, documented handoff is better than a long unfocused
investigation. This applies to every agent working in this repository.
