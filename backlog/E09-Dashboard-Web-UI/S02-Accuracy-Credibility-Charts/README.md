# S02 - Accuracy & Credibility Charts

## Overview

This story builds the analytical charts section of the dashboard. While S01 answers "what did the system predict?", S02 answers "how well is the system performing over time?" and "which knowledge graph rules are most reliable?"

Three chart components are built:
1. A rolling accuracy line chart showing prediction accuracy per asset over time
2. An edge credibility bar chart and source credibility ranked table
3. An interactive knowledge graph visualizer showing causal edges and their weights

---

## Tasks

| Task | Description |
|---|---|
| **T01 - Accuracy over time chart** | Recharts line chart of rolling 7-day accuracy per asset, with summary stat cards |
| **T02 - Credibility trends chart** | Two-panel component: top-20 KG edge credibility bar chart with CI error bars, and ranked source credibility table |
| **T03 - Knowledge graph visualizer** | Interactive React Flow / Cytoscape.js graph of KG nodes and edges, with edge credibility history sparkline on click |

---

## Dependencies

Before this story can be started:

- **E08 API Gateway** - the following endpoints must be implemented and reachable:
  - `GET /predictions/stats` - rolling accuracy time series per asset
  - `GET /credibility/edges` - top KG edges with credibility scores and CI
  - `GET /credibility/sources` - source credibility scores with hit/miss counts
  - `GET /graph/assets/{symbol}` - graph nodes and edges for a given asset
- **S01 T01** - the `DashboardPage.tsx` scaffold and Vite project must exist.
- React Flow (`@xyflow/react`) or Cytoscape.js (`cytoscape` + `react-cytoscapejs`) installed in `dashboard/`.

---

## How to Test End-to-End

1. Start the full stack: `docker compose up`
2. Navigate to `http://localhost:3000/charts` (or the charts tab/page)
3. Verify the accuracy line chart renders with one line per asset.
4. Hover over chart data points and verify a tooltip shows date + accuracy %.
5. Verify summary cards above the chart show overall accuracy %, total predictions, correct count.
6. Verify the edge credibility bar chart shows up to 20 bars, each with an error bar.
7. Verify bars are green/amber/red based on credibility score thresholds.
8. Verify the source credibility table is sorted by score descending.
9. Verify each row shows hit count, miss count, and a trend arrow.
10. Navigate to the knowledge graph tab. Select asset GOLD from the dropdown.
11. Verify nodes and edges render as a graph.
12. Click an edge and verify a credibility history sparkline appears.
13. Pan and zoom the graph without errors.
