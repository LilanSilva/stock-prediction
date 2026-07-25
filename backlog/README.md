# Backlog and Milestones

## Local development setup

All epics share **one root virtual environment** (`.venv/`) and the single editable `shared`
package. Set it up once with the repository script (do not create per-service or machine-level
environments):

- Windows: `powershell -ExecutionPolicy Bypass -File scripts\setup-venv.ps1`
- macOS/Linux: `bash scripts/setup-venv.sh`

See the repository [`README.md`](../README.md) (*Development environment* and *Local infrastructure*)
for interpreter selection, the hash-pinned lockfile, secrets handling, and Docker stack commands.

## Execution policy

Existing implementation tasks are retained until the contract-freeze work is complete. They are draft detail and must be read through [contract-freeze-overrides.md](contract-freeze-overrides.md); conflicting child-task text must not be implemented. Oversized tasks will be re-sliced afterward so the backlog is not rewritten twice.

## Milestones

| Milestone | Outcome | Exit condition | Status |
|---|---|---|---|
| M0 | Contract freeze and POC-6 | Frozen conflict corpus, approved reference-price policy, and controlled rerun decision | `STOP` for LLM arbitration; graph-only M1 may proceed after contract freeze |
| M1 | Token-efficient walking skeleton | GOLD and BRENT_OIL flow end-to-end with one multi-event conflict | Scope changed to graph-only prediction |
| M2 | Reliability and minimum security | Recovery, DLQ replay, graceful shutdown, SSRF controls | Blocked by M1 |
| M3 | API and Dashboard | Read-only BFF and UI consume the stable read model | Blocked by M2 |
| M4 | Coverage expansion | Additional sources/assets validated and registered | Blocked by M3 |

## Contract-freeze work

Before existing epic implementation:

1. Implement the multi-event context design in E04 specifications.
2. Align all tasks to `docs/contracts/message-contracts.md`.
3. Replace provider symbols with canonical asset IDs at service boundaries.
4. Align PostgreSQL schema ownership and RabbitMQ bindings.
5. Align Verification and Market Data around one dual-session price request.
6. Align credibility learning and idempotency.
7. Apply the POC-6 `STOP` finding: keep prediction-time LLM arbitration out of M1 unless a new controlled hypothesis is approved.

## Epic index

| Epic | Scope | Milestone | Prerequisite |
|---|---|---|---|
| E01 | Shared infrastructure and contracts | M1 | Contract freeze |
| E02 | Ingestion | M1 | E01 messaging and database schemas |
| E03 | Cleansing | M1 | Taxonomy, cluster state machine, token policy |
| E04 | Prediction | M1 | Multi-event context and canonical registry |
| E05 | Market Data | M1 | Asset registry and dual-session contract |
| E06 | Verification | M1 | Scoring and price-session contract |
| E07 | Credibility | M1 | Idempotent separated learning targets |
| E08 | API Gateway/BFF | M3 | Stable read schema and exchange fan-out |
| E09 | Dashboard | M3 | Stable API contracts |

## POC status

| POC | Scope | Milestone | Status |
|---|---|---|---|
| [P06](POC/P06-Validation-Remediation/README.md) | POC-6 dataset, market ground truth, and controlled rerun | M0 | Complete; `STOP` for KG-plus-LLM arbitration |

## POC walking-skeleton scope

- Sources: one validated RSS source initially; add a global source only after its availability/corpus path passes P06.
- Assets: `GOLD`, `BRENT_OIL`.
- Horizon: `ONE_TRADING_DAY`.
- Prediction: graph-only for M1; prediction-time LLM arbitration is deferred by POC-6.
- UI: no full Dashboard in M1; a minimal API or CLI result is sufficient.
- Reliability: persistent price work, duplicate-safe scoring, and duplicate-safe learning.

## Deferred work

- Formal FR/NFR/BR traceability.
- Full monitoring and distributed tracing.
- Load and soak testing.
- Public API authentication/rate limiting.
- Automated backup/restore and deletion mechanics.
- Additional asset classes and provider fallbacks.
