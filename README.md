# Trender Skill

Trender is a coding-agent skill that maps how a topic evolves across flexible time windows.

It uses a broad `last30days`-style research substrate for multi-source evidence, then adds Trender-specific analysis:

- flexible lookback windows and explicit date ranges
- comparison windows such as 7 vs 30 days
- trend direction classification: emerging, rising, stable, fading
- source diversity and momentum scoring
- Markdown, JSON, and self-contained HTML trend-map outputs

## Run locally

Install or clone `last30days-skill`, then point Trender at it:

```powershell
python .\skills\trender\scripts\trender.py "agentic AI" `
  --compare=7,30 `
  --last30days-dir C:\Users\rynadel\last30days-skill-src\skills\last30days `
  --emit=all
```

Or set:

```powershell
$env:LAST30DAYS_SKILL_DIR="C:\Users\rynadel\last30days-skill-src\skills\last30days"
```

Then run:

```powershell
python .\skills\trender\scripts\trender.py "MCP servers" --days=90 --emit=html
```

## Build skill archive

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\skills\trender\scripts\build-skill.ps1
```

The archive is written to:

```text
dist\trender.skill
```

