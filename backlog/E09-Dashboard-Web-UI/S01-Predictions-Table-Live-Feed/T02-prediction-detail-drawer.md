# T02: Prediction Detail Drawer

## Context

This task builds the prediction detail drawer — a panel that slides in from the right side of the screen when a user clicks any row in the predictions table (built in T01). The drawer gives users the full context behind a prediction: the prediction rationale, which knowledge graph edges contributed, which source articles triggered the event, and (if already scored) a visual comparison of predicted vs actual outcome. This is the "why" behind every prediction row.

## Background

The API Gateway endpoint `GET /predictions/{prediction_id}` returns a richer payload than the list endpoint. It joins data from multiple Postgres tables:

- Prediction record (from Predictions DB)
- Contributing causal edges with their current credibility weights (from Credibility DB via Neo4j)
- Source articles that fed the underlying event (from Events DB / RawNews DB)
- Scoring outcome if prediction has been evaluated (from Outcomes DB)

Expected response shape from `GET /predictions/{id}`:
```json
{
  "prediction_id": "uuid",
  "created_at": "2024-01-15T14:30:00Z",
  "asset": "GOLD",
  "direction": "UP",
  "magnitude_bucket": "MEDIUM",
  "confidence": 0.72,
  "time_horizon": "24h",
  "status": "CORRECT",
  "actual_direction": "UP",
  "actual_return_pct": 1.23,
  "rationale": "Graph-only policy identified geopolitical tension as the dominant force...",
  "contributing_edges": [
    {
      "edge_id": "war->gold",
      "from_node": "war",
      "to_node": "GOLD",
      "relationship": "CAUSES_UP",
      "current_weight": 0.71,
      "alpha": 15.2,
      "beta": 6.1
    }
  ],
  "sources": [
    {
      "article_id": "uuid",
      "domain": "dn.se",
      "title": "Riksbanken höjer räntan",
      "url": "https://dn.se/...",
      "published_at": "2024-01-15T12:00:00Z",
      "credibility_score": 0.68
    }
  ]
}
```

The drawer is triggered by user interaction (row click) from the `PredictionsTable` component (T01). It must not block or replace the table — it overlays it as a side panel.

## Inputs

- `prediction_id: string` passed as a prop when the parent opens the drawer
- REST: `GET /predictions/{prediction_id}` fetched when drawer opens
- User click on a table row (event originates in `PredictionsTable.tsx` from T01)

## Outputs

- Rendered side drawer with all detail sections
- No data written; this is a read-only display component

## Technical Requirements

### File structure

```
dashboard/
  src/
    components/
      predictions/
        PredictionDrawer.tsx          ← main drawer shell
        PredictionDrawerContent.tsx   ← content when data is loaded
        ContributingEdgesList.tsx     ← list of causal edges
        SourceArticlesList.tsx        ← list of source articles
        ScoredComparisonChart.tsx     ← actual vs predicted bar chart (Recharts)
        ConfidenceCalibrationBadge.tsx ← calibration indicator
    hooks/
      usePredictionDetail.ts          ← React Query hook for GET /predictions/{id}
    types/
      prediction.ts                   ← extend with PredictionDetail interface (from T01 file)
```

### TypeScript interfaces (extend `src/types/prediction.ts`)

```typescript
export interface ContributingEdge {
  edge_id: string;
  from_node: string;
  to_node: string;
  relationship: string;
  current_weight: number;  // 0-1, credibility score
  alpha: number;           // Beta-Bernoulli alpha parameter
  beta: number;            // Beta-Bernoulli beta parameter
}

export interface SourceArticle {
  article_id: string;
  domain: string;
  title: string;
  url: string;
  published_at: string;       // ISO-8601
  credibility_score: number;  // 0-1
}

export interface PredictionDetail extends Prediction {
  time_horizon: string;
  rationale: string;
  contributing_edges: ContributingEdge[];
  sources: SourceArticle[];
}
```

### React Query hook (`src/hooks/usePredictionDetail.ts`)

Use `useQuery` with query key `['prediction', predictionId]`. Only fetch when `predictionId` is non-null (use `enabled: predictionId != null`). Set `staleTime: 60_000`. On error, expose `error` so the drawer can show an error state.

### PredictionDrawer component (`src/components/predictions/PredictionDrawer.tsx`)

Props:
```typescript
interface PredictionDrawerProps {
  predictionId: string | null;  // null = closed
  onClose: () => void;
}
```

- When `predictionId` is `null`, the drawer is not rendered (return null or use CSS `translate-x-full` to hide).
- Animate open/close with a CSS transition: `transform: translateX(0)` when open, `transform: translateX(100%)` when closed. Use Tailwind `transition-transform duration-300`.
- Render an overlay backdrop (semi-transparent dark overlay covering the table) when open. Clicking the backdrop calls `onClose`.
- Drawer width: `w-full max-w-xl` (Tailwind). On mobile it takes full width.
- Drawer header: prediction ID (truncated to first 8 chars), asset name, close button (×).
- Press `Escape` key to close (add `keydown` event listener in `useEffect`, remove on unmount).
- Show a spinner (Tailwind `animate-spin` border trick) while the detail fetch is loading.
- Show an error message "Failed to load prediction details. Try again." with a retry button on fetch error.

### PredictionDrawerContent component

Organize into clearly labeled sections separated by `<hr>` or Tailwind dividers:

**Section 1 - Summary**
- Asset, direction badge, magnitude, confidence, status badge (reuse `DirectionBadge` and `StatusBadge` from T01)
- Time horizon (e.g. "24h")
- Created at timestamp (local time)

**Section 2 - Prediction Rationale**
- Label: "Rationale"
- Full `rationale` text in a scrollable `<div>` with max height `max-h-40 overflow-y-auto`. Use `whitespace-pre-wrap` to preserve line breaks.

**Section 3 - Contributing Causal Edges**
- Label: "Contributing Edges"
- Render `ContributingEdgesList` component.
- Each edge shows: `from_node → to_node` (bold), relationship label, current weight as a colored progress bar (green if ≥ 0.6, amber if 0.4–0.59, red if < 0.4), and the weight as a percentage.
- Also show alpha/beta values in small grey text: `α=15.2 β=6.1`.
- If `contributing_edges` is empty, show "No contributing edges recorded."

**Section 4 - Source Articles**
- Label: "Source Articles"
- Render `SourceArticlesList` component.
- Each article: domain in a grey pill badge, title as a link (`<a href={url} target="_blank" rel="noopener noreferrer">`), published timestamp, credibility score as a small badge.
- Credibility score coloring: ≥ 0.7 green, 0.5–0.69 amber, < 0.5 red.
- If `sources` is empty, show "No source articles found."

**Section 5 - Actual vs Predicted Comparison (conditional)**
- Only render this section if `status !== 'PENDING'` (i.e. the prediction has been scored).
- Label: "Outcome Comparison"
- Render `ScoredComparisonChart` component.
- Use a Recharts `BarChart` with two bars side-by-side: "Predicted" (blue) and "Actual" (green if correct, red if wrong).
- X-axis: labels "Predicted", "Actual".
- Y-axis: direction encoded as numeric for the bar chart: UP=+1, NEUTRAL=0, DOWN=-1. Show tick labels as "UP", "NEUTRAL", "DOWN".
- Also display `actual_return_pct` as a text line below the chart: e.g. "Actual return: +1.23%".

**Section 6 - Confidence Calibration**
- Render `ConfidenceCalibrationBadge`.
- Show the confidence score as a horizontal bar (0–100%). Label above: "Confidence". Bar color: same thresholds as T01 (green ≥ 70%, amber 50–69%, red < 50%).
- Below bar: small text explaining interpretation: "High confidence" / "Moderate confidence" / "Low confidence".

### Integration with PredictionsTable (T01)

In `DashboardPage.tsx`, add state: `const [selectedPredictionId, setSelectedPredictionId] = useState<string | null>(null)`.

Pass `onRowClick={(id) => setSelectedPredictionId(id)}` to `PredictionsTable`.

Render `<PredictionDrawer predictionId={selectedPredictionId} onClose={() => setSelectedPredictionId(null)} />` at the page level.

In `PredictionRow.tsx`, add `onClick` handler that calls `onRowClick(prediction.prediction_id)`. Add `cursor-pointer hover:bg-gray-50` Tailwind classes to the row.

## Acceptance Criteria

1. Clicking a prediction row opens the detail drawer from the right with a 300 ms slide-in animation.
2. A semi-transparent backdrop appears behind the drawer; clicking it closes the drawer.
3. Pressing `Escape` closes the drawer.
4. The drawer shows a spinner while fetching prediction details.
5. Section 1 shows asset, direction badge, magnitude, confidence, status badge, time horizon, and created_at.
6. Section 2 shows the prediction rationale text; long rationale is scrollable within a fixed-height box.
7. Section 3 shows each contributing edge with a `from_node → to_node` label, relationship, and a color-coded weight progress bar.
8. Contributing edge weights ≥ 0.6 render green bars; 0.4–0.59 amber; < 0.4 red.
9. Section 4 shows each source article with domain badge, title link (opens in new tab), timestamp, and credibility score badge.
10. Source article credibility ≥ 0.7 renders green badge; 0.5–0.69 amber; < 0.5 red.
11. Section 5 (Outcome Comparison) is only rendered when `status !== 'PENDING'`.
12. When rendered, the ScoredComparisonChart shows two bars labeled "Predicted" and "Actual" using Recharts BarChart.
13. Section 6 shows a confidence bar colored according to the ≥70% / 50-69% / <50% thresholds.
14. If the API returns an error, the drawer shows "Failed to load prediction details. Try again." with a retry button.
15. If `contributing_edges` is empty, the fallback message "No contributing edges recorded." is shown.
16. If `sources` is empty, the fallback message "No source articles found." is shown.
17. `npm run build` passes with no TypeScript errors.
18. `npm test` passes all unit tests for this component.

## Implementation Notes

- The drawer must not unmount the table underneath when it opens — it overlays it. Do not navigate to a new route.
- Use React portals (`createPortal`) to render the drawer and backdrop at the `document.body` level, outside the main layout hierarchy, so it is not clipped by any `overflow: hidden` parent.
- For the `ScoredComparisonChart`, encoding direction as a number (UP=+1, NEUTRAL=0, DOWN=-1) for the bar height allows Recharts BarChart to render it naturally. A custom `tickFormatter` on the Y-axis converts -1/0/+1 back to the string labels.
- The `actual_return_pct` is shown as text below the chart, not as a bar, because percentage return and directional encoding have different scales.
- Rationale text may contain newlines. Use `whitespace-pre-wrap` CSS so these are preserved as visual line breaks.
- Article URLs from Swedish news sources may be HTTP. The `rel="noopener noreferrer"` on external links is required for security.
- The `alpha` and `beta` values in the contributing edges represent the Beta-Bernoulli posterior. Their ratio `alpha / (alpha + beta)` equals the `current_weight`. Display both raw values for transparency.
- If the drawer is open and the user navigates pagination in the table, the drawer should remain open with the currently selected prediction.

## Definition of Done

- [ ] `PredictionDetail` TypeScript interface defined in `src/types/prediction.ts`
- [ ] `usePredictionDetail.ts` hook fetches from `GET /predictions/{id}` with React Query
- [ ] `PredictionDrawer.tsx` renders as a right-side overlay with backdrop, open/close animation, and Escape key close
- [ ] `PredictionDrawerContent.tsx` renders all 6 sections in order
- [ ] `ContributingEdgesList.tsx` renders edges with color-coded weight bars
- [ ] `SourceArticlesList.tsx` renders articles with domain badge, title link, credibility badge
- [ ] `ScoredComparisonChart.tsx` renders Recharts BarChart with UP/NEUTRAL/DOWN encoding
- [ ] `ConfidenceCalibrationBadge.tsx` renders confidence bar with correct color thresholds
- [ ] Drawer integrated with `DashboardPage.tsx` via `selectedPredictionId` state
- [ ] Row click in `PredictionRow.tsx` triggers drawer open
- [ ] Loading spinner shown while fetching
- [ ] Error state shown on fetch failure with retry button
- [ ] Empty states shown for missing edges and sources
- [ ] Uses `createPortal` for drawer and backdrop DOM placement
- [ ] Unit tests written for `ContributingEdgesList`, `SourceArticlesList`, `ScoredComparisonChart`
- [ ] `npm run build` passes
- [ ] `npm run lint` passes
- [ ] `npm test` passes
