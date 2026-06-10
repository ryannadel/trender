---
name: trender
version: "0.3.1"
description: "Map how a topic is evolving across flexible time windows using last30days-style multi-source research plus trend scoring and HTML trend maps."
argument-hint: 'trender "agentic AI" --days=90 | trender "MCP servers" --compare=7,30 | trender "AI coding agents" --from=2026-01-01 --to=2026-06-01 --emit=html'
allowed-tools: Bash, Read, Write, AskUserQuestion, WebSearch
homepage: https://github.com/your-org/trender-skill
repository: https://github.com/your-org/trender-skill
author: ryannadel
license: MIT
user-invocable: true
metadata:
  openclaw:
    emoji: "📈"
    requires:
      env: []
      optionalEnv:
        - LAST30DAYS_SKILL_DIR
        - SCRAPECREATORS_API_KEY
        - OPENAI_API_KEY
        - XAI_API_KEY
        - OPENROUTER_API_KEY
        - PARALLEL_API_KEY
        - BRAVE_API_KEY
        - APIFY_API_TOKEN
        - AUTH_TOKEN
        - CT0
        - BSKY_HANDLE
        - BSKY_APP_PASSWORD
    bins:
      - python3
    tags:
      - trends
      - deep-research
      - last30days
      - multi-source
      - time-series
      - social-media
      - analysis
      - html
---

# Trender Skill

Trender uses broad `last30days`-style research as its evidence substrate, then adds trend-specific analysis:

- flexible time windows instead of a hardcoded last 30 days
- adaptive temporal buckets
- cross-source theme clustering
- momentum and direction scoring
- compare windows such as 7 vs 30 days or 30 vs 90 days
- optional self-contained HTML trend maps

## Usage

Run the engine script from this skill directory:

```bash
python3 "$SKILL_DIR/scripts/trender.py" "agentic AI" --days=90
python3 "$SKILL_DIR/scripts/trender.py" "agentic AI" --compare=7,30
python3 "$SKILL_DIR/scripts/trender.py" "MCP servers" --from=2026-01-01 --to=2026-06-01 --emit=json
python3 "$SKILL_DIR/scripts/trender.py" --diagnose
python3 "$SKILL_DIR/scripts/trender.py" setup
```

Trender ships with a compatible `last30days` engine under `vendor/last30days`, so no separate install is required for normal use.

Run `trender.py setup` once to let the bundled last30days engine discover browser cookies and write `~/.config/last30days/.env`. Run `trender.py --diagnose` to see which sources are currently available.

If you want to override the bundled engine, set:

```bash
export LAST30DAYS_SKILL_DIR=/path/to/last30days/skill
```

or pass:

```bash
python3 "$SKILL_DIR/scripts/trender.py" "agentic AI" --last30days-dir /path/to/last30days
```

## Output Contract

For normal user-facing output:

1. Run `scripts/trender.py`.
2. Pass through its Markdown synthesis.
3. HTML is the default output and opens automatically. Mention the saved HTML path.
4. Do not invent unsupported trend claims. Every trend should trace back to original source evidence in the output.

## What Makes Trender Different From last30days

`last30days` answers: "What are people saying recently?"

`trender` answers: "How is the signal moving over time, which themes are emerging or accelerating, and what evidence supports that?"

Trender should preserve the broad multi-source philosophy: Reddit, X, YouTube, TikTok, Instagram, HN, GitHub, Polymarket, Digg, Bluesky, web, and other sources supported by the installed `last30days` engine.

