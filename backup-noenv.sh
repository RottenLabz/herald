#!/usr/bin/env bash
set -euo pipefail
# Compatibility entry point: source-only export, not a private runtime backup.
# Pass --output /an/already/existing/directory/reviewed-source.tar.gz.
BASE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$BASE_DIR/source_export.py" --root "$BASE_DIR" "$@"
