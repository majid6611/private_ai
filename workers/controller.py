"""Narrow job API identity only. No database credentials or document filesystem mount."""

import json
import os
import time
import urllib.request
from pathlib import Path
from typing import Any, cast
from uuid import UUID

backend = os.environ.get("BACKEND_URL", "http://backend:8000")
parser = os.environ.get("PARSER_URL", "http://parser:8090")
key = Path(os.environ["INGESTION_KEY_FILE"]).read_text().strip()


def post(url: str, data: dict[str, object], headers: dict[str, str]) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(data, ensure_ascii=False).encode(),
        headers={"Content-Type": "application/json", **headers},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        payload = response.read(8 * 1024 * 1024 + 1)
        if len(payload) > 8 * 1024 * 1024:
            raise ValueError("Response too large")
        return cast(dict[str, Any], json.loads(payload)) if payload else {}


while True:
    try:
        result = post(backend + "/internal/v1/ingestion/lease", {}, {"X-Ingestion-Key": key})
        job = result.get("job")
        if job is None:
            time.sleep(3)
            continue
        if not isinstance(job, dict) or not isinstance(job.get("text"), str):
            raise ValueError("Invalid job payload")
        job_id = str(UUID(job["id"]))
        chunks = post(parser + "/extract", {"text": job["text"]}, {})
        post(
            backend + "/internal/v1/ingestion/" + job_id + "/complete",
            {"lease_token": job["lease_token"], "chunks": chunks["chunks"]},
            {"X-Ingestion-Key": key},
        )
    except (OSError, ValueError, KeyError, TypeError):
        # No source text, lease token, service key or exception payload in logs.
        print("Ingestion attempt failed; lease will be retried.", flush=True)
        time.sleep(5)
