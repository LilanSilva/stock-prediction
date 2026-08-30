# scripts — Manual scripts

Scripts a user or developer runs **by hand**. Nothing here is invoked by a running service; the
services' own scheduled jobs are in their `app.py` and documented in each SRS section 7.

## Environment setup

| Script | Platform |
|---|---|
| [setup-venv.ps1](setup-venv.ps1) | Windows (PowerShell) |
| [setup-venv.sh](setup-venv.sh) | macOS/Linux (bash) |

Run once when setting up the repository. Both create `.venv/` at the repository root, install the
hash-verified dependencies, and install the local `shared` package editable. Safe to re-run to sync
after the lockfile changes.

**Invocation and full setup notes are in the repository
[README](../README.md#first-time-setup)** — including the manual equivalent if you prefer not to run a
script.

## Build and deploy

| Script | Purpose |
|---|---|
| [build-and-deploy.ps1](build-and-deploy.ps1) | Rebuild the seven service images and redeploy **only those containers**, onto data stores that are already running |

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build-and-deploy.ps1
powershell -ExecutionPolicy Bypass -File scripts\build-and-deploy.ps1 -Service feed-prediction,feed-notification
powershell -ExecutionPolicy Bypass -File scripts\build-and-deploy.ps1 -NoBuild        # redeploy current images
powershell -ExecutionPolicy Bypass -File scripts\build-and-deploy.ps1 -NoCache -Pull
```

This is the code-change loop, not stack bring-up. Postgres, Neo4j and RabbitMQ keep running
throughout, with their volumes and every weight Credibility has learned intact. The script uses the
same compose file, env file and Compose project ('infra') as the documented command, so it acts on
the existing stack rather than a parallel one.

**Why not `up -d --build`.** `feed-prediction` and `feed-credibility` declare
`depends_on: feed-neo4j-seed`, so a plain `up` re-runs the graph seed container. Its `MERGE`
statements are idempotent, but `05-seed-conditioned-edges.cypher` *deletes* the unconditional edges
its conditioned edges supersede — a re-seed cannot restore anything an earlier version of those
files removed ([infra/README.md](../infra/README.md#re-seeding-an-existing-volume)). This script
deploys with `--no-deps`, so Compose starts nothing but the services named. Verified: the seed
container's `FinishedAt` is unchanged after a full run, including after force-recreating
`feed-prediction`.

What it adds over the raw command:

- **`docker compose config --quiet` before building.** A variable the compose file declares required
  (`POSTGRES_PASSWORD`, `NEO4J_AUTH`, `RABBITMQ_DEFAULT_PASS`, …) otherwise surfaces only when a
  container starts, after the build time has already been spent.
- **A read-only infrastructure check.** Fails, with the command to fix it, if a data store is not
  running and healthy — rather than deploying services that will crash-loop against it. It reports
  how the graph seed last completed but never runs it; a bad or missing seed is a warning, repeated
  in the final summary, since `feed-prediction` and `feed-credibility` read that graph.
- **Waiting on each service healthcheck**, reporting each one as it settles, and tailing the logs of
  anything that did not reach `healthy`. Exits non-zero in that case, so it is usable in a chain.

Naming a data store in `-Service` is refused, and no volume is ever removed — `-RemoveFirst` removes
the targeted *service* containers only. Bringing the infrastructure up in the first place stays a
separate, deliberate step:

```powershell
docker compose --env-file infra\.env -f infra\docker-compose.yml up -d
```

Full parameter list: `Get-Help scripts\build-and-deploy.ps1 -Detailed`.

## Asset registry

Run these after **any** edit to `assets.json`. Structural validation alone cannot catch a typo'd
ticker — the registry would load, the asset would predict, and scoring would then stall silently on
`PriceNotYetAvailable`.

| Script | Purpose |
|---|---|
| [validate-assets.py](validate-assets.py) | Validate registry structure, then probe every `provider_symbol` live and cross-check the provider's reported currency and timezone |
| [generate-asset-seed.py](generate-asset-seed.py) | Regenerate the Neo4j asset/group seed Cypher from the registry |

```bash
python scripts/validate-assets.py              # structure + live provider probe
python scripts/validate-assets.py --offline    # structure only (CI)
python scripts/generate-asset-seed.py          # regenerate the seed
python scripts/generate-asset-seed.py --check  # verify the seed matches the registry
```

Registry rules and field definitions: [REF-02](../requirements/REF-02-asset-registry.md).

## Knowledge graph snapshot

`infra/neo4j/init/*.cypher` seeds expert **priors**: every causal edge starts at `alpha=1.0,
beta=1.0`. The live graph then moves away from them — Credibility refines the Beta counts online per
scored prediction, and the offline structure learner adds asset-level edges that no seed file
declares at all. None of that exists on disk, so a re-seed onto a fresh volume silently reverts to
the priors. Measured 2026-08-30: 11 learned asset edges, one at `alpha=65, beta=8`.

These two scripts close that gap. They do **not** change how the stack boots — `feed-neo4j-seed`
still runs `init/*.cypher` exactly as before.

| Script | Purpose |
|---|---|
| [export-kg-snapshot.ps1](export-kg-snapshot.ps1) | Live graph → `infra/neo4j/snapshot/kg-snapshot.cypher` (overwritten each run) |
| [import-kg-snapshot.ps1](import-kg-snapshot.ps1) | Snapshot → live graph, after reporting exactly what changes |

```powershell
powershell -File scripts\export-kg-snapshot.ps1              # capture current state
powershell -File scripts\export-kg-snapshot.ps1 -Check       # fail if the snapshot is stale (CI)
powershell -File scripts\import-kg-snapshot.ps1 -DryRun      # show the diff, write nothing
powershell -File scripts\import-kg-snapshot.ps1              # apply new + changed edges
powershell -File scripts\import-kg-snapshot.ps1 -Prune       # also delete edges absent from the snapshot
```

**Scope.** The snapshot owns `CausalFactor` nodes, `CAUSES` edges and `CORRELATES_WITH` edges. It
deliberately excludes `Asset`, `AssetGroup` and `MEMBER_OF`: those come from `assets.json` via
`generate-asset-seed.py`, which stays the single source of truth for the registry. Restoring a fresh
volume is therefore `01-constraints` + `02-seed-assets` + the snapshot.

**The import always diffs before it writes**, classifying every row as `NEW` / `CHANGED` /
`UNCHANGED` / `MISSING_ENDPOINT` / `LIVE_ONLY`, because overwriting an edge that has learned *more*
than the snapshot throws away evidence that nothing downstream would report. It warns when the live
`alpha+beta` exceeds the snapshot's — the signal that the snapshot is stale and you want `export`,
not `import`.

`LIVE_ONLY` edges are **kept** unless you pass `-Prune`. An edge learned since the export is not
garbage, and deleting it is the only irreversible thing these scripts can do.

`MISSING_ENDPOINT` is reported rather than ignored: Cypher's `MATCH` binds nothing when a node is
absent and `cypher-shell` still exits 0, which is how an entire file of expert priors was once lost
unnoticed (see [09-verify-seed.cypher](../infra/neo4j/init/09-verify-seed.cypher)). It normally means
the registry moved on since the export — re-seed assets, then re-import.

**Verified end to end** on 2026-08-30 against a throwaway Neo4j seeded with constraints and assets
only: importing the snapshot reproduced all 31 factors, 248 `CAUSES` and 5 `CORRELATES_WITH` edges
with identical weights, Beta counts and timestamps, passed all six assertions in
`09-verify-seed.cypher`, and was a clean no-op on re-run.

## Operational reporting

These require the local Docker stack to be running. They read credentials from `infra/.env` and query
Postgres and Neo4j via `docker exec`. Each writes a timestamped HTML file next to itself.

| Script | Produces |
|---|---|
| [query-daily-activity.ps1](query-daily-activity.ps1) | `daily-activity-<date>.html` — news retrieved and predictions with their verified results |
| [debug-pipeline.ps1](debug-pipeline.ps1) | `debug-pipeline-<date>.html` — diagnostic report: ingestion/cleansing gaps, articles classified `OTHER`, predictions and verification state |
| [export-cleansing-audit.ps1](export-cleansing-audit.ps1) | `cleansing-audit-<date>.json` — each cleansing cluster with its raw articles (title, body excerpt, NLP extraction) for manual LLM verification of event-group classification |

```powershell
powershell -File scripts\query-daily-activity.ps1 -Date 2026-08-06
powershell -File scripts\debug-pipeline.ps1 -Date 2026-08-06
powershell -File scripts\export-cleansing-audit.ps1 -Date 2026-08-06
powershell -File scripts\export-cleansing-audit.ps1 -Date 2026-08-06 -OutputPath C:\tmp\audit.json
```

All date filtering is UTC, matching the containers' server time.

The generated `*.html` reports are point-in-time output, not documentation — they accumulate in this
folder and are safe to delete.

## Prediction accuracy

| Script | Produces |
|---|---|
| [measure-prediction-accuracy.py](measure-prediction-accuracy.py) | Console report: directional accuracy, per-event-type breakdown, confidence distribution, NEUTRAL rate, and per-asset-per-day volume |

```bash
python scripts/measure-prediction-accuracy.py
python scripts/measure-prediction-accuracy.py --since 2026-08-14
python scripts/measure-prediction-accuracy.py --split 2026-08-13T20:11:56Z
```

Reads `DATABASE_URL` from the environment, so load `infra/.env` first (unlike the PowerShell reports
above, which shell into the containers).

Two things this exists to prevent, both of which hid real defects:

- **Reading the raw correct/total ratio as accuracy.** About a quarter of outcomes are NEUTRAL — the
  price moved less than Verification's 0.3% deadband — so that ratio understates the system. The
  *directional* figure is the headline; the report shows both and how many outcomes were NEUTRAL.
- **Judging a change from a window that spans its deploy.** Use `--split <deploy timestamp>` to
  compare the windows either side; a single window covering both mixes old and new behaviour and
  cannot be attributed to either.

## Demo

| Script | Purpose |
|---|---|
| [demo_publish_event.py](demo_publish_event.py) | Publish one `EventDetected` (war de-escalation affecting oil transport) to `feed.events`, to exercise the prediction path by hand |

Requires the stack to be running. Useful for watching a `RESOLUTION`-polarity event flow through
Prediction without waiting for real news.
