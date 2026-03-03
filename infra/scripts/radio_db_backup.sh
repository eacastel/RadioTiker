#!/usr/bin/env bash
set -euo pipefail

DEFAULTS_FILE="${1:-$HOME/.mysql-radio.cnf}"
OUT_DIR="${2:-$HOME/backups/radio_db}"

if [[ ! -f "${DEFAULTS_FILE}" ]]; then
  echo "Missing MySQL defaults file: ${DEFAULTS_FILE}" >&2
  exit 1
fi

mkdir -p "${OUT_DIR}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT_FILE="${OUT_DIR}/radio_db_${STAMP}.sql.gz"

mysqldump \
  --defaults-extra-file="${DEFAULTS_FILE}" \
  --single-transaction \
  --quick \
  --no-tablespaces \
  --triggers \
  --set-gtid-purged=OFF \
  --databases radio_db | gzip -c > "${OUT_FILE}"

echo "Backup created: ${OUT_FILE}"
