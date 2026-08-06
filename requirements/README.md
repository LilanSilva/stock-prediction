# requirements — Requirement documents and functional knowledge

This folder holds **all requirement documents and the system's functional knowledge.** It is the
**primary specification** of Feed Analyzer: what the system must do, how each service works internally,
and which test proves each requirement.

Naming tells you a document's kind: `SyRS` is the system, `SRS-##` a component, `REF-##` reference data,
`ADR` the decision records. Every `SyRS`/`SRS` document uses the same 15 sections, so you always know
where to look.

> **Who this is for.** A developer or coding agent should be able to read one document here and then
> safely change that service — knowing its purpose, its message contracts, its database tables,
> every configuration key, every scheduled job, every API endpoint, and which existing behaviour a
> change must not break.

## Documents

| Document | Component | Source code |
|---|---|---|
| [SyRS-system.md](SyRS-system.md) | Whole system: topology, messaging, cross-cutting rules | — |
| [SRS-01-shared-foundation.md](SRS-01-shared-foundation.md) | Shared library and local infrastructure | [src/shared/](../src/shared/), [infra/](../infra/) |
| [SRS-02-ingestion.md](SRS-02-ingestion.md) | Ingestion Service | [src/services/ingestion/](../src/services/ingestion/) |
| [SRS-03-cleansing.md](SRS-03-cleansing.md) | Cleansing Service | [src/services/cleansing/](../src/services/cleansing/) |
| [SRS-04-prediction.md](SRS-04-prediction.md) | Prediction Service | [src/services/prediction/](../src/services/prediction/) |
| [SRS-05-market-data.md](SRS-05-market-data.md) | Market Data Service | [src/services/market-data/](../src/services/market-data/) |
| [SRS-06-verification.md](SRS-06-verification.md) | Verification Service | [src/services/verification/](../src/services/verification/) |
| [SRS-07-credibility.md](SRS-07-credibility.md) | Credibility Service | [src/services/credibility/](../src/services/credibility/) |
| [SRS-10-notification.md](SRS-10-notification.md) | Notification Service | [src/services/notification/](../src/services/notification/) |

Not yet built: API Gateway and Dashboard. Those remain in [backlog/](../backlog/) as E08 and E09;
no SRS exists for them until they are implemented.

### Reference data and decisions

These are not specifications — they contain no `shall` statements — but the specifications depend on
them:

| Document | Contains |
|---|---|
| [REF-01-event-taxonomy.md](REF-01-event-taxonomy.md) | The 32 canonical event types and multilingual normalization rules |
| [REF-02-asset-registry.md](REF-02-asset-registry.md) | Asset registry file shape, provider routing, session calendars, registry version history |
| [ADR-decisions.md](ADR-decisions.md) | ADR-001…007 — why the system is shaped this way |

## Reading order

**New to the system?** Read in this order:

1. [SyRS-system.md](SyRS-system.md) — what the system does and how the seven components fit together.
2. [SRS-01-shared-foundation.md](SRS-01-shared-foundation.md) — the message contracts and shared
   library that every service depends on.
3. The SRS for the service you are changing.

**Changing one service?** Read its SRS section 5 (requirements) and section 7 (how it works), then
its section 11 (verification) to see which tests protect the existing behaviour.

## Document structure

Every SRS uses the same 15 sections, so you always know where to look:

| Section | Contains |
|---|---|
| 1. Document control | ID, status, version, last verified |
| 2. Purpose and scope | What the component does and explicitly does not do |
| 3. Definitions | Terms used in this document |
| 4. System context | Where it sits, what it talks to |
| 5. Functional requirements | Numbered `shall` statements — the contract |
| 6. Non-functional requirements | Reliability, security, performance, observability |
| 7. How it works | Step-by-step mechanism for every capability |
| 8. Interfaces | Messages consumed/published, HTTP endpoints, field by field |
| 9. Data design | Every table, column, index, and constraint |
| 10. Configuration | Every environment variable, default, and effect |
| 11. Verification | Requirement ID → test that proves it |
| 12. Failure handling | What happens when each dependency fails |
| 13. Assumptions, dependencies, and known limitations | |
| 14. How to update this document | Change rules |
| 15. Change history | |

## Requirement IDs

Each component owns an ID prefix. IDs are permanent: a retired requirement is marked `Withdrawn`
and its number is never reused.

| Prefix | Component |
|---|---|
| `SYS` | System-wide |
| `SHR` | Shared library and infrastructure |
| `ING` | Ingestion |
| `CLN` | Cleansing |
| `PRD` | Prediction |
| `MKT` | Market Data |
| `VER` | Verification |
| `CRD` | Credibility |
| `NTF` | Notification |

## Conventions

**Requirement wording** — every requirement is one testable statement using `shall`:

> `ING-9` — The Ingestion Service **shall** reject a fetch target that resolves to a loopback,
> private, link-local, multicast, reserved, or unspecified IP address.

- `shall` = binding requirement. Nothing else is binding.
- One requirement per row. No "and also" clauses.
- States observable behaviour, not code structure.

**Status values**

| Status | Meaning |
|---|---|
| `Implemented` | Built and proven by a test named in section 11 |
| `Approved` | Agreed but not yet built |
| `Proposed` | Under discussion |
| `Deferred` | Agreed but intentionally postponed, with a reason |
| `Withdrawn` | No longer required; kept for history |

**Priority values** — `Must`, `Should`, `Could`.

**Verification methods**

| Method | Meaning |
|---|---|
| `Test` | An automated test proves it |
| `Demonstration` | Observable by running the system |
| `Inspection` | Proven by reading code or config |
| `Analysis` | Proven by reasoning over design or data |

## Authority

When two sources disagree, the higher one wins:

1. **Executable Pydantic models** in [src/shared/shared/schemas/](../src/shared/shared/schemas/) and
   [assets.json](../src/shared/shared/reference/assets.json) — the running contract.
2. **These specifications** — [SyRS-system.md](SyRS-system.md) and the SRS documents.
3. [REF-01-event-taxonomy.md](REF-01-event-taxonomy.md) and
   [REF-02-asset-registry.md](REF-02-asset-registry.md) — reference data.
4. [ADR-decisions.md](ADR-decisions.md) — accepted decisions and their reasoning.
5. [docs/architectural-documents/](../docs/architectural-documents/) — diagrams.
6. [backlog/](../backlog/) — unbuilt work only (E08, E09).

If code and a specification disagree, **the code is the truth and the specification is a defect** —
fix the specification, or fix the code if the specification describes agreed intent.

## How to update these documents

**When to update** — any change to observable behaviour, a message contract, a database table, a
configuration key, an endpoint, or a scheduled job. A pure refactor with no behaviour change needs
no update.

**Steps**

1. Change or add the requirement in section 5 or 6 of the relevant SRS.
   - New requirement → next free number for that prefix (`ING-24`, never `ING-9a`).
   - Retired requirement → set status `Withdrawn` and add the reason. Never delete, never renumber.
2. Update section 7 so the mechanism description matches the new behaviour.
3. Update sections 8, 9, and 10 if interfaces, tables, or configuration changed.
4. Add the proving test to section 11. **A requirement with no test is `Approved`, not
   `Implemented`.**
5. If the change crosses services, update [SyRS-system.md](SyRS-system.md) too.
6. Bump the version in section 1:
   - patch (`1.0.1`) — wording, typo, clarification
   - minor (`1.1.0`) — requirement added
   - major (`2.0.0`) — requirement changed or withdrawn
7. Add a row to section 15.

**Adding a new service** — copy [TEMPLATE.md](TEMPLATE.md), claim a new ID prefix in the table
above, add the file to the Documents table, and add its component row to
[SyRS-system.md](SyRS-system.md).
