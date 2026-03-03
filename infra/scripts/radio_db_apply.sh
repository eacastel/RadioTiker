#!/usr/bin/env bash
set -euo pipefail

DEFAULTS_FILE="${1:-$HOME/.mysql-radio.cnf}"
MIGRATION_FILE="${2:-infra/db/mysql/001_init_radio_db.sql}"

if [[ ! -f "${DEFAULTS_FILE}" ]]; then
  echo "Missing MySQL defaults file: ${DEFAULTS_FILE}" >&2
  exit 1
fi

if [[ ! -f "${MIGRATION_FILE}" ]]; then
  echo "Missing migration file: ${MIGRATION_FILE}" >&2
  exit 1
fi

mysql --defaults-extra-file="${DEFAULTS_FILE}" < "${MIGRATION_FILE}"
echo "Applied migration: ${MIGRATION_FILE}"

