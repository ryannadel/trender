# Trender Skill

Trender is a coding-agent skill that maps how a topic evolves across flexible time windows.

It bundles a compatible `last30days` engine for broad multi-source evidence retrieval, then adds Trender-specific analysis:

- flexible lookback windows and explicit date ranges
- comparison windows such as 7 vs 30 days
- trend direction classification: emerging, rising, stable, fading
- source diversity and momentum scoring
- Markdown, JSON, and self-contained HTML trend-map outputs

## Install locally

From this repository:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass `
  -File .\skills\trender\scripts\install-skill.ps1 `
  -Force
```

That installs the skill to:

```text
%USERPROFILE%\.copilot\skills\trender
```

On macOS/Linux:

```bash
bash ./skills/trender/scripts/install-skill.sh
```

To install somewhere else:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass `
  -File .\skills\trender\scripts\install-skill.ps1 `
  -Destination "C:\path\to\skills\trender" `
  -Force
```

## Run locally

Trender includes its own compatible `last30days` engine under `skills/trender/vendor/last30days`, so no separate install is required for normal use:

```powershell
python .\skills\trender\scripts\trender.py --diagnose
python .\skills\trender\scripts\trender.py setup
```

`--diagnose` shows which sources are currently available. `setup` runs the bundled `last30days` setup flow, which can discover browser cookies and write `~/.config/last30days/.env`.

```powershell
python .\skills\trender\scripts\trender.py "agentic AI" `
  --compare=7,30 `
  --emit=all
```

To override the bundled engine with another checkout, set:

```powershell
$env:LAST30DAYS_SKILL_DIR="C:\Users\rynadel\last30days-skill-src\skills\last30days"
```

Then run. HTML is the default and opens automatically:

```powershell
python .\skills\trender\scripts\trender.py "MCP servers" --days=90
```

Use `--no-open` for scripts or CI:

```powershell
python .\skills\trender\scripts\trender.py "MCP servers" --days=90 --emit=html --no-open
```

To intentionally bypass the upstream preflight checks, pass `--skip-last30days-preflight`.

## Build skill archive

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\skills\trender\scripts\build-skill.ps1
```

The archive is written to:

```text
dist\trender.skill
```

