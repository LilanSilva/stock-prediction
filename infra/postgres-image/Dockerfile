# Local Postgres image with the pgvector extension, built from the org-approved official base.
#
# Rationale: the community image `pgvector/pgvector` is blocked by org registry policy, but the
# Docker Official `postgres:16` image is allowed. The official image already has the PGDG apt
# repository configured, so pgvector installs directly from it.
FROM postgres:16
RUN apt-get update \
 && apt-get install -y --no-install-recommends postgresql-16-pgvector \
 && rm -rf /var/lib/apt/lists/*
