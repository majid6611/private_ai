"""Opaque immutable blobs. Never derive a filesystem path from a client filename."""

import hashlib
import os
from pathlib import Path
from uuid import UUID, uuid4


class BlobStore:
    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)

    @classmethod
    def from_environment(cls) -> "BlobStore":
        return cls(Path(os.environ.get("DOCUMENT_STORAGE", "/data/documents")))

    def put(self, content: bytes) -> UUID:
        key = uuid4()
        path = self.root / str(key)
        # Exclusive creation prevents replacement; unpublished orphans are safe on crash.
        with path.open("xb") as output:
            output.write(content)
            output.flush()
            os.fchmod(output.fileno(), 0o400)
            os.fsync(output.fileno())
        self.sync_directory()
        return key

    def sync_directory(self) -> None:
        fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def get(self, key: UUID, expected_hash: str, expected_size: int) -> bytes:
        with (self.root / str(key)).open("rb") as source:
            content = source.read(2097153)
        if len(content) != expected_size or hashlib.sha256(content).hexdigest() != expected_hash:
            raise OSError("Blob integrity check failed")
        return content
