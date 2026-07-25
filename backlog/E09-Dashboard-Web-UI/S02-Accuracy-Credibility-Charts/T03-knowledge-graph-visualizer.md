# T03: Knowledge Graph Visualizer

## Context

This task builds an interactive knowledge graph visualization in the dashboard charts section. The knowledge graph (stored in Neo4j, read via the API Gateway) contains causal edges like `war → GOLD (CAUSES_UP, weight=0.7)`. This visualizer lets operators inspect which causal rules exist for a given asset, understand their current weights, and drill into the credibility history of any specific edge. It is the deepest analytical view in the dashboard — aimed at data scientists and system operators rather than casual users.

## Background

The Prediction Service (service 3) fires subgraphs from Neo4j to generate predictions. The Credibility Service (service 6) updates edge weights in Neo4j after each scored prediction. The current state of these edges is exposed by the API Gateway at `GET /graph/assets/{symbol}`.

Expected response shape from `GET /graph/assets/{symbol}` (e.g. `GET /graph/assets/GOLD`):
```json
{
  "nodes": [
    { "id": "GOLD", "type": "asset", "label": "GOLD" },
    { "id": "war", "type": "event_type", "label": "war" },
    { "id": "inflation", "type": "event_type", "label": "inflation" },
    { "id": "rate_hike", "type": "event_type", "label": "rate hike" }
  ],
  "edges": [
    {
      "id": "war->gold_up",
      "source": "war",
      "target": "GOLD",
      "relationship": "CAUSES_UP",
      "current_weight": 0.71,
      "credibility_history": [
        { "date": "2024-01-01", "score": 0.60 },
        { "date": "2024-01-08", "score": 0.65 },
        { "date": "2024-01-15", "score": 0.71 }
      ]
    }
  ]
}
```

Node types:
- `"asset"` — the central asset node (e.g. GOLD, OIL, OMXS30)
- `"event_type"` — causal factor nodes (e.g. war, inflation, rate_hike)

Edge `relationship` values: `"CAUSES_UP"` or `"CAUSES_DOWN"`.

The `credibility_history` array provides weekly snapshots of the edge's credibility score over time. Used to render a sparkline when an edge is clicked.

## Inputs

- User interaction: asset selector dropdown (GOLD / OIL / OMXS30)
- REST: `GET /graph/assets/{symbol}` fetched when asset is selected
- User interaction: click on a graph edge to show credibility history sparkline

## Outputs

- Interactive graph rendered in the browser
- No data written; this is a read-only display component

## Technical Requirements

### Library choice

Use **React Flow** (`@xyflow/react`, version 11.x). It is TypeScript-native, supports custom node shapes, and handles pan/zoom out of the box. Install:
```bash
npm install @xyflow/react
```

Import base CSS in the component file:
```typescript
import '@xyflow/react/dist/style.css';
```

Alternatively, if the project already has **Cytoscape.js** installed (`cytoscape` + `react-cytoscapejs`), use that instead — the requirements below apply equally to both libraries. The choice should be consistent with whatever is already in `package.json`.

### File structure

```
dashboard/
  src/
    components/
      graph/
        KnowledgeGraphPanel.tsx      ← outer panel with asset selector and graph
        KnowledgeGraphView.tsx       ← React Flow canvas component
        AssetNode.tsx                ← custom React Flow node for asset type
        EventTypeNode.tsx            ← custom React Flow node for event_type
        EdgeCredibilitySparkline.tsx ← sparkline shown on edge click
    hooks/
      useGraphAsset.ts              ← React Query hook for GET /graph/assets/{symbol}
    types/
      graph.ts                      ← TypeScript interfaces
```

### TypeScript interfaces (`src/types/graph.ts`)

```typescript
export type NodeType = 'asset' | 'event_type';
export type EdgeRelationship = 'CAUSES_UP' | 'CAUSES_DOWN';

export interface GraphNode {
  id: string;
  type: NodeType;
  label: string;
}

export interface CredibilityHistoryPoint {
  date: string;   // YYYY-MM-DD
  score: number;  // 0-1
}

export interface GraphEdge {
  id: string;
  source: string;  // node id
  target: string;  // node id
  relationship: EdgeRelationship;
  current_weight: number;  // 0-1
  credibility_history: CredibilityHistoryPoint[];
}

export interface GraphAssetResponse {
  nodes: GraphNode[];
  edges: GraphEdge[];
}
```

### React Query hook (`src/hooks/useGraphAsset.ts`)

Query key: `['graph-asset', symbol]`. Fetch from `GET /graph/assets/${symbol}`. `staleTime: 300_000`. Enabled only when `symbol` is non-null.

### KnowledgeGraphPanel component

Outer wrapper that contains:
1. Asset selector: `<select>` with options GOLD, OIL, OMXS30. Default selection: GOLD.
2. `KnowledgeGraphView` rendered below the selector.
3. `EdgeCredibilitySparkline` panel: appears below the graph when an edge is clicked, closes with an × button.

State: `selectedSymbol: string`, `selectedEdge: GraphEdge | null`.

### KnowledgeGraphView component

Props:
```typescript
interface KnowledgeGraphViewProps {
  nodes: GraphNode[];
  edges: GraphEdge[];
  onEdgeClick: (edge: GraphEdge) => void;
}
```

**Node transformation** — convert `GraphNode[]` to React Flow `Node[]`:
```typescript
const rfNodes: Node[] = nodes.map((n, i) => ({
  id: n.id,
  type: n.type === 'asset' ? 'assetNode' : 'eventTypeNode',
  position: autoLayout(n, i, nodes.length),  // see layout note below
  data: { label: n.label }
}));
```

**Edge transformation** — convert `GraphEdge[]` to React Flow `Edge[]`:
```typescript
const rfEdges: Edge[] = edges.map(e => ({
  id: e.id,
  source: e.source,
  target: e.target,
  label: `${(e.current_weight * 100).toFixed(0)}%`,
  style: {
    stroke: e.relationship === 'CAUSES_UP' ? '#10b981' : '#ef4444',
    strokeWidth: weightToStrokeWidth(e.current_weight)  // 1-5px range
  },
  data: e  // store full edge for click handler
}));
```

**Layout**: Use a simple radial layout: place the asset node at the center `(0, 0)` and distribute event_type nodes in a circle around it. Formula:
```typescript
function autoLayout(node: GraphNode, index: number, total: number): { x: number, y: number } {
  if (node.type === 'asset') return { x: 300, y: 200 };
  const angle = (2 * Math.PI * index) / (total - 1);  // -1 for the asset node
  return { x: 300 + 220 * Math.cos(angle), y: 200 + 150 * Math.sin(angle) };
}
```

**Edge thickness**: Map `current_weight` to stroke width linearly: weight 0.0 → 1px, weight 1.0 → 5px.
```typescript
function weightToStrokeWidth(weight: number): number {
  return 1 + weight * 4;
}
```

**Custom nodes**:
- `AssetNode`: Render a circle (CSS `border-radius: 50%`, `bg-blue-100 border-2 border-blue-500`) with the asset label centered.
- `EventTypeNode`: Render a diamond shape. Use CSS `transform: rotate(45deg)` on an inner square, counter-rotate the text: `transform: rotate(-45deg)`.

**Edge click handler**: React Flow's `onEdgeClick` callback provides `(event, edge)` — call `onEdgeClick(edge.data as GraphEdge)`.

**Canvas settings**:
```tsx
<ReactFlow
  nodes={rfNodes}
  edges={rfEdges}
  onEdgeClick={(_, edge) => onEdgeClick(edge.data as GraphEdge)}
  fitView
  fitViewOptions={{ padding: 0.2 }}
  minZoom={0.3}
  maxZoom={2}
  nodesDraggable={false}
  nodesConnectable={false}
  elementsSelectable={true}
>
  <MiniMap />
  <Controls />
  <Background color="#f3f4f6" gap={16} />
</ReactFlow>
```

The graph canvas must be at least `600px` tall. Use `h-[600px]` Tailwind class on the wrapper div.

**Loading state**: Show a grey placeholder `h-[600px] animate-pulse bg-gray-100 rounded` while loading.
**Error state**: "Failed to load knowledge graph. Try again."
**Empty state**: If `nodes` is empty, show "No graph data for this asset."

### EdgeCredibilitySparkline component

Shown below the graph when an edge is clicked.

Props:
```typescript
interface EdgeCredibilitySparklineProps {
  edge: GraphEdge;
  onClose: () => void;
}
```

Render:
- Header: `"{edge.source} → {edge.target}"`, relationship label, current weight as %, close button
- Sparkline: use Recharts `LineChart` (small, `height={80}`) with:
  - `XAxis dataKey="date"` with `hide={true}` (no tick labels to save space)
  - `YAxis domain={[0, 1]}` with `hide={true}`
  - `Line dataKey="score" stroke="#6366f1" dot={false} strokeWidth={2}`
  - `Tooltip` showing date and score
  - `ResponsiveContainer width="100%" height={80}`
- Below the sparkline: "First observed: {first date}" and "Latest: {last date} — {score%}" in small grey text

## Acceptance Criteria

1. The knowledge graph panel renders with an asset selector dropdown (GOLD, OIL, OMXS30).
2. Selecting GOLD fetches `GET /graph/assets/GOLD` and renders the graph.
3. The asset node renders as a circle; event type nodes render as diamonds.
4. Edges with `relationship=CAUSES_UP` render green; `CAUSES_DOWN` edges render red.
5. Edge thickness visually varies by `current_weight` (heavier = thicker).
6. Edge labels show the current weight as a percentage (e.g. `71%`).
7. The graph supports pan and zoom. Zoom in/out with scroll wheel works without error.
8. A minimap and zoom controls are visible on the canvas.
9. Clicking an edge opens the `EdgeCredibilitySparkline` panel below the graph.
10. The sparkline shows the credibility history as a line chart with a tooltip.
11. Clicking the × button on the sparkline panel closes it.
12. Switching the asset selector closes any open sparkline and loads the new asset's graph.
13. While loading, a grey placeholder is shown in place of the graph.
14. On API error, the error message and retry button are shown.
15. When `nodes` is empty, the empty state message is shown.
16. `npm run build` passes with no TypeScript errors.
17. `npm test` passes all unit tests for this component.

## Implementation Notes

- React Flow requires its container to have an explicit height — it does not auto-size. Always wrap in a `div` with a fixed or min-height. `h-[600px]` is the minimum.
- The radial auto-layout function must handle the edge case where `total === 1` (only the asset node, no event_type nodes). Guard against division by zero.
- React Flow nodes are positioned absolutely on the canvas. Initial positions from the radial layout are good enough — enabling `fitView` will center and scale them on first render.
- `nodesDraggable={false}` is intentional — the graph is for inspection, not editing. Users can still pan and zoom the canvas.
- When the `selectedSymbol` changes, call `useGraphAsset(selectedSymbol)` which will fetch fresh data. Use `useEffect` to reset `selectedEdge` to null when the symbol changes (otherwise the sparkline shows stale data from the previous asset).
- The `credibility_history` sparkline uses weekly data points. With 4–12 points, the `hide={true}` XAxis prevents crowded labels. The tooltip still shows the date on hover.
- Edge labels in React Flow are set via the `label` prop on the Edge object and render as an SVG text element centered on the edge. For readability, set `labelStyle={{ fontSize: 11, fill: '#374151' }}` and `labelBgStyle={{ fill: 'white', fillOpacity: 0.8 }}` on the edge object.
- If the API returns `credibility_history: []` for an edge, the sparkline should still render but show an empty state message: "No credibility history available for this edge."
- The diamond shape for `EventTypeNode` can be achieved with a `div` using `style={{ transform: 'rotate(45deg)', width: 60, height: 60 }}` and an inner `div` with `style={{ transform: 'rotate(-45deg)' }}` for the text. The outer shape gets the border/background; the inner div holds the label text.

## Definition of Done

- [ ] `src/types/graph.ts` defines all interfaces
- [ ] `useGraphAsset.ts` hook fetches with React Query, enabled only when symbol is set
- [ ] `KnowledgeGraphPanel.tsx` renders asset selector and composes graph + sparkline
- [ ] `KnowledgeGraphView.tsx` renders React Flow canvas with custom nodes and styled edges
- [ ] `AssetNode.tsx` renders circle shape
- [ ] `EventTypeNode.tsx` renders diamond shape
- [ ] Radial auto-layout function handles single-node edge case
- [ ] CAUSES_UP edges are green; CAUSES_DOWN edges are red
- [ ] Edge thickness proportional to `current_weight`
- [ ] Edge click opens `EdgeCredibilitySparkline`
- [ ] `EdgeCredibilitySparkline.tsx` renders Recharts sparkline with tooltip
- [ ] Sparkline close button works; symbol change resets sparkline
- [ ] Loading, error, and empty states handled
- [ ] MiniMap and Controls rendered on the canvas
- [ ] Unit tests for `weightToStrokeWidth` helper and `autoLayout` helper
- [ ] Unit tests for `KnowledgeGraphView` edge color logic
- [ ] `npm run build` passes
- [ ] `npm run lint` passes
- [ ] `npm test` passes
