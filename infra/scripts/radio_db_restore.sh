#!/usr/bin/env bash
set -euo pipefail

DEFAULTS_FILE="${1:-$HOME/.mysql-radio.cnf}"
BACKUP_FILE="${2:-}"

if [[ ! -f "${DEFAULTS_FILE}" ]]; then
  echo "Missing MySQL defaults file: ${DEFAULTS_FILE}" >&2
  exit 1
fi

if [[ -z "${BACKUP_FILE}" || ! -f "${BACKUP_FILE}" ]]; then
  echo "Usage: $0 [defaults_file] <backup.sql.gz>" >&2
  exit 2
fi

gunzip -c "${BACKUP_FILE}" | mysql --defaults-extra-file="${DEFAULTS_FILE}"
echo "Restore complete from: ${BACKUP_FILE}"

