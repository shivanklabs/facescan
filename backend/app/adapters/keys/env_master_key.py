"""KeyManager adapter: master key from FACESCAN_KEYS_MASTER_KEY (dev/free tier only).

Wrapped key format: b"FSK1" | 12-byte nonce | AES-256-GCM(data key), with the event id as
associated data, so a wrapped key copied to another event does not open.
"""

import base64
import binascii
import hashlib
import secrets
from uuid import UUID

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from app.ports.capabilities import Capabilities
from app.ports.key_manager import KeyUnwrapError

MAGIC = b"FSK1"
NONCE_BYTES = 12
KEY_BYTES = 32


def parse_master_key(encoded: str) -> bytes:
    """
    Purpose: decode FACESCAN_KEYS_MASTER_KEY (base64 of 32 bytes).
    Raises ValueError without echoing the value.
    """
    try:
        key = base64.b64decode(encoded.strip(), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("FACESCAN_KEYS_MASTER_KEY is not valid base64") from exc
    if len(key) != KEY_BYTES:
        raise ValueError("FACESCAN_KEYS_MASTER_KEY must decode to 32 bytes")
    return key


class EnvMasterKeyManager:
    """Wraps event data keys with one AES-256 master key held in process memory."""

    def __init__(self, master_key: bytes) -> None:
        """Purpose: hold the master key. Raises ValueError unless it is 32 bytes."""
        if len(master_key) != KEY_BYTES:
            raise ValueError("master key must be 32 bytes")
        self._aead = AESGCM(master_key)
        self._master = master_key
        # Public id: a short hash, so rotation can be tracked without revealing the key.
        self._id = "env:" + hashlib.sha256(b"facescan-master-key-id|" + master_key).hexdigest()[:16]

    @property
    def capabilities(self) -> Capabilities:
        """Static description: free, local."""
        return Capabilities(adapter_name="env_master_key")

    @property
    def master_key_id(self) -> str:
        """Identifier stored with each wrapped key."""
        return self._id

    def wrap(self, event_id: UUID, data_key: bytes) -> bytes:
        """Purpose: wrap a 32-byte data key for one event. Side effects: none."""
        if len(data_key) != KEY_BYTES:
            raise ValueError("data key must be 32 bytes")
        nonce = secrets.token_bytes(NONCE_BYTES)
        return MAGIC + nonce + self._aead.encrypt(nonce, data_key, _aad(event_id))

    def derive_key(self, purpose: str) -> bytes:
        """Purpose: HKDF-SHA256 sub-key of the master key for one purpose. No side effects."""
        if not purpose or len(purpose) > 64:
            raise ValueError("purpose must be 1-64 characters")
        return HKDF(
            algorithm=hashes.SHA256(),
            length=KEY_BYTES,
            salt=None,
            info=b"facescan-derived-key|v1|" + purpose.encode("ascii"),
        ).derive(self._master)

    def unwrap(self, event_id: UUID, wrapped: bytes) -> bytes:
        """Purpose: unwrap a data key. Raises KeyUnwrapError on any mismatch."""
        if not wrapped.startswith(MAGIC) or len(wrapped) < len(MAGIC) + NONCE_BYTES + 16:
            raise KeyUnwrapError("not a wrapped key")
        nonce = wrapped[len(MAGIC) : len(MAGIC) + NONCE_BYTES]
        try:
            key = self._aead.decrypt(nonce, wrapped[len(MAGIC) + NONCE_BYTES :], _aad(event_id))
        except InvalidTag as exc:
            raise KeyUnwrapError("wrapped key does not open for this event") from exc
        return key


def _aad(event_id: UUID) -> bytes:
    """Purpose: associated data binding a wrapped key to its event."""
    return b"facescan-event-key|v1|" + event_id.bytes
