"""Local password hashing and opaque human sessions; no service-token support."""

import hashlib
import hmac
import secrets
from threading import BoundedSemaphore

# Bound peak memory for this single-process bootstrap (about 128 MiB per hash).
_hash_slots = BoundedSemaphore(1)


def _derive(password: str, salt: bytes) -> bytes:
    with _hash_slots:
        return hashlib.scrypt(
            password.encode(), salt=salt, n=131072, r=8, p=1, maxmem=160 * 1024 * 1024, dklen=32
        )


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    return f"scrypt-v1${salt.hex()}${_derive(password, salt).hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, salt, digest = encoded.split("$")
        if algorithm != "scrypt-v1":
            return False
        return hmac.compare_digest(_derive(password, bytes.fromhex(salt)).hex(), digest)
    except (ValueError, TypeError):
        return False


def digest_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# Same expensive verification path when the username does not exist.
DUMMY_HASH = "scrypt-v1$" + "00" * 16 + "$" + "00" * 32
