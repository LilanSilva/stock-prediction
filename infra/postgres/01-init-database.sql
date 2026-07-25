-- Feed Analyzer PostgreSQL initialization.
--
-- Authoritative topology (docs/functional-documents/system-overview-functional-document.md sec 4,
-- backlog/contract-freeze-overrides.md E01): ONE database with service-owned schemas, NOT a
-- database per service. This script runs once, on the first container start, inside the
-- POSTGRES_DB database (default: feed) as the POSTGRES_USER superuser.
--
-- Application table DDL is owned by each service's own migrations, not this file. This file only
-- creates schemas, enables pgvector, and grants schema ownership to the application role.

-- pgvector is used by the cleansing schema for BGE-m3 embeddings (vector(1024)).
CREATE EXTENSION IF NOT EXISTS vector;

-- Service-owned schemas. Each service writes only its own schema; cross-service reads use
-- approved views or messages. The Gateway later receives read-only grants (E08).
CREATE SCHEMA IF NOT EXISTS ingestion   AUTHORIZATION feed_user;
CREATE SCHEMA IF NOT EXISTS cleansing   AUTHORIZATION feed_user;
CREATE SCHEMA IF NOT EXISTS prediction  AUTHORIZATION feed_user;
CREATE SCHEMA IF NOT EXISTS market_data AUTHORIZATION feed_user;
CREATE SCHEMA IF NOT EXISTS verification AUTHORIZATION feed_user;
CREATE SCHEMA IF NOT EXISTS credibility AUTHORIZATION feed_user;
