# Trender Skill

Trender is a coding-agent skill that maps how a topic evolves across flexible time windows.

It is self-contained as a skill:

- bundled `last30days` retrieval for social/community/engagement sources
- host-agent web evidence via `--agent-web-file`
- adaptive time buckets and compare windows
- general-purpose trend grouping and momentum scoring
- visual self-contained HTML trend-map reports

The script does not call OpenAI, Brave, or other web APIs directly. For deep web research, the coding agent that is running the skill should use its own web/deep-research tools, save evidence to JSON, and pass that file to Trender.

## Install as an agent skill

Trender follows the open Agent Skills layout: a skill directory with one `SKILL.md`, optional `scripts/`, and optional resources. The repository also includes plugin manifests for Claude Code, Codex, and GitHub Copilot CLI distribution.

Install as a personal GitHub Copilot CLI skill on Windows:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass `
  -File .\skills\trender\scripts\install-skill.ps1 `
  -Agent copilot `
  -Force
```

Install for Claude Code or Codex:

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

## Install as a plugin

For distribution, install the repository root as a plugin:

```bash
# GitHub Copilot CLI
copilot plugin install .

# Claude Code local plugin test
claude --plugin-dir .
```

Codex can install the plugin from a marketplace entry that points at this repository or local plugin folder. The Codex manifest is in `.codex-plugin/plugin.json`.

After direct skill installation, reload or restart the host if needed:

```text
Copilot CLI: /skills reload
Claude Code: /reload-plugins or restart
Codex: restart if the skill is not detected automatically
```

## Configure sources

Trender works out of the box with the free sources available to bundled `last30days` (typically Reddit, Hacker News, Polymarket, and GitHub).

Run:

```powershell
python .\skills\trender\scripts\trender.py --diagnose
python .\skills\trender\scripts\trender.py setup
```

## Add host-agent web evidence

Have the coding agent gather **bucketed** evidence — research, implementations, adoption, criticism, forecasts — and write a JSON file:

```json
{
  "buckets": {
    "research":        [{"title":"...", "url":"https://...", "published_at":"YYYY-MM-DD", "summary":"...", "source":"arxiv", "relevance_score":0.9}],
    "implementations": [],
    "adoption":        [],
    "criticism":       [],
    "forecasts":       []
  }
}
```

The legacy `{"items":[...]}` shape is still accepted (bucket inferred from content). See `skills/trender/SKILL.md` Step 0 for the full contract.

Then run:

```powershell
python .\skills\trender\scripts\trender.py "MCP servers" --agent-web-file .\agent-web.json
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

Archives are written to:

```text
dist\trender.skill       direct Agent Skill archive
dist\trender-plugin.zip  plugin archive with Copilot, Claude, and Codex manifests
```
