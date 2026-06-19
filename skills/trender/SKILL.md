---
name: trender
version: "0.6.0"
description: "Map how a topic is evolving across time. The host agent does bucketed deep web research; Trender clusters, scores momentum, surfaces emerging entities and vocabulary drift, and renders a trend map."
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
        - XAI_API_KEY
        - OPENROUTER_API_KEY
        - PARALLEL_API_KEY
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

`last30days` answers: "What are people saying recently?"
`trender` answers: **"How is this changing over time, what's emerging or accelerating, and what evidence supports that?"**

Trender is **agent-native by design**. The host coding agent is the *primary* researcher: it does bucketed deep web research first, hands the JSON to Trender, and Trender layers community signal (via bundled `last30days`) plus trend analytics on top.

What Trender produces:
- **Inflection moments** — biggest week-over-week jumps in volume, with the headline that broke that week.
- **Emerging / Accelerating / Fading / Stable themes** — TF-IDF clusters with linear-regression slope and momentum.
- **Then → Now quote pairs** per theme.
- **Emerging entities** — capitalized n-grams new to the current window.
- **Vocabulary drift** — terms with biggest baseline → current frequency lift.
- **Forward signals** — predictions, roadmaps, forecasts, betting markets.
- A self-contained HTML trend map and a Markdown synthesis.

## STEP 0 — Bucketed deep research (REQUIRED)

Before invoking the script, the host agent **must** gather web evidence into five buckets and write JSON. Running Trender without `--agent-web-file` falls back to community-only signal and the report will say so explicitly.

The five buckets — each a separate set of web searches, not one mega-query:

| Bucket | What goes here | Example sources |
|---|---|---|
| `research` | Papers, benchmarks, evaluations, analyst reports | arXiv, Semantic Scholar, vendor research blogs |
| `implementations` | Releases, repos, demos, framework versions | GitHub, Show HN, vendor changelogs |
| `adoption` | Production use, enterprise rollouts, job postings, podcast/conf mentions | company blogs, podcast transcripts, Lever/Greenhouse, conf programs |
| `criticism` | Limits, problems, regressions, cost/risk analyses | Reddit threads, blog post-mortems, HN comments |
| `forecasts` | Predictions, roadmaps, RFCs, betting markets | Polymarket, vendor roadmaps, analyst forecasts |

For each bucket, run **at least two query variants** spanning the comparison window (e.g. one query for the prior 5 months, one for the last 30 days). Capture concrete `published_at` dates — items without dates contribute nothing to trend analysis.

Write to `agent-web.json`:

```json
{
  "buckets": {
    "research": [
      {"title": "...", "url": "https://...", "published_at": "2026-04-12",
       "summary": "...", "source": "arxiv", "relevance_score": 0.9}
    ],
    "implementations": [...],
    "adoption":        [...],
    "criticism":       [...],
    "forecasts":       [...]
  }
}
```

Legacy `{"items":[...]}` is still accepted; bucket is inferred from content.

## Usage

```bash
# RECOMMENDED — agent runs Step 0 first, then:
python3 "$SKILL_DIR/scripts/trender.py" "MCP servers" --agent-web-file ./agent-web.json

# With explicit comparison window (default is 30 vs 180 if nothing specified):
python3 "$SKILL_DIR/scripts/trender.py" "agentic AI" --compare=30,180 --agent-web-file ./agent-web.json
python3 "$SKILL_DIR/scripts/trender.py" "AI coding agents" --compare=7,30  --agent-web-file ./agent-web.json

# Explicit date range:
python3 "$SKILL_DIR/scripts/trender.py" "MCP servers" --from=2026-01-01 --to=2026-06-01

# Diagnostics + setup:
python3 "$SKILL_DIR/scripts/trender.py" --diagnose
python3 "$SKILL_DIR/scripts/trender.py" setup

# Smoke test without network:
python3 "$SKILL_DIR/scripts/trender.py" "MCP servers" --mock --emit=all --no-open
```

**Default window**: if you don't pass `--from`, `--days`, or `--compare`, Trender defaults to `--compare=30,180` (last 30 days vs prior 5 months) because trends require comparison.

Trender ships a compatible `last30days` engine in `vendor/last30days`, so no separate install is required. Run `setup` once to discover browser cookies; run `--diagnose` to see active sources.

## Source routing (community layer)

Trender no longer blasts every subquery at every source. Subqueries route by intent:

| Subquery | Sources |
|---|---|
| primary | reddit, hackernews, x, github, grounding |
| research-and-claims | hackernews, grounding, perplexity, github |
| implementations | github, hackernews, x |
| community-friction | reddit, x, hackernews, bluesky |
| forecasts | polymarket, grounding, x |
| broader-social (low weight) | youtube, tiktok, instagram, threads, pinterest, digg, xiaohongshu, truthsocial |

This is intentionally narrower than v0.5. Most "trend" signal does not come from TikTok; routing by intent reduces noise dramatically.

## Output contract

1. **Run Step 0 first.** Skipping it makes Trender a worse `last30days`.
2. **Pass `--agent-web-file`** with bucketed JSON.
3. Pass through Trender's Markdown synthesis. The HTML is the primary deliverable; mention its saved path.
4. Every claim in the report must trace back to evidence with a URL. Do not invent.
5. If a bucket comes back empty, the report will say so under "Coverage notes" — don't hide that.

## Override bundled last30days

```bash
export LAST30DAYS_SKILL_DIR=/path/to/last30days/skill
# or
python3 "$SKILL_DIR/scripts/trender.py" "topic" --last30days-dir /path/to/last30days
```

