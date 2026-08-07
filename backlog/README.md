# backlog — Backlog tasks and POC documentation

This folder holds **backlog tasks — epics, stories, and tasks — for work that is not built yet**, plus
the POC documentation that justified the technical choices.

**E01–E07 are built.** Their specifications are in [requirements/](../requirements/README.md) — read the
SRS for those services, never a task file here. The remaining epic/story/task files under `E08` and
`E09` predate the specifications and are draft detail: where one conflicts with
[requirements/](../requirements/README.md), the requirements win.

## How this folder is organised

Work is nested three levels deep, each level with its own README:

```text
E08-API-Gateway-BFF/              epic   — README + functional-document.md
  S01-Core-REST-API-Endpoints/    story  — README lists its tasks
    T01-predictions-endpoints.md  task   — the implementable unit
```

| Level | Prefix | Contains |
|---|---|---|
| Epic | `E##` | One deliverable component. Its README gives the overview, architecture context, stories, and acceptance criteria |
| Story | `S##` | One coherent slice of an epic. Its README lists tasks, dependencies, and how to test end to end |
| Task | `T##` | A single implementable unit, as a `.md` file — not a folder |

[POC/](POC/README.md) does not follow that pattern: it holds research findings (`poc-1` … `poc-8`) plus
the one remediation epic `P06`, which does use the epic/story/task nesting.

## Live work

| Epic | Scope | Milestone | Status |
|---|---|---|---|
| [E08](E08-API-Gateway-BFF/README.md) | API Gateway / read-only BFF | M3 | Not started — design in [functional-document.md](E08-API-Gateway-BFF/functional-document.md) |
| [E09](E09-Dashboard-Web-UI/README.md) | Dashboard web UI | M3 | Not started — design in [functional-document.md](E09-Dashboard-Web-UI/functional-document.md) |
| [E10](E10-Cross-Asset-Propagation/README.md) | Cross-asset `CORRELATES_WITH` propagation | M4 | Not started — plan in [E10/README.md](E10-Cross-Asset-Propagation/README.md) |
| [E11](E11-Data-Retention/README.md) | Bounded growth/retention for unbounded tables | Unscheduled | Proposed; only Ingestion has a retention cleaner today |

E08 and E09 have no SRS by design: [requirements/](../requirements/README.md) specifies implemented
components only. An SRS-08 and SRS-09 get written when those services are built.

## Delivered epics

Built, and specified in `requirements/`:

| Epic | Component | Specification |
|---|---|---|
| E01 | Shared library and local infrastructure | [SRS-01](../requirements/SRS-01-shared-foundation.md) |
| E02 | Ingestion | [SRS-02](../requirements/SRS-02-ingestion.md) |
| E03 | Cleansing | [SRS-03](../requirements/SRS-03-cleansing.md) |
| E04 | Prediction | [SRS-04](../requirements/SRS-04-prediction.md) |
| E05 | Market Data | [SRS-05](../requirements/SRS-05-market-data.md) |
| E06 | Verification | [SRS-06](../requirements/SRS-06-verification.md) |
| E07 | Credibility | [SRS-07](../requirements/SRS-07-credibility.md) |
| — | Notification | [SRS-10](../requirements/SRS-10-notification.md) (`Approved`) |

## Milestones

| Milestone | Outcome | Status |
|---|---|---|
| M0 | Contract freeze and POC-6 | **Complete** — `STOP` for prediction-time LLM arbitration (2026-07-13) |
| M1 | Token-efficient walking skeleton, graph-only prediction | **Complete** — E01–E07 built end to end |
| M2 | Reliability and minimum security | **Largely delivered inside M1** — outbox, DLQs, recovery, graceful shutdown, SSRF controls are all `Implemented`; see [SyRS §6.1–6.2](../requirements/SyRS-system.md#61-reliability) |
| M3 | API and Dashboard | Not started — E08, E09 |
| M4 | Coverage expansion | **Partly delivered** — the multi-market registry (`multi-market-v2`) expanded from 2 to 37 assets; further sources still pending |

## POC research

| POC | Scope | Status |
|---|---|---|
| [POC findings index](POC/README.md) | All pre-development research: sources, cleansing, architecture, price data, tooling landscape | Complete |
| [P06](POC/P06-Validation-Remediation/README.md) | POC-6 dataset, market ground truth, and the controlled rerun | Complete; `STOP` for KG-plus-LLM arbitration |

Read the POC findings before changing a technical choice they justify — they explain decisions that
would otherwise look arbitrary.

## Deferred work

Recorded with reasons in [SyRS §13.4](../requirements/SyRS-system.md#134-deferred-work):

- Formal FR/NFR/BR traceability — superseded by the requirement IDs in the specifications.
- Full monitoring, dashboards, and distributed tracing.
- Load and soak testing.
- Public API authentication and rate limiting.
- Automated backup, restore, and deletion mechanics.
- Prediction-time LLM arbitration, pending a new approved controlled hypothesis.

## Local development setup

See the repository [README](../README.md#development-environment) — one root virtual environment serves
all work, and [scripts/](../scripts/README.md) holds the setup scripts.
