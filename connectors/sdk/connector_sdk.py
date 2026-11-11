"""Server helper for connector protocol v1 (standard library only).

A connector is a container that answers a few JSON calls from the core. This module handles
what every connector must get right so authors only write the data access:

* every call needs the shared bearer key (constant-time comparison);
* request and response sizes are bounded; one request is served at a time;
* errors are typed and never include exception text (it may contain credentials or data);
* request bodies are never logged.
"""

import hmac
import json
import logging
import time
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

PROTOCOL = "1"
MAX_REQUEST_BYTES = 1_048_576
MAX_RESPONSE_BYTES = 8 * 1024 * 1024

log = logging.getLogger("connector")

Handler = Callable[[dict[str, Any]], dict[str, Any]]


class ConnectorError(Exception):
    """A typed, safe-to-return failure. Messages must not contain secrets or row data."""

    STATUS = {
        "bad_request": 400,
        "unauthorized": 401,
        "forbidden": 403,
        "not_found": 404,
        "gone": 410,
        "retry_later": 503,
        "unsupported": 501,
        "internal": 500,
    }

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code if code in self.STATUS else "internal"
        self.message = message


def make_server(
    manifest: dict[str, Any],
    routes: dict[str, Handler],
    key: str,
    host: str = "0.0.0.0",  # noqa: S104 - bound inside an internal container network
    port: int = 8080,
) -> HTTPServer:
    """`routes` maps paths such as "/v1/check" to functions taking and returning a dict."""
    expected = key.encode()
    if len(expected) < 16:
        raise ValueError("The connector key is too short")

    class Request(BaseHTTPRequestHandler):
        def log_message(self, *args: object) -> None:
            pass

        def reply(self, status: int, payload: dict[str, Any]) -> None:
            data = json.dumps(payload, ensure_ascii=False, default=str).encode()
            if len(data) > MAX_RESPONSE_BYTES:
                status, data = 500, json.dumps(
                    {"error": {"code": "internal", "message": "Response too large"}}
                ).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def serve(self, method: str) -> None:
            started = time.monotonic()
            status = 500
            try:
                supplied = self.headers.get("Authorization", "")
                if not supplied.startswith("Bearer ") or not hmac.compare_digest(
                    supplied[7:].encode(), expected
                ):
                    raise ConnectorError("unauthorized", "Authentication required")
                path = self.path.split("?")[0]
                if method == "GET" and path == "/v1/describe":
                    result = {"protocol": PROTOCOL, **manifest}
                elif method == "POST" and path in routes:
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 0 <= length <= MAX_REQUEST_BYTES:
                        raise ConnectorError("bad_request", "Request too large")
                    body = json.loads(self.rfile.read(length) or b"{}")
                    if not isinstance(body, dict):
                        raise ConnectorError("bad_request", "Body must be a JSON object")
                    result = routes[path](body)
                else:
                    raise ConnectorError("not_found", "Unknown call")
                status = 200
                self.reply(200, result)
            except ConnectorError as error:
                status = ConnectorError.STATUS[error.code]
                self.reply(status, {"error": {"code": error.code, "message": error.message}})
            except (ValueError, TypeError):
                status = 400
                self.reply(400, {"error": {"code": "bad_request", "message": "Invalid request"}})
            except Exception as error:  # noqa: BLE001 - never echo internals to the caller
                log.warning("unhandled %s", type(error).__name__)
                self.reply(500, {"error": {"code": "internal", "message": "Internal error"}})
            finally:
                log.info(
                    "%s %s -> %s in %.0f ms",
                    method,
                    self.path.split("?")[0],
                    status,
                    (time.monotonic() - started) * 1000,
                )

        def do_GET(self) -> None:
            self.serve("GET")

        def do_POST(self) -> None:
            self.serve("POST")

    return HTTPServer((host, port), Request)
