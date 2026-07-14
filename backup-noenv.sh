#!/usr/bin/env bash
set -euo pipefail

# Herald Angel public-safe source backup helper.
#
# Creates a NOENV source archive from the current repository directory.
# It excludes secrets, runtime data, virtual environments, Git history,
# caches, logs, databases, private keys, and old backups.
# The safe public template .env.example is deliberately retained.
#
# Usage:
#   ./backup-noenv.sh

BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_NAME="$(basename "$BASE_DIR")"
STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP_DIR="$BASE_DIR/backups"
OUT="$BACKUP_DIR/${PROJECT_NAME}-NOENV-${STAMP}.tar.gz"
TMP_TAR="$BACKUP_DIR/.${PROJECT_NAME}-NOENV-${STAMP}.tar"

mkdir -p "$BACKUP_DIR"
rm -f "$TMP_TAR" "$OUT"
trap 'rm -f "$TMP_TAR"' EXIT

cd "$BASE_DIR"

# Build an uncompressed archive first. This lets us exclude every .env.* file
# safely, then append only the known-safe .env.example template afterwards.
tar \
  --exclude='./.env' \
  --exclude='./.env.*' \
  --exclude='./*.env' \
  --exclude='./.git' \
  --exclude='./.venv' \
  --exclude='./venv' \
  --exclude='./env' \
  --exclude='./.tox' \
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
  -cf "$TMP_TAR" \
  .

if [[ ! -f .env.example ]]; then
  echo "ERROR: .env.example is missing; refusing to create an incomplete source backup."
  exit 1
fi

tar -rf "$TMP_TAR" .env.example
gzip -n -c "$TMP_TAR" > "$OUT"
rm -f "$TMP_TAR"
trap - EXIT

echo "Created NOENV backup:"
echo "$OUT"
ls -lh "$OUT"

echo
echo "Checking archive for common secret/runtime patterns..."
SUSPICIOUS="$({
  tar -tzf "$OUT" \
    | grep -Ei '(^|/)\.env($|[./])|(^|/)\.git/|(^|/)\.venv/|(^|/)venv/|(^|/)env/|(^|/)data/|(^|/)logs/|(^|/)backups/|__pycache__/|\.pyc$|\.pyo$|\.db$|\.sqlite3?$|\.log$|\.pem$|\.key$|(^|/)id_rsa$|(^|/)id_ed25519$' \
    | grep -Ev '(^|/)\.env\.example$' \
    || true
})"

if [[ -n "$SUSPICIOUS" ]]; then
  printf '%s\n' "$SUSPICIOUS"
  echo "WARNING: backup contains a secret/runtime-looking path. Archive removed."
  rm -f "$OUT"
  exit 1
fi

if ! tar -tzf "$OUT" | grep -Eq '(^|/)\.env\.example$'; then
  echo "WARNING: safe public template .env.example is missing. Archive removed."
  rm -f "$OUT"
  exit 1
fi

echo "NOENV backup check passed."

if command -v sha256sum >/dev/null 2>&1; then
  sha256sum "$OUT"
fi
