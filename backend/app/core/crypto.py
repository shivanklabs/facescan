"""Data encryption under an event's data key (AES-256-GCM).

Sealed format: b"FSD1" | 12-byte nonce | ciphertext+tag. The associated data names the event,
the table and the row, so a ciphertext cannot be moved to another row or event unnoticed.
"""

import secrets
from uuid import UUID

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

MAGIC = b"FSD1"
NONCE_BYTES = 12
DATA_KEY_BYTES = 32


class DecryptionError(Exception):
    """Ciphertext does not open with this key and context (wrong key, moved or tampered)."""


def new_data_key() -> bytes:
    """Purpose: a fresh random 32-byte event data key. Side effects: none."""
    return secrets.token_bytes(DATA_KEY_BYTES)


def aad(event_id: UUID, table: str, row_id: UUID) -> bytes:
    """Purpose: associated data for one row of one table of one event."""
    return b"|".join((b"facescan-data|v1", event_id.bytes, table.encode("ascii"), row_id.bytes))


def seal(data_key: bytes, plaintext: bytes, associated: bytes) -> bytes:
    """Purpose: encrypt bytes. Outputs: sealed bytes. Side effects: none."""
    nonce = secrets.token_bytes(NONCE_BYTES)
    return MAGIC + nonce + AESGCM(data_key).encrypt(nonce, plaintext, associated)


def open_sealed(data_key: bytes, sealed: bytes, associated: bytes) -> bytes:
    """Purpose: decrypt bytes from seal(). Raises DecryptionError on any mismatch."""
    if not sealed.startswith(MAGIC) or len(sealed) < len(MAGIC) + NONCE_BYTES + 16:
        raise DecryptionError("not sealed data")
    nonce = sealed[len(MAGIC) : len(MAGIC) + NONCE_BYTES]
    try:
        return AESGCM(data_key).decrypt(nonce, sealed[len(MAGIC) + NONCE_BYTES :], associated)
    except InvalidTag as exc:
        raise DecryptionError("sealed data does not open with this key") from exc
