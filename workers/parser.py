"""Pure offset extraction; no credentials, filesystem documents, macros or URL fetching."""

import json
from http.server import BaseHTTPRequestHandler, HTTPServer


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args: object) -> None:
        pass

    def do_POST(self) -> None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if self.path != "/extract" or not 0 < length <= 8 * 1024 * 1024:
                self.send_error(400)
                return
            self.connection.settimeout(10)
            body = json.loads(self.rfile.read(length))
            source = body["text"]
            if not isinstance(source, str) or not 0 < len(source.encode("utf-8")) <= 2097152:
                self.send_error(400)
                return
            chunks = []
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
            data = json.dumps({"chunks": chunks}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except (ValueError, KeyError, TypeError, TimeoutError):
            self.send_error(400)


HTTPServer(("0.0.0.0", 8090), Handler).serve_forever()
