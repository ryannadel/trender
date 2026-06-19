# Trender Skill

Trender is an agent-native skill for mapping how a topic changes over time. It combines bucketed web evidence from the host coding agent with a bundled `last30days` community-research layer, then clusters themes, scores momentum, surfaces emerging entities and vocabulary drift, and renders a self-contained HTML trend map.

Current version: **0.7.0**.

## What it does

- Compares flexible time windows, including `--compare=7,30`, `--compare=30,180`, `--days`, or explicit `--from` / `--to` ranges.
- Uses host-agent web research via `--agent-web-file` for higher-quality evidence.
- Bundles a compatible `last30days` engine in `skills/trender/vendor/last30days`.
- Routes community subqueries by intent instead of sending every query to every source.
- Produces inflection moments, accelerating/fading/stable themes, then-to-now quote pairs, emerging entities, vocabulary drift, forward signals, an agent-authored BLUF/forward outlook when provided, Markdown synthesis, JSON data, and an HTML report.
- Opens the HTML report automatically by default.

Trender itself does not call OpenAI, Brave, or similar web APIs directly. For deep web research, the host agent should use its own web/deep-research tools, save evidence to JSON, and pass that file to Trender. Optional credentials can improve the bundled `last30days` sources.

## Requirements

- Python 3.12+ for the bundled `last30days` community layer.
- Shell access from the host agent.
- Optional network access and credentials for richer source coverage.

Useful environment variables:

| Variable | Purpose |
|---|---|
| `TRENDER_OUTPUT_DIR` | Override the default report directory, `~/Documents/Trender`. |
| `TRENDER_AGENT_WEB_FILE` | Default `--agent-web-file` path. |
| `LAST30DAYS_SKILL_DIR` | Override the bundled `last30days` engine. |
| `TRENDER_LAST30DAYS_PYTHON` | Python executable to use for `last30days` when multiple versions are installed. |

## Agent-authored narrative

The host coding agent can provide a bottom-line-up-front (BLUF) summary and forward outlook with `--narrative-file`. This lets the final report lead with the agent's synthesized takeaways while Trender keeps the evidence-backed trend map, computed signals, and traceable source lists.

If no narrative file is provided, Trender renders a clearly labeled auto-generated fallback based on the strongest computed signals.

## Repository layout

```text
plugin.json                    GitHub Copilot CLI plugin manifest
.claude-plugin/plugin.json      Claude Code plugin manifest
.codex-plugin/plugin.json       Codex plugin manifest
skills/trender/SKILL.md         Agent skill instructions and metadata
skills/trender/README.md        README packaged inside the skill archive
skills/trender/scripts/         CLI, installer, and build scripts
skills/trender/vendor/          Bundled last30days engine
dist/trender.skill              Built direct Agent Skill archive
dist/trender-plugin.zip         Built plugin archive
```

## Install as an agent skill

Install as a personal GitHub Copilot CLI skill on Windows:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass `
  -File .\skills\trender\scripts\install-skill.ps1 `
  -Agent copilot `
  -Force
```

Install for Claude Code or Codex on Windows:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass `
  -File .\skills\trender\scripts\install-skill.ps1 `
  -Agent claude `
  -Force

powershell -NoProfile -ExecutionPolicy Bypass `
  -File .\skills\trender\scripts\install-skill.ps1 `
  -Agent codex `
  -Force
```

Default install locations:

```text
GitHub Copilot CLI: %USERPROFILE%\.copilot\skills\trender
Claude Code:        %USERPROFILE%\.claude\skills\trender
Codex / agents:     %USERPROFILE%\.agents\skills\trender
```

On macOS/Linux:

```bash
bash ./skills/trender/scripts/install-skill.sh --agent copilot
bash ./skills/trender/scripts/install-skill.sh --agent claude
bash ./skills/trender/scripts/install-skill.sh --agent codex
```

After direct skill installation, reload or restart the host if needed:

```text
Copilot CLI: /skills reload
Claude Code: /reload-plugins or restart
Codex: restart if the skill is not detected automatically
```

## Install as a plugin

For plugin distribution, install the repository root:

```bash
# GitHub Copilot CLI
copilot plugin install .

# Claude Code local plugin test
claude --plugin-dir .
```

Codex can install from a marketplace entry that points at this repository or local plugin folder. The Codex manifest is in `.codex-plugin/plugin.json`.

## Configure sources

Trender works without separate setup in `--mock` mode and can use free bundled `last30days` sources such as Reddit, Hacker News, Polymarket, and GitHub when available.

Run diagnostics:

```powershell
python .\skills\trender\scripts\trender.py --diagnose
```

Run setup once to let the bundled `last30days` layer discover browser cookies and write its local environment file:

```powershell
python .\skills\trender\scripts\trender.py setup
```

## Add host-agent web evidence

For best results, have the host coding agent gather **bucketed** evidence first. Use separate web searches for each bucket: research, implementations, adoption, criticism, and forecasts. Each item should include a concrete `published_at` date because undated evidence cannot contribute to trend analysis.

```json
{
  "buckets": {
    "research": [
      {
        "title": "...",
        "url": "https://...",
        "published_at": "YYYY-MM-DD",
        "summary": "...",
        "source": "arxiv",
        "relevance_score": 0.9
      }
    ],
    "implementations": [],
    "adoption": [],
    "criticism": [],
    "forecasts": []
  }
}
```

The legacy `{"items":[...]}` shape is still accepted, with buckets inferred from content. See `skills/trender/SKILL.md` Step 0 for the full evidence contract.

Then run:

```powershell
python .\skills\trender\scripts\trender.py "MCP servers" --agent-web-file .\agent-web.json
```

## Run

HTML is the default output and opens automatically:

```powershell
python .\skills\trender\scripts\trender.py "MCP servers" --days=90
```

Use `--no-open` for scripts, CI, or unattended runs:

```powershell
python .\skills\trender\scripts\trender.py "MCP servers" --days=90 --no-open
```

Other examples:

```powershell
# Default when no window is specified: compare last 30 days vs prior 5 months.
python .\skills\trender\scripts\trender.py "agentic AI" --agent-web-file .\agent-web.json

# Include an agent-authored BLUF and forward outlook.
python .\skills\trender\scripts\trender.py "agentic AI" --agent-web-file .\agent-web.json --narrative-file .\narrative.json

# Compare two lookback windows.
python .\skills\trender\scripts\trender.py "agentic AI" --compare=7,30 --agent-web-file .\agent-web.json
python .\skills\trender\scripts\trender.py "AI video tools" --compare=30,180 --emit=all

# Analyze an explicit date range.
python .\skills\trender\scripts\trender.py "AI coding agents" --from=2026-01-01 --to=2026-06-01

# Network-free smoke test.
python .\skills\trender\scripts\trender.py "MCP servers" --mock --emit=all --no-open
```

By default, reports are written to `~/Documents/Trender`:

```text
<topic>-trend-map.html
<topic>-trend-map.json
```

Set `TRENDER_OUTPUT_DIR` or pass `--save-dir` to change the destination.

## Build skill archives

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\skills\trender\scripts\build-skill.ps1
```

On macOS/Linux:

```bash
bash ./skills/trender/scripts/build-skill.sh
```

Archives are written to:

```text
dist\trender.skill       Direct Agent Skill archive
dist\trender-plugin.zip  Plugin archive with Copilot, Claude, and Codex manifests
```

## Public release checklist

Before publishing, make sure the repository includes a root license file matching the MIT license declared in the manifests, rebuild `dist/` after documentation or code changes, and run a mock smoke test:

```powershell
python .\skills\trender\scripts\trender.py "MCP servers" --mock --emit=all --no-open
```
