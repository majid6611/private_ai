"""One-shot PDF text extraction in a resource-limited child process.

Reads PDF bytes on stdin, writes one JSON object on stdout. Runs with no network, no secrets
and no writable files; hard CPU/memory limits make a hostile PDF kill this child, not the
parser service. Output is untrusted derived data and is validated again by the backend.
"""

import json
import re
import resource
import sys

MAX_PAGES = 300
MAX_CHARS = 2_000_000
MIN_TEXT_CHARS = 40  # below this the file is treated as image-only (needs OCR)

resource.setrlimit(resource.RLIMIT_CPU, (20, 20))
resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))
resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))
resource.setrlimit(resource.RLIMIT_NOFILE, (32, 32))


def clean(text: str) -> str:
    text = text.replace("\x00", "")
    text = re.sub(r"[\x01-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def fail(code: str) -> None:
    sys.stdout.write(json.dumps({"ok": False, "error": code}))
    sys.exit(0)


def main() -> None:
    import io

    import pypdf

    data = sys.stdin.buffer.read()
    try:
        reader = pypdf.PdfReader(io.BytesIO(data), strict=False)
        if reader.is_encrypted and not reader.decrypt(""):
            fail("ENCRYPTED")
        count = len(reader.pages)
        if count > MAX_PAGES:
            fail("TOO_MANY_PAGES")
        pieces: list[str] = []
        spans: list[dict[str, int]] = []
        position = 0
        for number, page in enumerate(reader.pages, 1):
            body = clean(page.extract_text() or "")
            if not body:
                continue
            start = position + (2 if pieces else 0)
            if start + len(body) > MAX_CHARS:
                fail("TOO_LARGE")
            pieces.append(body)
            spans.append({"page": number, "start": start, "end": start + len(body)})
            position = start + len(body)
        text = "\n\n".join(pieces)
    except MemoryError:
        fail("TOO_LARGE")
    except Exception:
        fail("MALFORMED")
    result = {
        "ok": True,
        "text": text,
        "pages": spans,
        "page_count": max(count, 1),
        "image_only": len(text.strip()) < MIN_TEXT_CHARS,
        "extractor": "pypdf-" + pypdf.__version__,
    }
    sys.stdout.write(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
