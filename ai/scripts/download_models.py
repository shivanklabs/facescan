"""Download model weights listed in ai/models/models.lock.json and verify SHA-256.

Entries whose URL or checksum is still TODO are skipped with a warning.
"""

import hashlib
import json
import sys
import urllib.request
from pathlib import Path

MODELS_DIR = Path(__file__).resolve().parents[1] / "models"
LOCK = MODELS_DIR / "models.lock.json"
TIMEOUT_SECONDS = 120


def sha256_of(path: Path) -> str:
    """Purpose: hash a file. Inputs: path. Outputs: hex digest. Side effects: reads file."""
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    """
    Purpose: fetch and verify every pinned model.
    Inputs: models.lock.json. Outputs: exit code (1 on any checksum mismatch).
    Side effects: writes weights into ai/models/ (git-ignored); deletes files that fail checks.
    """
    entries = json.loads(LOCK.read_text(encoding="utf-8"))["models"]
    failed = False
    for entry in entries:
        url, expected, name = entry["source_url"], entry["sha256"], entry["file"]
        if "TODO" in url or "TODO" in expected or not url.startswith("https://"):
            print(f"skip {entry['name']}: URL or sha256 not pinned yet (TODO)")
            continue
        target = MODELS_DIR / name
        if not target.exists():
            if url.endswith(".pth"):
                print(f"run ai/scripts/convert_silent_face_onnx.py to generate {entry['name']}")
                failed = True
                continue
            print(f"download {entry['name']}")
            with urllib.request.urlopen(url, timeout=TIMEOUT_SECONDS) as resp:  # noqa: S310
                target.write_bytes(resp.read())
        actual = sha256_of(target)
        if actual != expected:
            print(f"FAIL {entry['name']}: sha256 mismatch, deleting")
            target.unlink()
            failed = True
        else:
            print(f"ok   {entry['name']}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
