"""Offset extraction for text and PDF; no credentials, document mounts, macros or URL fetching."""

import base64
import binascii
import json
import os
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

PDF_TIMEOUT_SECONDS = float(os.environ.get("PDF_TIMEOUT_SECONDS", "30"))
MAX_PDF_BYTES = 2 * 1024 * 1024
MAX_CHILD_OUTPUT = 10 * 1024 * 1024


def chunk_ranges(source: str) -> list[dict[str, int]]:
    chunks: list[dict[str, int]] = []
    start = 0
    while start < len(source):
        end = min(start + 1200, len(source))
        if end < len(source):
            boundary = source.rfind(" ", start + 700, end)
            if boundary > start:
                end = boundary
        chunks.append({"start": start, "end": end})
        if end == len(source):
            break
        start = end - 150
    return chunks


def extract_pdf(pdf: bytes) -> dict[str, Any]:
    """Run the resource-limited child; every failure becomes a coded verdict."""
    if not pdf.startswith(b"%PDF-") or len(pdf) > MAX_PDF_BYTES:
        return {"ok": False, "error": "MALFORMED"}
    try:
        child = subprocess.run(  # noqa: S603 - fixed argv, no shell, stdin only
            [sys.executable, "-I", str(Path(__file__).with_name("pdf_extract.py"))],
            input=pdf,
            capture_output=True,
            timeout=PDF_TIMEOUT_SECONDS,
            env={"PATH": "/usr/local/bin:/usr/bin", "PYTHONDONTWRITEBYTECODE": "1"},
            cwd="/",
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "TIMEOUT"}
    if child.returncode != 0 or len(child.stdout) > MAX_CHILD_OUTPUT:
        # Killed by a resource limit (memory/CPU) or crashed: treat as an unreadable file.
        return {"ok": False, "error": "TOO_LARGE" if child.returncode in (-9, -11, -24) else "MALFORMED"}
    try:
        result = json.loads(child.stdout)
    except ValueError:
        return {"ok": False, "error": "MALFORMED"}
    if not isinstance(result, dict) or result.get("ok") is not True:
        error = result.get("error") if isinstance(result, dict) else None
        allowed = ("ENCRYPTED", "MALFORMED", "TOO_LARGE", "TOO_MANY_PAGES", "TIMEOUT")
        return {"ok": False, "error": error if error in allowed else "MALFORMED"}
    if result["image_only"]:
        return {
            "ok": True,
            "needs_ocr": True,
            "text": "",
            "pages": [],
            "page_count": result["page_count"],
            "chunks": [],
            "extractor": result["extractor"],
        }
    result["needs_ocr"] = False
    result["chunks"] = chunk_ranges(result["text"])
    del result["image_only"]
    return result


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args: object) -> None:
        pass

    def reply(self, payload: dict[str, Any]) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self) -> None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if self.path == "/extract-pdf":
                if not 0 < length <= 4 * 1024 * 1024:
                    self.send_error(400)
                    return
                self.connection.settimeout(10)
                encoded = json.loads(self.rfile.read(length))["pdf_b64"]
                self.reply(extract_pdf(base64.b64decode(encoded, validate=True)))
                return
            if self.path != "/extract" or not 0 < length <= 8 * 1024 * 1024:
                self.send_error(400)
                return
            self.connection.settimeout(10)
            body = json.loads(self.rfile.read(length))
            source = body["text"]
            if not isinstance(source, str) or not 0 < len(source.encode("utf-8")) <= 2097152:
                self.send_error(400)
                return
            self.reply({"chunks": chunk_ranges(source)})
        except (ValueError, KeyError, TypeError, TimeoutError, binascii.Error):
            self.send_error(400)


if __name__ == "__main__":
    HTTPServer(("0.0.0.0", 8090), Handler).serve_forever()
