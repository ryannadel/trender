# Trender Skill

Trender maps how a topic evolves across time. It combines host-agent bucketed web evidence with a bundled `last30days` community layer, then produces theme momentum, inflection moments, vocabulary drift, emerging entities, forward signals, Markdown synthesis, JSON data, and a self-contained HTML trend map.

Current version: **0.6.0**.

## Requirements

- Python 3.12+ for the bundled `last30days` community layer.
- Shell access from the host agent.
- Optional network access and source credentials for richer evidence collection.

## Install

From this skill directory on Windows:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\install-skill.ps1 -Agent copilot -Force
```

Or from the repository root:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\skills\trender\scripts\install-skill.ps1 -Agent copilot -Force
```

Use `-Agent claude` for `%USERPROFILE%\.claude\skills\trender` or `-Agent codex` for `%USERPROFILE%\.agents\skills\trender`.

On macOS/Linux:

```bash
bash ./scripts/install-skill.sh --agent copilot
bash ./scripts/install-skill.sh --agent claude
bash ./scripts/install-skill.sh --agent codex
```

## Configure sources

Run diagnostics:

```bash
python3 scripts/trender.py --diagnose
```

Run setup once to let the bundled `last30days` engine discover browser cookies and write its local environment file:

```bash
python3 scripts/trender.py setup
```

Set `LAST30DAYS_SKILL_DIR` only if you want to override the bundled engine with another checkout.

## Host-agent evidence

For best results, the host coding agent should gather **bucketed** web evidence before running Trender and pass it with `--agent-web-file`.

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

See `SKILL.md` Step 0 for the full evidence contract. Without `--agent-web-file`, Trender falls back to community signal only and the report will say so.

## Examples

```bash
# Default: compare last 30 days vs prior 5 months.
python3 scripts/trender.py "agentic AI" --agent-web-file ./agent-web.json

# Explicit comparison windows.
python3 scripts/trender.py "agentic AI" --compare=7,30 --agent-web-file ./agent-web.json
python3 scripts/trender.py "MCP servers" --compare=30,180 --emit=all

# Explicit date range.
python3 scripts/trender.py "MCP servers" --from=2026-01-01 --to=2026-06-01 --emit=all

# Network-free smoke test.
python3 scripts/trender.py "MCP servers" --mock --emit=all --no-open
```

HTML is the default output and opens automatically. Pass `--no-open` to only write the file.

By default, reports are written to `~/Documents/Trender`. Pass `--save-dir` or set `TRENDER_OUTPUT_DIR` to change the destination.
