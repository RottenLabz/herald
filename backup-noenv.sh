#!/usr/bin/env bash
set -euo pipefail

# Herald Angel public-safe source backup helper.
#
# Creates a NOENV source archive from the current repository directory.
# It intentionally excludes secrets, runtime data, virtual environments,
# Git history, caches, logs, database files, and previous backups.
#
# Usage:
#   ./backup-noenv.sh

BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_NAME="$(basename "$BASE_DIR")"
STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP_DIR="$BASE_DIR/backups"
OUT="$BACKUP_DIR/${PROJECT_NAME}-NOENV-${STAMP}.tar.gz"

mkdir -p "$BACKUP_DIR"

cd "$BASE_DIR"

tar \
  --exclude='./.env' \
  --exclude='./.env.*' \
  --exclude='./*.env' \
  --exclude='./.git' \
  --exclude='./.github' \
  --exclude='./.venv' \
  --exclude='./venv' \
  --exclude='./env' \
  --exclude='./data' \
  --exclude='./logs' \
  --exclude='./backups' \
  --exclude='./__pycache__' \
  --exclude='*/__pycache__' \
  --exclude='*/__pycache__/*' \
  --exclude='./.pytest_cache' \
  --exclude='*/.pytest_cache' \
  --exclude='*/.pytest_cache/*' \
  --exclude='./.mypy_cache' \
  --exclude='*/.mypy_cache' \
  --exclude='*/.mypy_cache/*' \
  --exclude='./.ruff_cache' \
  --exclude='*/.ruff_cache' \
  --exclude='*/.ruff_cache/*' \
  --exclude='./.cache' \
  --exclude='*/.cache' \
  --exclude='*/.cache/*' \
  --exclude='./*.pyc' \
  --exclude='*.pyc' \
  --exclude='./*.pyo' \
  --exclude='*.pyo' \
  --exclude='./*.db' \
  --exclude='*.db' \
  --exclude='./*.sqlite' \
  --exclude='*.sqlite' \
  --exclude='./*.sqlite3' \
  --exclude='*.sqlite3' \
  --exclude='./*.log' \
  --exclude='*.log' \
  --exclude='./*.tar' \
  --exclude='*.tar' \
  --exclude='./*.tar.gz' \
  --exclude='*.tar.gz' \
  --exclude='./*.tgz' \
  --exclude='*.tgz' \
  --exclude='./*.zip' \
  --exclude='*.zip' \
  --exclude='./*.pem' \
  --exclude='*.pem' \
  --exclude='./*.key' \
  --exclude='*.key' \
  --exclude='./id_rsa' \
  --exclude='*/id_rsa' \
  --exclude='./id_ed25519' \
  --exclude='*/id_ed25519' \
  -czf "$OUT" \
  .

echo "Created NOENV backup:"
echo "$OUT"
ls -lh "$OUT"

echo
echo "Checking archive for common secret/runtime patterns..."
if tar -tzf "$OUT" | grep -Ei '(^|/)\.env($|[./])|(^|/)\.git/|(^|/)\.venv/|(^|/)venv/|(^|/)env/|(^|/)data/|(^|/)logs/|(^|/)backups/|__pycache__/|\.pyc$|\.pyo$|\.db$|\.sqlite3?$|\.log$|\.pem$|\.key$|(^|/)id_rsa$|(^|/)id_ed25519$'; then
  echo "WARNING: backup may contain excluded secret/runtime files. Review before sharing."
  exit 1
fi

echo "NOENV backup check passed."