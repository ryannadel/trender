#!/usr/bin/env bash
set -euo pipefail

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
AGENT="copilot"
DESTINATION=""

usage() {
  cat <<'EOF'
Usage: install-skill.sh [--agent copilot|claude|codex|agents] [--dest PATH]

Defaults:
  copilot -> ~/.copilot/skills/trender
  claude  -> ~/.claude/skills/trender
  codex   -> ~/.agents/skills/trender
  agents  -> ~/.agents/skills/trender

For backwards compatibility, a single positional argument is treated as --dest.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --agent)
      AGENT="${2:?Missing value for --agent}"
      shift 2
      ;;
    --dest|--destination)
      DESTINATION="${2:?Missing value for $1}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      if [[ -z "$DESTINATION" ]]; then
        DESTINATION="$1"
        shift
      else
        echo "Unexpected argument: $1" >&2
        usage >&2
        exit 2
      fi
      ;;
  esac
done

if [[ -z "$DESTINATION" ]]; then
  case "$AGENT" in
    copilot) DESTINATION="$HOME/.copilot/skills/trender" ;;
    claude) DESTINATION="$HOME/.claude/skills/trender" ;;
    codex|agents) DESTINATION="$HOME/.agents/skills/trender" ;;
    *)
      echo "Unsupported agent: $AGENT" >&2
      usage >&2
      exit 2
      ;;
  esac
fi

if [[ -e "$DESTINATION" ]]; then
  rm -rf "$DESTINATION"
fi

mkdir -p "$(dirname "$DESTINATION")"
cp -R "$SKILL_DIR" "$DESTINATION"
find "$DESTINATION" -type d -name '__pycache__' -prune -exec rm -rf {} +
find "$DESTINATION" -type f \( -name '*.pyc' -o -name '*.pyo' \) -delete

echo "Installed Trender skill:"
echo "  $DESTINATION"
echo "Agent target: $AGENT"
echo
echo "Restart or refresh your agent host if it does not detect new skills automatically."

