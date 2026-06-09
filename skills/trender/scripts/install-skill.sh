#!/usr/bin/env bash
set -euo pipefail

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DESTINATION="${1:-$HOME/.copilot/skills/trender}"

if [[ -e "$DESTINATION" ]]; then
  rm -rf "$DESTINATION"
fi

mkdir -p "$(dirname "$DESTINATION")"
cp -R "$SKILL_DIR" "$DESTINATION"
find "$DESTINATION" -type d -name '__pycache__' -prune -exec rm -rf {} +
find "$DESTINATION" -type f \( -name '*.pyc' -o -name '*.pyo' \) -delete

echo "Installed Trender skill:"
echo "  $DESTINATION"
echo
echo "Restart or refresh your agent host if it does not detect new skills automatically."

