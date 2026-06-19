#!/usr/bin/env bash
set -euo pipefail

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ROOT="$(cd "$SKILL_DIR/../.." && pwd)"
DIST="$ROOT/dist"
ARCHIVE="$DIST/trender.skill"
PLUGIN_ARCHIVE="$DIST/trender-plugin.zip"

mkdir -p "$DIST"

python3 - "$SKILL_DIR" "$ROOT" "$ARCHIVE" "$PLUGIN_ARCHIVE" <<'PY'
from pathlib import Path
import sys
import zipfile

skill_dir = Path(sys.argv[1])
root_dir = Path(sys.argv[2])
archive = Path(sys.argv[3])
plugin_archive = Path(sys.argv[4])

def write_skill(zf, prefix=""):
    for path in [skill_dir / "SKILL.md", skill_dir / "README.md"]:
        if path.exists():
            zf.write(path, str(Path(prefix) / path.relative_to(skill_dir)))
    for skill_root in [skill_dir / "scripts", skill_dir / "vendor", skill_dir / "agents"]:
        if not skill_root.exists():
            continue
        for path in skill_root.rglob("*"):
            if path.is_dir() or "__pycache__" in path.parts or path.suffix in {".pyc", ".pyo"}:
                continue
            zf.write(path, str(Path(prefix) / path.relative_to(skill_dir)))

for path in [archive, plugin_archive]:
    if path.exists():
        path.unlink()

with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zf:
    write_skill(zf)

with zipfile.ZipFile(plugin_archive, "w", compression=zipfile.ZIP_DEFLATED) as zf:
    for path in [root_dir / "plugin.json", root_dir / "README.md"]:
        if path.exists():
            zf.write(path, path.relative_to(root_dir))
    for plugin_dir in [root_dir / ".claude-plugin", root_dir / ".codex-plugin"]:
        if not plugin_dir.exists():
            continue
        for path in plugin_dir.rglob("*"):
            if path.is_file():
                zf.write(path, path.relative_to(root_dir))
    write_skill(zf, "skills/trender")
PY

echo "Built $ARCHIVE"
echo "Built $PLUGIN_ARCHIVE"
