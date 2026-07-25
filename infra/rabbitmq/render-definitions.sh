#!/usr/bin/env bash
# Render the RabbitMQ definitions file at container startup, injecting the broker user and its
# permissions from environment variables. The password is never stored in a tracked file: its
# salted SHA-256 hash is computed here at runtime with `rabbitmqctl hash_password`.
set -euo pipefail

# The mounted, tracked definitions file is the topology-only template (users/permissions are empty
# arrays in git); the rendered copy with the injected user is written to the data volume.
TEMPLATE="${DEFINITIONS_TEMPLATE:-/etc/rabbitmq/definitions.json}"
OUTPUT="${DEFINITIONS_OUTPUT:-/var/lib/rabbitmq/rendered-definitions.json}"

USER="${RABBITMQ_DEFAULT_USER:?RABBITMQ_DEFAULT_USER must be set}"
PASS="${RABBITMQ_DEFAULT_PASS:?RABBITMQ_DEFAULT_PASS must be set}"

# `hash_password` runs offline (no node required) and prints the hash on the last line.
HASH="$(rabbitmqctl hash_password "$PASS" | tail -n1)"

USERS_JSON="[{\"name\":\"${USER}\",\"password_hash\":\"${HASH}\",\"hashing_algorithm\":\"rabbit_password_hashing_sha256\",\"tags\":[\"administrator\"]}]"
PERMISSIONS_JSON="[{\"user\":\"${USER}\",\"vhost\":\"/\",\"configure\":\".*\",\"write\":\".*\",\"read\":\".*\"}]"

# The template keeps valid empty arrays so it passes JSON validation; replace them in place.
# `|` is a safe sed delimiter: the base64 hash alphabet (A-Za-z0-9+/=) never contains it.
sed -e "s|\"users\": \[\]|\"users\": ${USERS_JSON}|" \
    -e "s|\"permissions\": \[\]|\"permissions\": ${PERMISSIONS_JSON}|" \
  "$TEMPLATE" > "$OUTPUT"

echo "Rendered RabbitMQ definitions for user '${USER}' at ${OUTPUT}"
