# E09 - Dashboard Web UI

> Contract-freeze status: this epic and its child tasks are governed by the [backlog override matrix](../contract-freeze-overrides.md). Conflicting legacy details are non-authoritative until re-slicing.

## Overview

This epic delivers the React-based dashboard that surfaces the system's prediction pipeline output to end users. It covers two areas: a live predictions feed with filtering and drill-down detail, and a suite of accuracy and credibility charts that let users evaluate how well the system is performing over time.

The dashboard is a synchronous foreground component that communicates exclusively with the **API Gateway (service 7)** via REST and WebSocket. It never touches RabbitMQ, Postgres, or Neo4j directly.

---

## Stories

| Story | Description |
|---|---|
| **S01 - Predictions Table & Live Feed** | Main predictions table with filtering, status indicators, prediction detail drawer, and live WebSocket updates |
| **S02 - Accuracy & Credibility Charts** | Rolling accuracy line chart, edge credibility bar chart, source credibility table, and interactive knowledge graph visualizer |

---

## Architecture Context

```
User
 │
 ▼
dashboard/          ← React + Recharts + React Flow (this epic)
 │  HTTP REST
 │  WebSocket
 ▼
services/api-gateway/   ← FastAPI BFF (E08)
 │
 ├── reads: Predictions DB (Postgres) → GET /predictions, /predictions/stats
 ├── reads: Outcomes DB (Postgres)    → scoring data joined to predictions
 ├── reads: Credibility DB (Postgres) → GET /credibility/edges, /credibility/sources
 ├── reads: Events DB (Postgres)      → source articles per prediction
 ├── reads: Knowledge Graph (Neo4j)   → GET /graph/assets/{symbol}
 └── pushes: WebSocket                → PredictionMade events to connected clients
```

### Key API endpoints consumed by this epic

| Endpoint | Used by |
|---|---|
| `GET /predictions` | S01-T01 predictions table |
| `GET /predictions/{id}` | S01-T02 prediction detail drawer |
| `GET /predictions/stats` | S02-T01 accuracy chart |
| `GET /credibility/edges` | S02-T02 edge credibility chart |
| `GET /credibility/sources` | S02-T02 source credibility table |
| `GET /graph/assets/{symbol}` | S02-T03 knowledge graph visualizer |
| `WS /ws/predictions` | S01-T01 live feed |

### Frontend tech stack

| Concern | Library |
|---|---|
| Framework | React 18 + TypeScript |
| Build tool | Vite |
| Charts | Recharts 2.x |
| Graph visualization | React Flow 11.x or Cytoscape.js 3.x |
| Styling | Tailwind CSS 3.x |
| HTTP client | Axios or native fetch with React Query (`@tanstack/react-query`) |
| WebSocket | native browser WebSocket API |
| State management | React Context + `useReducer` (no Redux needed) |
| Testing | Vitest + React Testing Library |
| Linting | ESLint + Prettier |

---

## Overall Acceptance Criteria

1. Dashboard loads at `http://localhost:3000` and renders without console errors.
2. Predictions table shows all columns: timestamp, asset, direction, magnitude, confidence, status, actual direction, actual return %.
3. A new prediction pushed via WebSocket appears at the top of the table within 2 seconds without a page refresh.
4. Clicking a prediction row opens the detail drawer with rationale, contributing edges, and source articles.
5. Accuracy chart renders one line per asset with rolling 7-day accuracy %.
6. Edge credibility chart renders top 20 edges with 95% CI error bars, color-coded by score.
7. Source credibility table is ranked by score and includes hit/miss counts and trend arrow.
8. Knowledge graph visualizer renders nodes and edges fetched from the API; clicking an edge shows a credibility history sparkline.
9. All components handle loading states, empty states, and API error states gracefully.
10. `npm run build` produces a production bundle with no TypeScript errors.
11. `npm test` passes all unit tests.
12. `npm run lint` passes with zero errors.
