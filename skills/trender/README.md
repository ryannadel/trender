# Trender Skill

Trender is a coding-agent skill that reuses broad `last30days`-style research and adds trend analysis over flexible time windows.

The skill bundles a compatible `last30days` engine under `vendor/last30days`, so it can retrieve evidence without requiring a separate `last30days` install.

## Install

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\install-skill.ps1 -Force
```

Or from the repository root:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\skills\trender\scripts\install-skill.ps1 -Force
```

## Examples

```bash
python3 scripts/trender.py --diagnose
python3 scripts/trender.py setup
python3 scripts/trender.py "agentic AI" --days=90
python3 scripts/trender.py "agentic AI" --compare=7,30 --emit=html
python3 scripts/trender.py "MCP servers" --from=2026-01-01 --to=2026-06-01 --emit=all
```

HTML reports open automatically when generated. Pass `--no-open` to only write the file.

Run `setup` once to let the bundled `last30days` engine discover browser cookies and write `~/.config/last30days/.env`. Run `--diagnose` to see which sources are currently available.

Set `LAST30DAYS_SKILL_DIR` only if you want to override the bundled engine with another checkout.

