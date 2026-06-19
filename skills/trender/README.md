# Trender Skill

Trender is a coding-agent skill that reuses broad `last30days`-style research and adds trend analysis over flexible time windows.

The skill bundles a compatible `last30days` engine under `vendor/last30days`, so it can retrieve evidence without requiring a separate `last30days` install.

For deep web research, use the host coding agent's web/deep-research tools, write findings to JSON, and pass that file with `--agent-web-file`.

The host coding agent also authors the **bottom-line-up-front (BLUF)** summary — a few scannable bullets that lead the report — and an optional forward outlook, handing them to Trender via `--narrative-file`. If omitted, Trender renders a clearly-labeled auto-generated fallback derived from the strongest computed signals.

## Install

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\install-skill.ps1 -Agent copilot -Force
```

Or from the repository root:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\skills\trender\scripts\install-skill.ps1 -Agent copilot -Force
```

Use `-Agent claude` for `~/.claude/skills/trender` or `-Agent codex` for `~/.agents/skills/trender`.

## Examples

```bash
python3 scripts/trender.py --diagnose
python3 scripts/trender.py setup

# Default: compare last 30 days vs prior 5 months
python3 scripts/trender.py "agentic AI" --agent-web-file ./agent-web.json

# With an agent-authored bottom line + forward outlook
python3 scripts/trender.py "agentic AI" --agent-web-file ./agent-web.json --narrative-file ./narrative.json

# Explicit comparison windows
python3 scripts/trender.py "agentic AI" --compare=7,30  --agent-web-file ./agent-web.json
python3 scripts/trender.py "MCP servers" --from=2026-01-01 --to=2026-06-01 --emit=all

# Network-free smoke test
python3 scripts/trender.py "MCP servers" --mock --emit=all --no-open
```

HTML is the default output and opens automatically. Pass `--no-open` to only write the file.

The host coding agent is expected to gather **bucketed** web evidence (research / implementations / adoption / criticism / forecasts) and pass it via `--agent-web-file`. See `SKILL.md` Step 0 for the JSON schema. Without it, Trender falls back to community signal only and the report will say so.

Run `setup` once to let the bundled `last30days` engine discover browser cookies and write `~/.config/last30days/.env`. Run `--diagnose` to see which sources are currently available.

Set `LAST30DAYS_SKILL_DIR` only if you want to override the bundled engine with another checkout.

