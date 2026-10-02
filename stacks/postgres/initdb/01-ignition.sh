#!/bin/bash
# Creates the Ignition database + role.
#
# Scripts in /docker-entrypoint-initdb.d run ONLY when the data directory is
# empty, i.e. the very first time this stack starts. Editing this file later
# has no effect unless you delete the postgres_data volume.
set -e

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
	CREATE ROLE "${IGNITION_DB_USER}" WITH LOGIN PASSWORD '${IGNITION_DB_PASSWORD}';
	CREATE DATABASE "${IGNITION_DB_NAME}" OWNER "${IGNITION_DB_USER}";
EOSQL

# Postgres 15+ revoked the implicit CREATE grant on the public schema, so the
# owner must be granted it explicitly or Ignition cannot create its tables.
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "${IGNITION_DB_NAME}" <<-EOSQL
	GRANT ALL ON SCHEMA public TO "${IGNITION_DB_USER}";
	ALTER SCHEMA public OWNER TO "${IGNITION_DB_USER}";
EOSQL

echo "initdb: created database '${IGNITION_DB_NAME}' owned by role '${IGNITION_DB_USER}'"
