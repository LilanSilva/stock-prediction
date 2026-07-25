# T02: Credibility Trends Chart

## Context

This task builds the credibility trends panel in the dashboard's charts section. It has two sub-components: (1) a bar chart of the top 20 knowledge graph edges ranked by current credibility score with 95% confidence interval error bars, and (2) a ranked table of news source credibility scores. Together they let operators understand which causal rules the system trusts most and which news sources are proving most reliable. This view is rendered in the same charts section as the accuracy chart (T01), below it.

## Background

**Credibility Service (service 6)** maintains Beta-Bernoulli credibility estimates per causal edge and per source. For each edge, it tracks `alpha` (hit count pseudo-count) and `beta` (miss count pseudo-count). The credibility score (mean of the Beta distribution) is `alpha / (alpha + beta)`. The 95% confidence interval uses the Wilson score interval or the Beta distribution's 2.5th and 97.5th percentiles.

The API Gateway serves this data at two endpoints:

**`GET /credibility/edges`** response shape:
```json
{
  "edges": [
    {
      "edge_id": "war->gold",
      "from_node": "war",
      "to_node": "GOLD",
      "relationship": "CAUSES_UP",
      "credibility_score": 0.71,
      "ci_lower": 0.58,
      "ci_upper": 0.82,
      "alpha": 15.2,
      "beta": 6.1,
      "total_activations": 21
    }
  ]
}
```

Edges are returned pre-sorted by `credibility_score` descending. The endpoint returns up to 20 edges.

**`GET /credibility/sources`** response shape:
```json
{
  "sources": [
    {
      "domain": "dn.se",
      "credibility_score": 0.73,
      "hit_count": 18,
      "miss_count": 7,
      "total_count": 25,
      "trend": "UP"
    }
  ]
}
```

`trend` values: `"UP"` (score improved in last 7 days), `"DOWN"` (score declined), `"STABLE"` (unchanged).

Sources are returned pre-sorted by `credibility_score` descending.

## Inputs

- REST: `GET /credibility/edges` — top 20 KG edges with credibility score and CI
- REST: `GET /credibility/sources` — source credibility scores with hit/miss counts
- No user filter inputs for this component (data is always full top-20 view)

## Outputs

- Rendered credibility panel with bar chart and source table
- No data written; this is a read-only display component

## Technical Requirements

### File structure

```
dashboard/
  src/
    components/
      charts/
        CredibilityPanel.tsx          ← outer panel that composes both sub-components
        EdgeCredibilityChart.tsx      ← bar chart with CI error bars
        SourceCredibilityTable.tsx    ← ranked table of source credibility
    hooks/
      useCredibilityEdges.ts         ← React Query hook for GET /credibility/edges
      useCredibilitySources.ts       ← React Query hook for GET /credibility/sources
    types/
      credibility.ts                 ← TypeScript interfaces
```

### TypeScript interfaces (`src/types/credibility.ts`)

```typescript
export interface CredibilityEdge {
  edge_id: string;
  from_node: string;
  to_node: string;
  relationship: string;
  credibility_score: number;  // 0-1, Beta mean
  ci_lower: number;           // 95% CI lower bound
  ci_upper: number;           // 95% CI upper bound
  alpha: number;
  beta: number;
  total_activations: number;
}

export type CredibilityTrend = 'UP' | 'DOWN' | 'STABLE';

export interface CredibilitySource {
  domain: string;
  credibility_score: number;
  hit_count: number;
  miss_count: number;
  total_count: number;
  trend: CredibilityTrend;
}

export interface CredibilityEdgesResponse {
  edges: CredibilityEdge[];
}

export interface CredibilitySourcesResponse {
  sources: CredibilitySource[];
}
```

### React Query hooks

`useCredibilityEdges`: query key `['credibility-edges']`, `staleTime: 120_000` (2 minutes).
`useCredibilitySources`: query key `['credibility-sources']`, `staleTime: 120_000`.

### EdgeCredibilityChart component

Use Recharts `BarChart` (horizontal orientation — `layout="vertical"`) so edge labels (e.g. `war → GOLD`) are readable on the left.

Configuration:
- **Data**: up to 20 bars, one per edge. Prepare a label string for each bar: `"{from_node} → {to_node}"` (e.g. `"war → GOLD"`).
- **YAxis**: `YAxis type="category" dataKey="label" width={140}` to show edge labels. Use `tick={{ fontSize: 11 }}` to fit long labels.
- **XAxis**: `XAxis type="number" domain={[0, 1]}` with ticks at 0, 0.2, 0.4, 0.6, 0.8, 1.0. Format labels as `0%`, `20%`, etc.
- **Bar**: `<Bar dataKey="credibility_score">` with a `Cell` per bar for color:
  - `credibility_score >= 0.6` → `fill="#10b981"` (green)
  - `0.4 <= credibility_score < 0.6` → `fill="#f59e0b"` (amber)
  - `credibility_score < 0.4` → `fill="#ef4444"` (red)
- **Error bars** (95% CI): Use Recharts `<ErrorBar>` component on the Bar. The `ErrorBar` needs `dataKey` pointing to an array `[lower_error, upper_error]` where `lower_error = credibility_score - ci_lower` and `upper_error = ci_upper - credibility_score`. Pre-compute these in the data transformation step. `ErrorBar` props: `width={4}`, `strokeWidth={2}`, `stroke="#6b7280"` (grey).
- **Tooltip**: Show edge label, credibility score as %, CI range as `[{ci_lower*100}%, {ci_upper*100}%]`, total activations.
- **Responsive container**: `<ResponsiveContainer width="100%" height={Math.max(300, edges.length * 28)} />`
- **Loading state**: Grey placeholder `animate-pulse`.
- **Empty state**: "No credibility data available yet."

### SourceCredibilityTable component

Render an HTML `<table>` (or Tailwind-styled `<div>` grid) with columns:

| # | Source | Credibility | Hits | Misses | Total | Trend |
|---|---|---|---|---|---|---|

Column details:
- **#** - rank number (1, 2, 3 ...)
- **Source** - `domain` as plain text, bold
- **Credibility** - `credibility_score` formatted as `XX.X%`. Color: ≥ 70% green text, 50–69% amber, < 50% red
- **Hits** - `hit_count` integer
- **Misses** - `miss_count` integer
- **Total** - `total_count` integer
- **Trend** - icon only:
  - `UP` → `▲` in green
  - `DOWN` → `▼` in red
  - `STABLE` → `─` in grey

Table header row uses `bg-gray-50` background. Alternating row backgrounds: even rows `bg-white`, odd rows `bg-gray-50` (zebra striping).

Loading state: show 5 skeleton rows with `animate-pulse`.
Empty state: "No source credibility data available."

### CredibilityPanel component

Compose both sub-components in a two-section layout:

```tsx
<section aria-label="Credibility Trends">
  <h2>Knowledge Graph Edge Credibility</h2>
  <EdgeCredibilityChart />
  <h2>Source Credibility Rankings</h2>
  <SourceCredibilityTable />
</section>
```

Add a "Refresh" button in the panel header that calls `refetch()` on both hooks simultaneously.

## Acceptance Criteria

1. The edge credibility chart renders with up to 20 horizontal bars, one per KG edge.
2. Each bar label shows `"{from_node} → {to_node}"` on the left.
3. Bars with `credibility_score >= 0.6` are green; 0.4–0.59 amber; < 0.4 red.
4. Each bar has 95% CI error bars rendered as a centered error bar marker.
5. The X-axis shows 0% to 100% with 20% interval ticks.
6. Hovering over a bar shows a tooltip with score, CI range, and total activations.
7. The source credibility table renders all sources sorted by credibility score descending.
8. Each row shows rank, domain, credibility %, hit count, miss count, total count, and trend icon.
9. Credibility ≥ 70% in the table renders green text; 50–69% amber; < 50% red.
10. Trend UP renders ▲ green; DOWN renders ▼ red; STABLE renders ─ grey.
11. Clicking the "Refresh" button re-fetches both the edges and sources endpoints.
12. Both sub-components show loading skeletons while fetching.
13. Both sub-components show empty state messages when no data is returned.
14. `npm run build` passes with no TypeScript errors.
15. `npm test` passes all unit tests for this component.

## Implementation Notes

- Recharts `ErrorBar` is documented but can be tricky. The `dataKey` on `ErrorBar` must reference a field in the data array that holds `[lowerError, upperError]` as a two-element array (absolute error offsets, not absolute bounds). Pre-process the edges array:
  ```typescript
  const chartData = edges.map(e => ({
    ...e,
    label: `${e.from_node} → ${e.to_node}`,
    ci_error: [e.credibility_score - e.ci_lower, e.ci_upper - e.credibility_score]
  }));
  ```
- Recharts `Cell` inside `Bar` requires iterating over the data to assign per-bar fill colors. Use `edges.map((entry, index) => <Cell key={index} fill={getColor(entry.credibility_score)} />)`.
- The chart height should be dynamic: with 20 edges at 28px per bar plus margins, the chart needs at least 600px height. Use `Math.max(300, edges.length * 28 + 60)` for `ResponsiveContainer` height.
- The Beta-Bernoulli credibility score `alpha / (alpha + beta)` is computed server-side by the Credibility Service. The CI bounds are also computed server-side (likely as the 2.5th and 97.5th percentiles of the Beta(alpha, beta) distribution). Do NOT recompute these client-side — display them as received from the API.
- The source credibility `trend` field is also computed server-side (comparing the current score to the score 7 days ago). Display it as-is.
- If the `trend` field is missing or unexpected, default to rendering `─` (STABLE).
- For accessibility, add `aria-label` attributes to the trend icons: `aria-label="Trending up"` etc.

## Definition of Done

- [ ] `src/types/credibility.ts` defines all interfaces
- [ ] `useCredibilityEdges.ts` hook fetches with React Query
- [ ] `useCredibilitySources.ts` hook fetches with React Query
- [ ] `EdgeCredibilityChart.tsx` renders horizontal Recharts BarChart with color-coded bars and CI error bars
- [ ] `SourceCredibilityTable.tsx` renders ranked table with color-coded scores and trend icons
- [ ] `CredibilityPanel.tsx` composes both components with refresh button
- [ ] Loading skeletons shown for both sub-components
- [ ] Empty states shown for both sub-components
- [ ] CI error bar data pre-processing implemented correctly
- [ ] Dynamic chart height based on number of edges
- [ ] Unit tests for color threshold logic in `EdgeCredibilityChart`
- [ ] Unit tests for trend icon rendering in `SourceCredibilityTable`
- [ ] `npm run build` passes
- [ ] `npm run lint` passes
- [ ] `npm test` passes
