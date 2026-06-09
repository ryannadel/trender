#!/usr/bin/env bash
set -euo pipefail

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ROOT="$(cd "$SKILL_DIR/../.." && pwd)"
DIST="$ROOT/dist"
mkdir -p "$DIST"

cd "$SKILL_DIR"
zip -r "$DIST/trender.skill" SKILL.md scripts -x '*/__pycache__/*' '*.pyc'
echo "Built $DIST/trender.skill"

