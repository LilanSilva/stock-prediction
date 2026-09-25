# docs — System architecture diagrams and other system documents

This folder holds the **system architecture diagrams**, and is where any other diagram-related or
general system document belongs.

**Specifications are not here** — they live in [requirements/](../requirements/README.md). Start there
for what the system does, how each service works, and which test proves each requirement.

## Contents

| Path | Contains |
|---|---|
| [architectural-documents/](architectural-documents/) | The Mermaid diagrams listed below |
| `feed-analyzer-investor-presentation.pptx` | Investor-facing slide deck. A point-in-time presentation, not a specification — do not treat it as a source of truth |

## Architecture diagrams

Open `.mmd` files with the **Mermaid Preview** VS Code plugin (`Ctrl+Shift+P` → "Mermaid: Open
Preview").

| File | What it shows |
|---|---|
| [01-architecture-components.mmd](architectural-documents/01-architecture-components.mmd) | Components, schemas, graph store, and optional snapshot worker |
| [02-architecture-queues.mmd](architectural-documents/02-architecture-queues.mmd) | Topic exchange, independent consumer queues and sampled SHADOW path |
| [03-sequence-prediction-pipeline.mmd](architectural-documents/03-sequence-prediction-pipeline.mmd) | Ingestion, cleansing, multi-event context, and token-efficient prediction |
| [04-sequence-verification-credibility.mmd](architectural-documents/04-sequence-verification-credibility.mmd) | Dual-session price request, scoring, and idempotent learning |
| [05-sequence-dashboard.mmd](architectural-documents/05-sequence-dashboard.mmd) | Read-only API access and live fan-out — the Dashboard is not built yet |

Diagrams sit at authority level 5, below the executable models, the specifications, the reference data,
and the ADRs — see [requirements/README.md](../requirements/README.md#authority). **When a diagram
disagrees with an SRS, the diagram is the defect.** Update it in the same change that alters the
topology it shows.
