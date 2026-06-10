# Trender Skill

Trender is a coding-agent skill that maps how a topic evolves across flexible time windows.

It combines:

- bundled `last30days` retrieval for social/community/engagement sources
- native Trender web research through OpenAI web search or Brave Search when configured
- adaptive time buckets and compare windows
- general-purpose trend grouping and momentum scoring
- visual self-contained HTML trend-map reports

## Install locally

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

## Configure sources

Trender works out of the box with the free sources available to bundled `last30days` (typically Reddit, Hacker News, Polymarket, and GitHub).

Run:

```powershell
python .\skills\trender\scripts\trender.py --diagnose
python .\skills\trender\scripts\trender.py setup
```

Native Trender web research runs automatically when either key is configured:

```powershell
$env:OPENAI_API_KEY="..."
# or
$env:BRAVE_API_KEY="..."
```

You can control native web research explicitly:

```powershell
python .\skills\trender\scripts\trender.py "MCP servers" --web-research=openai
python .\skills\trender\scripts\trender.py "MCP servers" --web-research=brave
python .\skills\trender\scripts\trender.py "MCP servers" --web-research=off
```

## Run

HTML is the default output and opens automatically:

```powershell
python .\skills\trender\scripts\trender.py "MCP servers" --days=90
```

Use `--no-open` for scripts or CI:

```powershell
python .\skills\trender\scripts\trender.py "MCP servers" --days=90 --no-open
```

Other examples:

```powershell
python .\skills\trender\scripts\trender.py "agentic AI" --compare=7,30 --emit=all
python .\skills\trender\scripts\trender.py "AI video tools" --from=2026-01-01 --to=2026-06-01
```

## Build skill archive

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\skills\trender\scripts\build-skill.ps1
```

The archive is written to:

```text
dist\trender.skill
```

