"""Azure Functions worker API for Trender scans.

Rayfin hosts the authenticated UI and data model. This worker runs the
dependency-heavy Python pipeline: discovery, crawling, GPT-5.4 analysis,
trend scoring, and report rendering.
"""

from __future__ import annotations

import json
import traceback
from typing import Any

import azure.functions as func

from trender.service import scan_trends

app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)


@app.route(route="worker/health", methods=["GET"])
def health(_req: func.HttpRequest) -> func.HttpResponse:
    return json_response({"status": "ok", "service": "trender-worker"})


@app.route(route="worker/scan", methods=["POST"])
async def scan(req: func.HttpRequest) -> func.HttpResponse:
    try:
        payload = req.get_json()
        result = await run_scan(payload)
        return json_response({"status": "succeeded", "result": result.to_dict()})
    except ValueError as exc:
        return json_response({"status": "failed", "error": str(exc)}, status_code=400)
    except RuntimeError as exc:
        return json_response({"status": "failed", "error": str(exc)}, status_code=500)
    except Exception as exc:
        return json_response(
            {
                "status": "failed",
                "error": str(exc),
                "trace": traceback.format_exc(),
            },
            status_code=500,
        )


async def run_scan(payload: dict[str, Any]):
    topic = str(payload.get("topic") or "").strip()
    if not topic:
        raise ValueError("'topic' is required.")

    days = payload.get("days")
    start = payload.get("start") or payload.get("startDate")
    end = payload.get("end") or payload.get("endDate")
    max_results = int(payload.get("maxResults") or payload.get("max_results") or 30)

    return await scan_trends(
        topic,
        days=int(days) if days is not None else None,
        start=str(start) if start else None,
        end=str(end) if end else None,
        max_results=max_results,
        include_arxiv=bool(payload.get("includeArxiv", True)),
        include_github=bool(payload.get("includeGithub", True)),
        include_web=bool(payload.get("includeWeb", True)),
    )


def json_response(payload: dict[str, Any], status_code: int = 200) -> func.HttpResponse:
    return func.HttpResponse(
        json.dumps(payload, indent=2),
        status_code=status_code,
        mimetype="application/json",
    )

