# T01: Accuracy Over Time Chart

## Context

This task builds the accuracy over time chart in the dashboard's charts section (`dashboard/src/components/charts/`). The chart shows how the prediction system's accuracy has evolved day by day, broken down per asset (GOLD, OIL, OMXS30). It is the primary performance monitoring view for operators and data scientists evaluating the system. It sits above the credibility charts (T02) in the dashboard layout.

## Background

The Verification Service (service 5) scores predictions by comparing `predicted_direction` to `actual_direction` and writes results to the Outcomes DB (Postgres). The API Gateway (service 7) aggregates these scores into a rolling 7-day accuracy time series, served at `GET /predictions/stats`.

Expected response shape from `GET /predictions/stats`:
```json
{
  "summary": {
    "overall_accuracy_pct": 68.5,
    "total_predictions": 142,
    "correct_count": 97
  },
  "time_series": [
    {
      "date": "2024-01-15",
      "GOLD": 75.0,
      "OIL": 60.0,
      "OMXS30": 66.7
    },
    {
      "date": "2024-01-14",
      "GOLD": 70.0,
      "OIL": null,
      "OMXS30": 80.0
    }
  ]
}
```

Each `time_series` entry represents one calendar day. The value per asset is the rolling 7-day accuracy percentage (scored predictions correct / total scored predictions in the trailing 7 days). A `null` value means no predictions were scored for that asset on that day — the line should be broken (gap in the chart).

The `GET /predictions/stats` endpoint supports optional query params:
- `date_from` (ISO-8601 date string, default: 30 days ago)
- `date_to` (ISO-8601 date string, default: today)

## Inputs

- REST: `GET /predictions/stats?date_from=YYYY-MM-DD&date_to=YYYY-MM-DD`
- User interactions: date range picker to adjust the time window shown

## Outputs

- Rendered line chart and summary stat cards in the browser
- No data written; this is a read-only display component

## Technical Requirements

### File structure

```
dashboard/
  src/
    components/
      charts/
        AccuracyChart.tsx            ← main chart component
        AccuracySummaryCards.tsx     ← summary stat cards above chart
        ChartDateRangePicker.tsx     ← shared date range input for charts section
    hooks/
      usePredictionStats.ts         ← React Query hook for GET /predictions/stats
    types/
      stats.ts                      ← TypeScript interfaces for stats responses
```

### TypeScript interfaces (`src/types/stats.ts`)

```typescript
export interface AccuracySummary {
  overall_accuracy_pct: number;
  total_predictions: number;
  correct_count: number;
}

export interface AccuracyTimeSeriesPoint {
  date: string;          // YYYY-MM-DD
  GOLD: number | null;
  OIL: number | null;
  OMXS30: number | null;
}

export interface PredictionStatsResponse {
  summary: AccuracySummary;
  time_series: AccuracyTimeSeriesPoint[];
}
```

### React Query hook (`src/hooks/usePredictionStats.ts`)

Query key: `['prediction-stats', dateFrom, dateTo]`. Fetch from `GET /predictions/stats` with `date_from` and `date_to` query params. `staleTime: 300_000` (5 minutes — accuracy stats do not change frequently). Expose `data`, `isLoading`, `error`, `refetch`.

### AccuracySummaryCards component

Three cards displayed in a row (`flex gap-4`) above the chart:

1. **Overall Accuracy** - large number `{overall_accuracy_pct.toFixed(1)}%`, label below. Color the number: ≥ 70% green, 50–69% amber, < 50% red.
2. **Total Predictions** - integer count, label below.
3. **Correct Predictions** - integer count, label below. Show `{correct_count} / {total_predictions}` format.

Each card uses a white rounded box with drop shadow: Tailwind `bg-white rounded-lg shadow p-4`.

When `isLoading`, render skeleton cards with `animate-pulse`.

### AccuracyChart component

Use `Recharts` `LineChart` from the `recharts` package.

Chart configuration:
- **X-axis**: `XAxis dataKey="date"`. Format tick labels as `MMM DD` using `Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric' })`. Show every 3rd or 7th tick to avoid crowding (use `interval` prop based on data length).
- **Y-axis**: `YAxis domain={[0, 100]}`. Label: "Accuracy %". Show ticks at 0, 25, 50, 75, 100.
- **Lines**: One `Line` per asset:
  - GOLD: `stroke="#f59e0b"` (amber)
  - OIL: `stroke="#6366f1"` (indigo)
  - OMXS30: `stroke="#10b981"` (emerald)
  - All lines: `strokeWidth={2}`, `dot={false}`, `connectNulls={false}` (gaps where null)
- **Tooltip**: `<Tooltip>` with custom `content` prop. Show date in header, then each asset's accuracy % (or "No data" if null) in the asset's color.
- **Legend**: `<Legend>` below chart. Show colored line swatch + asset name.
- **Reference line**: `<ReferenceLine y={50} stroke="#9ca3af" strokeDasharray="4 4" label="50%" />` to mark the random-guess baseline.
- **Responsive container**: Wrap in `<ResponsiveContainer width="100%" height={300} />`.
- **Loading state**: Show a grey placeholder box `h-[300px] animate-pulse bg-gray-100 rounded` while `isLoading`.
- **Error state**: Show "Failed to load accuracy data." with a retry button.
- **Empty state**: If `time_series` is empty or all values are null, show "No scored predictions yet. Check back after the first scoring cycle."

### ChartDateRangePicker component

- Two `<input type="date">` inputs: "From" and "To".
- Default: `date_from = today - 30 days`, `date_to = today`.
- On change, update query params which triggers `usePredictionStats` re-fetch.
- Validate: `date_from` must not be after `date_to`. Show inline error if violated.
- Place above the `AccuracySummaryCards` in the layout.

### Layout integration

In `DashboardPage.tsx` (or a new `ChartsPage.tsx` if the dashboard uses routing), add the accuracy chart section:

```tsx
<section aria-label="Prediction Accuracy">
  <h2 className="text-xl font-semibold mb-4">Prediction Accuracy Over Time</h2>
  <ChartDateRangePicker value={dateRange} onChange={setDateRange} />
  <AccuracySummaryCards dateFrom={dateRange.from} dateTo={dateRange.to} />
  <AccuracyChart dateFrom={dateRange.from} dateTo={dateRange.to} />
</section>
```

## Acceptance Criteria

1. The accuracy chart section renders without console errors.
2. Three summary cards display overall accuracy %, total predictions, and correct/total count.
3. The line chart renders one line per asset (GOLD in amber, OIL in indigo, OMXS30 in emerald).
4. X-axis shows date labels; Y-axis shows 0–100% with ticks at 0, 25, 50, 75, 100.
5. A dashed reference line at y=50 is visible.
6. Hovering over the chart shows a tooltip with the date and accuracy % per asset.
7. A legend below the chart identifies each line by asset name and color.
8. When an asset has no data for a date (null), the line breaks at that point (no interpolation across the gap).
9. Changing the date range picker updates the chart.
10. Setting `date_from` after `date_to` shows an inline validation error and does not fire a new API request.
11. While loading, skeleton cards and a grey placeholder are shown.
12. On API error, the error message and retry button are displayed.
13. When `time_series` is empty, the empty state message is shown.
14. Overall accuracy ≥ 70% renders green; 50–69% amber; < 50% red in the summary card.
15. `npm run build` passes with no TypeScript errors.
16. `npm test` passes all unit tests for `AccuracySummaryCards` and `AccuracyChart`.

## Implementation Notes

- Recharts `connectNulls={false}` is the correct prop to break lines at null data points. Do NOT use `connectNulls={true}` as that would interpolate across missing data, giving a misleadingly smooth line.
- The rolling 7-day accuracy is computed server-side by the API Gateway. The chart displays it as-is — do not recompute it client-side.
- When the data array has more than 60 points, set `interval={6}` on the XAxis to show weekly ticks. For 30 points, use `interval={2}` for every-3-day ticks. For ≤ 14 points, use `interval={1}`.
- Recharts requires its data array to have consistent keys across all points. If the API returns some days with `OMXS30: null` and others without the key entirely, normalize the array in the hook: ensure every point has all three asset keys (defaulting to null if missing).
- The summary cards are fetched from the same `GET /predictions/stats` endpoint as the chart. Use a single `usePredictionStats` hook call in the parent section component and pass `summary` and `time_series` as props to the child components — do not make two separate API calls.
- For the date range default, compute `today - 30 days` on the client at component mount time. Store in `useState` so it does not re-compute on re-renders.

## Definition of Done

- [ ] `src/types/stats.ts` defines `AccuracySummary`, `AccuracyTimeSeriesPoint`, `PredictionStatsResponse`
- [ ] `usePredictionStats.ts` hook fetches with React Query, accepts `dateFrom`/`dateTo` params
- [ ] `AccuracySummaryCards.tsx` renders 3 cards with correct color-coding
- [ ] `AccuracyChart.tsx` renders Recharts LineChart with 3 lines, tooltip, legend, reference line
- [ ] Lines break at null data points (`connectNulls={false}`)
- [ ] `ChartDateRangePicker.tsx` with validation implemented
- [ ] Loading, error, and empty states all handled
- [ ] Layout integration in `DashboardPage.tsx` or `ChartsPage.tsx`
- [ ] Unit tests for `AccuracySummaryCards` color-coding logic
- [ ] Unit tests for `AccuracyChart` null handling and rendering
- [ ] `npm run build` passes
- [ ] `npm run lint` passes
- [ ] `npm test` passes
