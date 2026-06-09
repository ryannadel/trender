# Trender Skill

Trender is a coding-agent skill that reuses broad `last30days`-style research and adds trend analysis over flexible time windows.

## Examples

```bash
python3 scripts/trender.py "agentic AI" --days=90
python3 scripts/trender.py "agentic AI" --compare=7,30 --emit=html
python3 scripts/trender.py "MCP servers" --from=2026-01-01 --to=2026-06-01 --emit=all
```

Set `LAST30DAYS_SKILL_DIR` if the last30days skill is not installed next to this skill.

