# SRS-nn: <Component Name>

> Copy this file to start a new specification. Delete every `<...>` placeholder and this quote block.
> Keep all 15 sections and their order — readers rely on the fixed structure. If a section does not
> apply, keep the heading and write "Not applicable — <reason>" rather than deleting it.

## 1. Document control

| | |
|---|---|
| Document ID | `SRS-nn` |
| Component | `<service name>` |
| Requirement ID prefix | `<XXX>` |
| Status | `Implemented` / `Approved` / `Proposed` |
| Version | `1.0.0` |
| Source code | [`<path>`](../<path>) |
| Tests | [`<path>`](../<path>) |
| Last verified against code | `<YYYY-MM-DD>` |

## 2. Purpose and scope

### 2.1 What this component does

<One paragraph: the component's single responsibility in plain language.>

### 2.2 In scope

- <capability>

### 2.3 Explicitly out of scope

- <thing it must NOT do, and which component does it instead>

## 3. Definitions

| Term | Meaning |
|---|---|
| <term> | <definition> |

## 4. System context

### 4.1 Position in the pipeline

```text
<upstream> --<routing key>--> [ THIS COMPONENT ] --<routing key>--> <downstream>
```

### 4.2 Dependencies

| Dependency | Purpose | Failure impact |
|---|---|---|
| <PostgreSQL schema / RabbitMQ / external API> | <why> | <what breaks> |

## 5. Functional requirements

| ID | Requirement | Priority | Status |
|---|---|---|---|
| `XXX-1` | The <component> **shall** <observable behaviour>. | Must | Implemented |

## 6. Non-functional requirements

| ID | Requirement | Priority | Status |
|---|---|---|---|
| `XXX-n` | The <component> **shall** <quality attribute>. | Must | Implemented |

## 7. How it works

### 7.1 <Capability name>

**Purpose:** <one line>

**Steps:**

1. <step> → [`file.py`](../<path>/file.py)
2. <step>

**Rules:**

- <rule or edge case>

## 8. Interfaces

### 8.1 Messages consumed

| Queue | Routing key | Message |
|---|---|---|
| `<queue>` | `<key>` | `<Type>` |

### 8.2 Messages published

| Routing key | Message | When |
|---|---|---|
| `<key>` | `<Type>` | <trigger> |

### 8.3 HTTP endpoints

| Method | Path | Purpose | Response |
|---|---|---|---|
| `GET` | `/health` | Liveness | `200` |

### 8.4 Scheduled jobs

| Job | Interval | Purpose |
|---|---|---|
| `<name>` | `<config key>` (default `<n>`) | <what it does> |

## 9. Data design

### 9.1 Owned schema

`<schema_name>` in the `feed` database. DDL: [`db.py`](../<path>/db.py)

### 9.2 Table: `<schema>.<table>`

<Purpose sentence.>

| Column | Type | Notes |
|---|---|---|
| `<col>` | `<TYPE>` | <constraint / meaning> |

**Indexes / constraints:**

- <index and why it exists>

## 10. Configuration

Environment variables. Prefix: `<PREFIX>_`.

| Variable | Default | Effect |
|---|---|---|
| `<VAR>` | `<default>` | <what changes if you alter it> |

## 11. Verification

| Requirement | Method | Evidence |
|---|---|---|
| `XXX-1` | Test | [`test_x.py`](../<path>/tests/test_x.py) — <test name or description> |

## 12. Failure handling

| Failure | Behaviour | Recovery |
|---|---|---|
| <dependency down> | <what happens> | <how it recovers> |

## 13. Assumptions, dependencies, and known limitations

### 13.1 Assumptions

- <assumption>

### 13.2 Known limitations

- <limitation and its consequence>

## 14. How to update this document

Follow the rules in [README.md](README.md#how-to-update-these-documents).

Component-specific notes:

- <anything that must be updated together with this document>

## 15. Change history

| Date | Version | Change | Driver |
|---|---|---|---|
| `<YYYY-MM-DD>` | `1.0.0` | Initial specification | <reason> |
