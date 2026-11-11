"""A tiny stand-in for the embedder service with controllable 'meaning'.

Real vectors are checked by the evaluation against the real model. These tests only need the
*mechanics* (routing, asking, focus, permissions) to be deterministic, so words are mapped to a
few concepts and hashed into a small vector.
"""

import json
import math
import re
import threading
import zlib
from http.server import BaseHTTPRequestHandler, HTTPServer

CONCEPTS = {
    "career": "worked work working companies company employer employers experience career engineer "
    "developer resume cv job jobs employment skills languages programming education".split(),
    "legal": "contract agreement payment deadline supplier client liability termination invoice "
    "delivery fees renewal".split(),
    "weather": "weather rain temperature forecast mars".split(),
}
WORD_TO_CONCEPT = {w: c for c, words in CONCEPTS.items() for w in words}
IGNORED = set(
    "search_query search_document the a an of to in on for and or is are was did do does what which "
    "who whom he she his her him they it its me my send all just name names please tell give list".split()
)
DIMS = 128


def vector(text: str) -> list[float]:
    counts = [0.0] * DIMS
    for word in re.findall(r"[a-z_]+", text.lower()):
        if word in IGNORED:
            continue
        concept = WORD_TO_CONCEPT.get(word)
        key = concept or word
        counts[zlib.crc32(key.encode()) % DIMS] += 3.0 if concept else 1.0
    norm = math.sqrt(sum(x * x for x in counts)) or 1.0
    return [x / norm for x in counts]


class FakeEmbedder:
    def __init__(self) -> None:
        self.requests = 0
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: object) -> None:
                pass

            def do_POST(self) -> None:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
                outer.requests += 1
                data = json.dumps({"embeddings": [vector(t) for t in body["input"]]}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
