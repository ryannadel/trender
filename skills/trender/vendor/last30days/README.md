# Vendored last30days runtime

This directory contains the trimmed `last30days` runtime used by Trender for community and engagement-source retrieval.

Trender calls `scripts/last30days.py` directly and supplies its own query plan. Standalone last30days skill instructions, comparison tests, evaluation tools, and packaging helpers are intentionally not bundled here so the Trender package exposes only one installable `SKILL.md`.

The runtime is licensed as MIT per the upstream last30days skill metadata.
