# S01 - Predictions Table & Live Feed

## Overview

This story builds the primary user-facing surface: a paginated, filterable predictions table that receives live updates via WebSocket, plus a slide-in detail drawer that shows the full context behind any individual prediction.

Users land on the dashboard to answer: "What did the system predict, and was it right?" The table is the answer to that question at a glance. The drawer is the answer in depth.

---

## Tasks

| Task | Description |
|---|---|
| **T01 - Predictions table component** | Paginated table with sort/filter, colored direction indicators, live WebSocket row insertion |
| **T02 - Prediction detail drawer** | Side drawer showing prediction rationale, contributing edges, source articles, and scored comparison chart |

---

## Dependencies

Before this story can be started, the following must exist:

- **E08 API Gateway** - the following endpoints must be implemented and reachable:
  - `GET /predictions` - paginated list with filter params
  - `GET /predictions/{id}` - single prediction full detail
  - `WS /ws/predictions` - WebSocket broadcasting `PredictionMade` events
- **Vite + React + TypeScript** project scaffold in `dashboard/` with Tailwind CSS configured (can be created as part of T01 if not already done).
- Docker Compose entry for the dashboard service exposing port 3000.

---

## How to Test End-to-End

1. Start the full stack: `docker compose up`
2. Navigate to `http://localhost:3000`
3. Verify the predictions table renders rows fetched from the API.
4. Apply a filter (e.g. Asset = GOLD) and verify the table updates.
5. Sort by confidence descending and verify the order changes.
6. Navigate to page 2 and verify different rows appear.
7. In a second terminal, publish a test `PredictionMade` message to the `predictions` RabbitMQ queue. Verify a new row appears at the top of the table within 2 seconds without refreshing.
8. Click any row and verify the detail drawer slides in with rationale text, contributing edges, and source articles.
9. If the prediction is scored, verify the actual vs predicted comparison is shown in the drawer.
