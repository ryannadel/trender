#!/usr/bin/env bash
set -euo pipefail

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ROOT="$(cd "$SKILL_DIR/../.." && pwd)"
DIST="$ROOT/dist"
ARCHIVE="$DIST/trender.skill"

mkdir -p "$DIST"

python3 - "$SKILL_DIR" "$ARCHIVE" <<'PY'
from pathlib import Path
import sys
import zipfile

skill_dir = Path(sys.argv[1])
archive = Path(sys.argv[2])

if archive.exists():
    archive.unlink()

with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zf:
    for path in [skill_dir / "SKILL.md", skill_dir / "README.md"]:
        if path.exists():
            zf.write(path, path.relative_to(skill_dir))
    scripts_dir = skill_dir / "scripts"
    for path in scripts_dir.rglob("*"):
        if path.is_dir() or "__pycache__" in path.parts or path.suffix in {".pyc", ".pyo"}:
            continue
        zf.write(path, path.relative_to(skill_dir))
PY

echo "Built $ARCHIVE"

