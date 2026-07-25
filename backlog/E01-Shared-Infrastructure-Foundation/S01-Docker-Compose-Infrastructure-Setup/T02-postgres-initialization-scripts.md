# T02: Postgres Initialization Scripts

## Context

This task creates the SQL initialization scripts that Postgres runs automatically when the container starts for the first time. The scripts create all six application databases and enable the `pgvector` extension in each. Without these scripts, every microservice would fail to connect to its database on startup.

This task belongs to the infrastructure story (S01). It produces files that are volume-mounted into the `feed-postgres` container defined in T01.

## Background

Postgres Docker images run every `.sql` and `.sh` file found in `/docker-entrypoint-initdb.d/` in alphabetical filename order, but only on the very first container start (when the data volume is empty). This is the standard pattern for seeding a containerized Postgres.

The system uses six separate databases — one per service — for data isolation:

| Database | Owning Service | Purpose |
|---|---|---|
| `raw_news` | Ingestion | Raw article storage |
| `events` | Cleansing | Deduplicated canonical events |
| `predictions` | Prediction | Graph-only market predictions and provenance |
| `prices` | Market Data | OHLC price observations |
| `outcomes` | Verification | Prediction vs actual comparison results |
| `credibility` | Credibility | Per-source and per-edge accuracy scores |

The `pgvector` extension must be enabled in the databases where vector similarity search will be used. The Cleansing service stores BGE-m3 embeddings (1024 dimensions) in the `events` database. Enabling it in all databases is safe and avoids surprises.

## Inputs

- Docker Compose mount defined in T01: `./postgres/` maps to `/docker-entrypoint-initdb.d/` inside the container.
- Postgres superuser credentials: `POSTGRES_USER=feed_user`, `POSTGRES_PASSWORD` from `.env`.
- The default database created by the image is `feed_admin` (set via `POSTGRES_DB`).

## Outputs

Files created in `infra/postgres/`:

```
infra/postgres/
  01-create-databases.sql
  02-enable-pgvector.sql
```

## Technical Requirements

### `01-create-databases.sql`

Create all six databases owned by `feed_user`:

```sql
CREATE DATABASE raw_news  OWNER feed_user ENCODING 'UTF8' LC_COLLATE 'en_US.utf8' LC_CTYPE 'en_US.utf8' TEMPLATE template0;
CREATE DATABASE events    OWNER feed_user ENCODING 'UTF8' LC_COLLATE 'en_US.utf8' LC_CTYPE 'en_US.utf8' TEMPLATE template0;
CREATE DATABASE predictions OWNER feed_user ENCODING 'UTF8' LC_COLLATE 'en_US.utf8' LC_CTYPE 'en_US.utf8' TEMPLATE template0;
CREATE DATABASE prices     OWNER feed_user ENCODING 'UTF8' LC_COLLATE 'en_US.utf8' LC_CTYPE 'en_US.utf8' TEMPLATE template0;
CREATE DATABASE outcomes   OWNER feed_user ENCODING 'UTF8' LC_COLLATE 'en_US.utf8' LC_CTYPE 'en_US.utf8' TEMPLATE template0;
CREATE DATABASE credibility OWNER feed_user ENCODING 'UTF8' LC_COLLATE 'en_US.utf8' LC_CTYPE 'en_US.utf8' TEMPLATE template0;
```

Requirements:
- Use `TEMPLATE template0` with explicit locale — required when specifying `LC_COLLATE`.
- Do NOT use `CREATE DATABASE IF NOT EXISTS` — that syntax does not exist in Postgres. The init script only runs once so it is safe to omit the guard.
- Ensure `feed_user` is the owner so application services connecting as `feed_user` have full DDL rights within their database.

### `02-enable-pgvector.sql`

Enable the `vector` extension in each database. Because `CREATE EXTENSION` can only run inside the target database, this script must use `\c` to switch connections:

```sql
\c raw_news
CREATE EXTENSION IF NOT EXISTS vector;

\c events
CREATE EXTENSION IF NOT EXISTS vector;

\c predictions
CREATE EXTENSION IF NOT EXISTS vector;

\c prices
CREATE EXTENSION IF NOT EXISTS vector;

\c outcomes
CREATE EXTENSION IF NOT EXISTS vector;

\c credibility
CREATE EXTENSION IF NOT EXISTS vector;
```

Requirements:
- Use `CREATE EXTENSION IF NOT EXISTS` to make the script idempotent.
- This script must be named `02-...` to run after `01-...` (alphabetical order).
- The locally built Postgres image (official `postgres:16` base + `pgvector`, see
  `infra/postgres-image/Dockerfile`) already has the extension binaries installed — only the
  `CREATE EXTENSION` activation is needed.

### No application table schemas here

This task only creates databases and enables extensions. Individual service table schemas (e.g., the `articles` table in `raw_news`) are created by each service's own Alembic migration, not here.

## Acceptance Criteria

1. After `docker compose up`, running `docker exec feed-postgres psql -U feed_user -l` lists all six databases: `raw_news`, `events`, `predictions`, `prices`, `outcomes`, `credibility`.
2. For each database, `SELECT extname FROM pg_extension WHERE extname = 'vector';` returns exactly one row.
3. The `feed_user` role has `CONNECT` and `CREATE` privileges on all six databases.
4. `docker compose down -v && docker compose up` re-runs the scripts cleanly on the fresh volume.
5. No errors or warnings appear in `docker logs feed-postgres` during initialization.
6. The default `feed_admin` database also exists (created automatically by the image via `POSTGRES_DB`).

## Implementation Notes

- The `\c dbname` meta-command works in `psql` scripts run by the init system because the init system uses `psql` internally. Do not use `\connect` — `\c` is the standard short form.
- The init scripts run as the `POSTGRES_USER` superuser, so `CREATE EXTENSION` and `CREATE DATABASE` both have sufficient privileges.
- If you need to test the scripts locally without Docker, you can run: `psql -U postgres -f 01-create-databases.sql` followed by `psql -U postgres -f 02-enable-pgvector.sql`. Adjust credentials as needed.
- The `pgvector` package is installed from the PGDG apt repository already configured in the
  official `postgres:16` image, so it tracks the latest pgvector release for Postgres 16. Pin the
  package version in `infra/postgres-image/Dockerfile` if a specific version is required.
- UTF8 encoding and `en_US.utf8` locale are used for consistency across all databases. If the host OS does not have this locale, the container still works because the locale is embedded in the Docker image.

## Definition of Done

- [x] `infra/postgres/01-create-databases.sql` exists and is valid SQL
- [x] `infra/postgres/02-enable-pgvector.sql` exists and is valid SQL
- [x] All six service-owned schemas are created after `docker compose up` (verified with `\dn`; frozen single-`feed`-DB topology)
- [x] `pgvector` extension is active in the `feed` database (verified: `vector` 0.8.5)
- [ ] `docker compose down -v && docker compose up` runs init scripts again without error
- [ ] No application table DDL is included (tables are managed by service migrations)
