# T01: Predictions Table Component

## Context

This task builds the main predictions table in the React dashboard (`dashboard/` directory). It is the first thing a user sees when opening the application. The table fetches paginated prediction data from the API Gateway and receives live updates via WebSocket so new predictions appear without a page refresh. This is the entry point to the entire dashboard experience.

## Background

The API Gateway (service 7, `services/api-gateway/`) exposes:
- `GET /predictions` - paginated list of predictions with optional filter params
- `WS /ws/predictions` - WebSocket that pushes `PredictionMade` events whenever the Prediction Service publishes to the `predictions` RabbitMQ queue

Each prediction record has the following fields derived from the `PredictionMade` message schema plus scoring outcome joined from the Outcomes DB:

```
prediction_id   string    UUID
created_at      string    ISO-8601 timestamp
asset           string    e.g. "GOLD", "OIL", "OMXS30"
direction       string    "UP" | "DOWN" | "NEUTRAL"
magnitude_bucket string   e.g. "SMALL" | "MEDIUM" | "LARGE"
confidence      number    0.0 – 1.0
status          string    "PENDING" | "CORRECT" | "WRONG"
actual_direction string | null  "UP" | "DOWN" | "NEUTRAL" or null when PENDING
actual_return_pct number | null  e.g. 1.23 or null when PENDING
```

The `GET /predictions` endpoint accepts these query params:
- `page` (integer, default 1)
- `page_size` (integer, default 20)
- `asset` (string, optional comma-separated: "GOLD,OIL")
- `status` (string, optional comma-separated: "CORRECT,WRONG,PENDING")
- `date_from` (string, optional ISO-8601 date)
- `date_to` (string, optional ISO-8601 date)
- `sort_by` (string, optional: "created_at" | "confidence" | "asset")
- `sort_dir` (string, optional: "asc" | "desc", default "desc")

Response shape:
```json
{
  "items": [ /* array of prediction objects */ ],
  "total": 142,
  "page": 1,
  "page_size": 20
}
```

The WebSocket at `WS /ws/predictions` sends JSON messages of the shape:
```json
{
  "type": "PredictionMade",
  "data": { /* same prediction object shape as items above, status always PENDING */ }
}
```

## Inputs

- REST: `GET /predictions` with filter/sort/pagination query params
- WebSocket: `WS /ws/predictions` → JSON message with `type: "PredictionMade"` and `data` payload
- User interactions: filter bar inputs, column header clicks (sort), pagination controls

## Outputs

- Rendered predictions table in the browser
- No data is written; this is a read-only display component

## Technical Requirements

### Project scaffold

If `dashboard/` does not yet exist as a Vite project, initialize it:
```bash
npm create vite@latest dashboard -- --template react-ts
cd dashboard
npm install
npm install @tanstack/react-query axios recharts tailwindcss postcss autoprefixer
npx tailwindcss init -p
```

File structure for this task:
```
dashboard/
  src/
    components/
      predictions/
        PredictionsTable.tsx      ← main table component
        PredictionsFilterBar.tsx  ← filter controls
        PredictionRow.tsx         ← single row
        DirectionBadge.tsx        ← colored UP/DOWN/NEUTRAL badge
        StatusBadge.tsx           ← colored PENDING/CORRECT/WRONG badge
    hooks/
      usePredictions.ts           ← React Query hook for GET /predictions
      usePredictionsWebSocket.ts  ← WebSocket hook
    types/
      prediction.ts               ← TypeScript interfaces
    pages/
      DashboardPage.tsx           ← top-level page that composes all parts
```

### TypeScript interfaces (`src/types/prediction.ts`)

```typescript
export type Direction = 'UP' | 'DOWN' | 'NEUTRAL';
export type Status = 'PENDING' | 'CORRECT' | 'WRONG';
export type MagnitudeBucket = 'SMALL' | 'MEDIUM' | 'LARGE';

export interface Prediction {
  prediction_id: string;
  created_at: string;  // ISO-8601
  asset: string;
  direction: Direction;
  magnitude_bucket: MagnitudeBucket;
  confidence: number;  // 0-1
  status: Status;
  actual_direction: Direction | null;
  actual_return_pct: number | null;
}

export interface PredictionsPage {
  items: Prediction[];
  total: number;
  page: number;
  page_size: number;
}

export interface PredictionsFilter {
  asset?: string[];
  status?: Status[];
  date_from?: string;
  date_to?: string;
}

export interface PredictionsSort {
  sort_by: 'created_at' | 'confidence' | 'asset';
  sort_dir: 'asc' | 'desc';
}
```

### React Query hook (`src/hooks/usePredictions.ts`)

Use `@tanstack/react-query` `useQuery`. Cache key must include all active filter + sort + page values so changing any param triggers a fresh fetch. Set `staleTime: 30_000` (30 seconds). API base URL from `import.meta.env.VITE_API_URL` (default `http://localhost:8000`).

### WebSocket hook (`src/hooks/usePredictionsWebSocket.ts`)

- Connect to `WS_URL/ws/predictions` (derive from `VITE_API_URL` by replacing `http` with `ws`).
- On message received: parse JSON, check `type === 'PredictionMade'`, call a provided `onNewPrediction(prediction: Prediction)` callback.
- Implement reconnection: on `onclose` or `onerror`, wait 3 seconds then reconnect. Use a `useRef` for the socket and a `useEffect` cleanup to close it on unmount.
- Export hook signature: `usePredictionsWebSocket(onNewPrediction: (p: Prediction) => void): { connected: boolean }`

### PredictionsTable component (`src/components/predictions/PredictionsTable.tsx`)

Table columns (in order):
1. **Timestamp** - formatted as `YYYY-MM-DD HH:mm` in the user's local timezone using `Intl.DateTimeFormat`
2. **Asset** - plain text, bold
3. **Direction** - use `DirectionBadge` component: UP=green background + up arrow (▲), DOWN=red background + down arrow (▼), NEUTRAL=grey background + dash (─)
4. **Magnitude** - SMALL/MEDIUM/LARGE in muted text
5. **Confidence** - displayed as percentage, e.g. `72%`. Color-code: ≥70% green text, 50-69% amber, <50% red
6. **Status** - use `StatusBadge`: PENDING=grey pill, CORRECT=green pill with ✓, WRONG=red pill with ✗
7. **Actual Direction** - same `DirectionBadge` or `—` if null
8. **Actual Return** - e.g. `+1.23%` (green if positive, red if negative) or `—` if null

Column headers for Timestamp, Asset, and Confidence are clickable for sort (toggle asc/desc). Show sort indicator arrow next to active sort column.

New rows received from WebSocket are prepended to the visible list with a brief yellow highlight animation (CSS keyframe, 2 seconds, fade from `#fef9c3` to transparent). Use `useReducer` in `DashboardPage.tsx` to merge WebSocket rows into the query result.

Table must show a skeleton loader (grey pulsing rows, Tailwind `animate-pulse`) while the initial fetch is in-flight.

Empty state: if `total === 0`, show a centered message "No predictions found. Adjust your filters."

### PredictionsFilterBar component (`src/components/predictions/PredictionsFilterBar.tsx`)

Controls:
- **Date range**: two `<input type="date">` inputs labeled "From" and "To"
- **Asset dropdown**: multi-select using a `<select multiple>` or a custom checkbox dropdown. Options: ALL (default), GOLD, OIL, OMXS30. Allow selecting multiple.
- **Status multi-select**: checkboxes for PENDING, CORRECT, WRONG. Default: all selected.
- **Clear filters** button: resets all filters to defaults.

Debounce text inputs by 400 ms using a `useDebounce` custom hook before updating query params. Dropdowns/checkboxes apply immediately.

### Pagination

Render pagination controls below the table: Previous / page numbers / Next. Show current range, e.g. "Showing 21–40 of 142". Clicking page number updates the `page` param. When filters change, reset `page` to 1.

### Environment variables

Create `dashboard/.env.example`:
```
VITE_API_URL=http://localhost:8000
VITE_WS_URL=ws://localhost:8000
```

Create `dashboard/.env.local` with the same defaults for local dev.

### Vite proxy config (`dashboard/vite.config.ts`)

Configure a proxy so `/api` requests in dev mode forward to `http://localhost:8000` to avoid CORS issues:
```typescript
server: {
  proxy: {
    '/api': 'http://localhost:8000',
    '/ws': { target: 'ws://localhost:8000', ws: true }
  }
}
```

## Acceptance Criteria

1. Navigating to `http://localhost:3000` renders the predictions table without console errors.
2. Table displays all 8 columns: timestamp, asset, direction badge, magnitude, confidence %, status badge, actual direction badge, actual return %.
3. Confidence ≥ 70% renders in green text; 50–69% in amber; < 50% in red.
4. Direction=UP renders a green badge with ▲; DOWN renders red badge with ▼; NEUTRAL renders grey badge with ─.
5. Status=PENDING renders grey pill; CORRECT renders green pill with ✓; WRONG renders red pill with ✗.
6. Clicking "Confidence" column header sorts ascending on first click, descending on second.
7. Clicking "Timestamp" column header changes sort direction; sort indicator arrow updates.
8. Selecting Asset=GOLD in the filter bar re-fetches and shows only GOLD predictions.
9. Selecting Status=CORRECT filters table to only CORRECT rows.
10. Clicking "Clear filters" resets all filters and reloads the default view.
11. Navigating to page 2 fetches the next 20 rows (query param `page=2`).
12. Changing any filter resets to page 1.
13. A grey pulse skeleton is shown while the initial fetch is loading.
14. When `total === 0` after filtering, the message "No predictions found. Adjust your filters." is displayed.
15. A new `PredictionMade` WebSocket message causes a new row to appear at the top of the table within 2 seconds without a page refresh.
16. The new WebSocket row has a yellow highlight that fades out over 2 seconds.
17. If the WebSocket disconnects, it reconnects automatically within 3 seconds.
18. `npm run build` completes with zero TypeScript errors.
19. `npm test` passes all unit tests for this component.
20. `npm run lint` passes with zero errors.

## Implementation Notes

- Do NOT use Redux. Use React Query for server state and `useReducer` in `DashboardPage` for merging WebSocket items into the displayed list.
- When prepending WebSocket rows: only prepend if the current page is 1 and no filters are active (i.e., the user is viewing the live "all predictions" view). If filters are active, silently discard WebSocket events (they will appear naturally on the next poll refresh).
- The `actual_return_pct` field can be negative. Format with explicit sign: `+1.23%` or `-0.45%`. Use `toLocaleString` with `signDisplay: 'always'`.
- Timestamps from the API are UTC. Convert to local time for display using `Intl.DateTimeFormat(undefined, { dateStyle: 'short', timeStyle: 'short' })`.
- Tailwind CSS dark mode: implement with `class` strategy. Add a toggle button in the header for light/dark mode, stored in `localStorage`.
- Do not hardcode asset names in filter options. Fetch available assets from `GET /predictions/assets` if that endpoint exists; otherwise use the static list [GOLD, OIL, OMXS30].
- React Query's `keepPreviousData: true` option (v4) or `placeholderData: keepPreviousData` (v5) prevents the table from blanking out between page navigations.

## Definition of Done

- [ ] `dashboard/src/types/prediction.ts` defines all TypeScript interfaces
- [ ] `dashboard/src/hooks/usePredictions.ts` fetches from `GET /predictions` using React Query
- [ ] `dashboard/src/hooks/usePredictionsWebSocket.ts` connects, reconnects, and calls `onNewPrediction` callback
- [ ] `PredictionsFilterBar.tsx` renders date range, asset, and status controls; "clear" resets all
- [ ] `PredictionsTable.tsx` renders all 8 columns with correct formatting and color-coding
- [ ] Sort by clicking column headers works and shows visual indicator
- [ ] Pagination controls render and navigate correctly
- [ ] WebSocket new rows appear at top with yellow flash animation
- [ ] Skeleton loader visible during initial fetch
- [ ] Empty state message shown when no results
- [ ] `dashboard/.env.example` created
- [ ] Unit tests written for `DirectionBadge`, `StatusBadge`, `PredictionsFilterBar` using Vitest + React Testing Library
- [ ] `npm run build` passes with no TypeScript errors
- [ ] `npm run lint` passes
- [ ] `npm test` passes
