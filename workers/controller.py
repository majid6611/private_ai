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


def post(
    url: str, data: dict[str, object], headers: dict[str, str], timeout: int = 20
) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(data, ensure_ascii=False).encode(),
        headers={"Content-Type": "application/json", **headers},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = response.read(8 * 1024 * 1024 + 1)
        if len(payload) > 8 * 1024 * 1024:
            raise ValueError("Response too large")
        return cast(dict[str, Any], json.loads(payload)) if payload else {}


def complete_body(job: dict[str, Any]) -> dict[str, Any]:
    """Run the isolated parser for one leased job and shape the completion payload."""
    if job.get("kind") == "pdf":
        if not isinstance(job.get("pdf_b64"), str):
            raise ValueError("Invalid job payload")
        result = post(parser + "/extract-pdf", {"pdf_b64": job["pdf_b64"]}, {}, timeout=60)
        if result.get("ok") is not True:
            return {"chunks": [], "failure": result.get("error", "MALFORMED")}
        return {
            "chunks": result["chunks"],
            "text": result["text"],
            "pages": result["pages"],
            "page_count": result["page_count"],
            "needs_ocr": bool(result.get("needs_ocr")),
            "extractor": result["extractor"],
        }
    if not isinstance(job.get("text"), str):
        raise ValueError("Invalid job payload")
    return {"chunks": post(parser + "/extract", {"text": job["text"]}, {})["chunks"]}


def main() -> None:
    key = Path(os.environ["INGESTION_KEY_FILE"]).read_text().strip()
    while True:
        try:
            result = post(backend + "/internal/v1/ingestion/lease", {}, {"X-Ingestion-Key": key})
            job = result.get("job")
            if job is None:
                time.sleep(3)
                continue
            if not isinstance(job, dict):
                raise ValueError("Invalid job payload")
            job_id = str(UUID(job["id"]))
            post(
                backend + "/internal/v1/ingestion/" + job_id + "/complete",
                {"lease_token": job["lease_token"], **complete_body(job)},
                {"X-Ingestion-Key": key},
            )
        except (OSError, ValueError, KeyError, TypeError):
            # No source text, lease token, service key or exception payload in logs.
            print("Ingestion attempt failed; lease will be retried.", flush=True)
            time.sleep(5)


if __name__ == "__main__":
    main()
